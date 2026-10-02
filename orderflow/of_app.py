"""订单流看盘 + 自动交易 服务器。浏览器打开 http://127.0.0.1:8010
启动: python of_app.py
读取同目录的 .env（PROXY_URL、OKX_API_KEY/SECRET/PASSPHRASE、OF_ALLOW_LIVE）和 of_config.json。"""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)   # 黑窗口里别显示无关的英文提示
import asyncio
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402

from of_core import auto_row_size  # noqa: E402
from of_engine import OrderFlowApp, SymbolEngine, TF_MS, VIEW_TFS, btc_regime_calc  # noqa: E402
from of_feed import OkxExtrasHub, OkxHub, REST, get_json, instruments, top_by_volume  # noqa: E402
from of_xfeed import CrossHub  # noqa: E402
import of_v7data  # noqa: E402
import of_notify  # noqa: E402
import httpx  # noqa: E402

CFG_FILE = os.path.join(HERE, "of_config.json")
OKX_BAR = {"1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "1h": "1H"}


def load_env():
    env = {}
    for p in (os.path.join(HERE, ".env"), os.path.join(HERE, "..", ".env")):
        if os.path.exists(p):
            for line in open(p, encoding="utf-8-sig"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    for k in ("PROXY_URL", "OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE", "OF_ALLOW_LIVE", "OF_V7_DATA",
              "OF_PUSHPLUS_TOKEN", "OF_SERVERCHAN_KEY"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


ENV = load_env()
PROXY = ENV.get("PROXY_URL") or None
KEYS = {"key": ENV.get("OKX_API_KEY", ""), "secret": ENV.get("OKX_API_SECRET", ""),
        "passphrase": ENV.get("OKX_API_PASSPHRASE", "")}
cfg = json.load(open(CFG_FILE, encoding="utf-8")) if os.path.exists(CFG_FILE) else {}
core = OrderFlowApp(cfg, PROXY, KEYS, ENV.get("OF_ALLOW_LIVE") == "1")
of_notify.setup(ENV, PROXY)
core.hub = OkxHub(PROXY, os.environ.get("OF_FEED", "auto"))
core.xhub = OkxExtrasHub(PROXY, core.hub)
core.xx = CrossHub(PROXY)            # 币安、Bybit、Coinbase、大背景
core.hub.on_liq = lambda inst, px, sz, side, ts: core.engines[inst].on_liq(px, sz, side, ts) if inst in core.engines else None
app = FastAPI()
INSTS: dict = {}                       # 全部合约信息
REST_SEM = asyncio.Semaphore(3)        # 启动时拉历史K线别太猛（欧易限频）
RUBIK_SEM = asyncio.Semaphore(1)
STARTING: set = set()


def save_cfg():
    """保存设置（自动刹车改了打法勾选也会调用）"""
    keep = {k: core.cfg[k] for k in ("symbols", "tf", "enabled", "auto", "risk_pct", "max_leverage",
                                     "max_positions", "daily_loss_pct", "paper_equity", "top_n", "v7_days", "flush", "squeeze", "margin_mode", "guard_n", "guard_pf", "btc_ma_days")
            if k in core.cfg}
    json.dump(keep, open(CFG_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


async def _candles(c, inst, tf):
    async with REST_SEM:
        data = await get_json(c, "/api/v5/market/candles", instId=inst, bar=OKX_BAR[tf], limit=200)
        await asyncio.sleep(0.15)
    return data


async def start_symbol(inst: str):
    """启动一个币：拉各周期历史K线定格子大小、垫底，补持仓/多空比/V7 历史，然后接上实时行情"""
    if inst in core.engines or inst in STARTING:
        return
    info = INSTS.get(inst)
    if not info:
        core.say(f"{inst} 不是欧易的 USDT 永续合约，跳过")
        return
    STARTING.add(inst)
    try:
        ct, tick = float(info["ctVal"]), float(info["tickSz"])
        tfs = sorted(set(VIEW_TFS + [core.cfg["tf"]]), key=lambda t: TF_MS[t])
        cs, rows = {}, {}
        async with httpx.AsyncClient(proxy=PROXY, timeout=20) as c:
            for tf in tfs:
                raw = await _candles(c, inst, tf)
                cs[tf] = sorted([(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]) * ct)
                                 for r in raw if r[8] == "1"])
                rows[tf] = auto_row_size([h - l for _, _, h, l, _, _ in cs[tf][-100:]], tick)
        eng = SymbolEngine(core, inst, core.cfg["tf"], rows, ct)
        for tf, b in eng.builders.items():
            b.seed(cs.get(tf, []))
        await seed_derivs(eng, inst)
        seed_from_v7(eng, inst)
        eng.category = str(info.get("instCategory") or "1")    # 1=加密币，3=股票合约 等
        core.engines[inst] = eng
        # 补当前这根K线的足迹：先把实时成交存起来，拉完最近的历史成交再按时间顺序喂进去，不会乱序
        buf = []
        core.hub.add(inst, lambda *t: buf.append(t))
        core.xhub.add(inst, eng)
        core.xx.add(inst, eng)
        try:
            tfm = TF_MS[core.cfg["tf"]]
            now = int(time.time() * 1000)
            hist = await asyncio.wait_for(core.hub.history_trades(inst, now - now % tfm, max_pages=10), timeout=25)
            first_live = buf[0][3] if buf else 10 ** 15
            eng.backfilling = True
            n = 0
            for t in hist:
                ts = int(t["ts"])
                if ts < first_live:
                    eng.on_trade(float(t["px"]), float(t["sz"]), t["side"] == "buy", ts)
                    n += 1
        except Exception:  # noqa: BLE001
            pass
        finally:
            eng.backfilling = False
            core.hub.on_trade[inst] = eng.on_trade
            for t in buf:
                eng.on_trade(*t)
        core.say(f"{inst} 已接入（信号周期 {core.cfg['tf']}，格子 {rows[core.cfg['tf']]:g}）")
    except Exception as e:  # noqa: BLE001
        core.say(f"{inst} 启动失败：{e}（检查 .env 里的 PROXY_URL）")
    finally:
        STARTING.discard(inst)


def stop_symbol(inst: str):
    if any(p.sym == inst for p in core.acct.positions):
        return False, "这个币还有持仓，先平仓再删"
    core.hub.remove(inst)
    core.xhub.remove(inst)
    core.xx.remove(inst)
    core.engines.pop(inst, None)
    return True, ""


V7 = {"dir": None, "data": {}}


def seed_from_v7(eng, inst):
    """用 V7 录的数据填历史K线：主动买卖、爆仓、持仓量、资金费率、盘口失衡"""
    rows = V7["data"].get(inst)
    if not rows:
        return
    n = 0
    for tf, bld in eng.builders.items():
        vb = of_v7data.bars(rows, bld.tf_ms)
        for b in bld.bars:
            v = vb.get(b.t)
            if not v:
                continue
            r = int(math.floor(b.c / bld.row))
            b.rows = {r: [v["sell"], v["buy"]]}       # 没有逐价位明细，买卖量放在收盘价那一格（delta、总量是真的）
            b.liq_long, b.liq_short = v["liq_long"], v["liq_short"]
            for k in ("oi", "funding", "obi"):
                if not math.isnan(v[k]):
                    setattr(b, k, v[k])
            n += 1
    if n:
        _replay(eng)
        core.say(f"{inst} 用 V7 数据填了 {n} 根历史K线（爆仓、持仓、盘口、主动买卖）")


def _replay(eng):
    """补好历史以后重新跑一遍识别器，让它记住持仓、多空比的历史"""
    eng.det = type(eng.det)(eng.row, enabled=list(eng.det.enabled))
    for b in eng.builder.bars:
        eng.det.on_bar(b)


def v7_load():
    d = of_v7data.find_dir(ENV.get("OF_V7_DATA"))
    V7["dir"] = d
    if d is not None:
        V7["data"] = of_v7data.load(d, days=int(core.cfg.get("v7_days", 2)))
        core.say(f"读取 V7 录的数据：{d}（{len(V7['data'])} 个币）")
    else:
        core.say("没找到 V7 的录制数据（recorder_data），不影响运行")


async def pick_symbols():
    """配置 symbols="auto"：按欧易 24 小时成交额自动选 top_n 个；否则用配置里的列表。
    有持仓的币一定带上（不然重启后掉出前几名，这笔单到时间不会平仓）"""
    held = [p.sym for p in core.acct.positions]
    out = await _pick_symbols()
    extra = [s for s in held if s not in out and s in INSTS]
    if extra:
        core.say(f"有持仓的币也一起接入：{', '.join(x.split('-')[0] for x in extra)}")
    return out + extra


async def _pick_symbols():
    syms = core.cfg.get("symbols")
    if isinstance(syms, list) and syms and syms != ["auto"]:
        return [s for s in syms if s in INSTS]
    n = int(core.cfg.get("top_n", 20))
    try:
        out = await top_by_volume(PROXY, n)
        core.say(f"按 24 小时成交额自动选了 {len(out)} 个币：{', '.join(x.split('-')[0] for x in out)}")
        return out
    except Exception as e:  # noqa: BLE001
        core.say(f"自动选币失败（{e}），先用 BTC/ETH/SOL")
        return ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]


async def seed_derivs(eng, inst):
    """历史K线补上持仓量、多空比（欧易 5 分钟数据，最近 100 条），让相关打法一启动就能算"""
    try:
        async with RUBIK_SEM, httpx.AsyncClient(proxy=PROXY, timeout=15) as c:
            oi = await get_json(c, "/api/v5/rubik/stat/contracts/open-interest-history", instId=inst, period="5m", limit=100)
            await asyncio.sleep(0.45)
            ls = await get_json(c, "/api/v5/rubik/stat/contracts/long-short-account-ratio-contract", instId=inst, period="5m", limit=100)
            await asyncio.sleep(0.45)
        oi_usd = sorted((int(r[0]), float(r[3])) for r in oi if len(r) > 3)   # 美元持仓价值：算"持仓量变化"用（和回测口径一致）
        oi = sorted((int(r[0]), float(r[2])) for r in oi)                     # 币数量：画图用
        ls = sorted((int(r[0]), float(r[1])) for r in ls)
        eng.ext["oi_hist"] = list(oi_usd)
        for bld in eng.builders.values():
            for b in bld.bars:
                end = b.t + bld.tf_ms
                v = [x for t, x in oi if t <= end]
                if v:
                    b.oi = v[-1]
                v = [x for t, x in ls if t <= end]
                if v:
                    b.ls = v[-1]
        _replay(eng)
    except Exception as e:  # noqa: BLE001
        core.say(f"{inst} 补持仓/多空比历史失败（不影响运行）：{e}")


async def book_sampler():
    while True:
        await asyncio.sleep(2)
        now = int(time.time() * 1000)
        for eng in list(core.engines.values()):
            try:
                eng.sample_book(now)
            except Exception as e:  # noqa: BLE001
                core.say(f"{eng.inst} 盘口采样出错：{e}")


async def heartbeat():
    """黑窗口里每 5 分钟一行运行状态（启动 1 分钟后先打一行）"""
    await asyncio.sleep(60)
    while True:
        try:
            core.heartbeat()
        except Exception as e:  # noqa: BLE001
            core.say(f"状态汇总出错：{e}")
        await asyncio.sleep(300)


REGIME_LAST = {"bull": "x"}


async def _regime_once():
    """读 BTC 日线，算大盘是多头还是空头（做多打法只在多头时开）。读失败返回 False"""
    n = int(core.cfg.get("btc_ma_days", 0) or 0)
    if n <= 0:
        core.btc_bull, core.btc_info = None, ""
        REGIME_LAST["bull"] = "x"
        return True
    try:
        async with httpx.AsyncClient(proxy=PROXY, timeout=15) as c:
            rows = await get_json(c, "/api/v5/market/history-candles", instId="BTC-USDT-SWAP", bar="1Dutc", limit=100)
            while len(rows) < n + 2:                       # 一次最多 100 根，往前翻页
                more = await get_json(c, "/api/v5/market/history-candles", instId="BTC-USDT-SWAP", bar="1Dutc",
                                      limit=100, after=min(int(r[0]) for r in rows))
                if not more:
                    break
                rows += more
                await asyncio.sleep(0.3)
    except Exception as e:  # noqa: BLE001
        core.say(f"读 BTC 日线失败（{e}），1 分钟后重试")
        return False
    bull, px, ma = btc_regime_calc(rows, n)
    core.btc_bull = bull
    core.btc_info = ("大盘：读不到足够的 BTC 日线，做多打法先不开" if bull is None else
                     f"大盘{'多头' if bull else '空头'}（BTC 昨收 {px:.0f}，{n} 天均线 {ma:.0f}）{'' if bull else '，做多打法暂停'}")
    if bull != REGIME_LAST["bull"]:
        core.say(core.btc_info)
        REGIME_LAST["bull"] = bull
    return True


async def btc_regime():
    """每小时更新一次大盘判断"""
    while True:
        ok = await _regime_once()
        await asyncio.sleep(3600 if ok else 60)


async def live_refresher():
    """实盘：每 5 秒核对一次交易所持仓（止盈止损触发后记真实盈亏），每 30 秒读一次权益"""
    while True:
        await asyncio.sleep(5)
        await core.reconcile_live()


core.save_cfg_cb = save_cfg


@app.on_event("startup")
async def _startup():
    global INSTS
    asyncio.create_task(core.hub.run())
    asyncio.create_task(core.xhub.run())
    asyncio.create_task(core.xx.run())
    asyncio.create_task(heartbeat())
    asyncio.create_task(btc_regime())
    lp = [p.sym.split("-")[0] for p in core.acct.positions if p.live]
    if lp:
        core.say(f"提醒：上次还有 {len(lp)} 笔实盘持仓（{', '.join(lp)}），请在网页上重新切到实盘，程序才能按时帮你平仓")
    asyncio.create_task(book_sampler())
    asyncio.create_task(live_refresher())
    # 接入币放到后台做：网页马上就能打开，币接好一个显示一个
    asyncio.create_task(_connect_all())


async def _connect_all():
    global INSTS
    for k in range(10):
        try:
            INSTS = await instruments(PROXY)
            break
        except Exception as e:  # noqa: BLE001
            core.say(f"读取合约列表失败（{e}），5 秒后重试；检查 .env 里的 PROXY_URL")
            await asyncio.sleep(5)
    await asyncio.to_thread(v7_load)
    syms = await pick_symbols()
    sem = asyncio.Semaphore(4)         # 同时接 4 个（拉历史的请求另外有限频保护）

    async def one(inst):
        async with sem:
            try:
                await start_symbol(inst)
            except Exception as e:  # noqa: BLE001
                core.say(f"{inst} 接入失败：{e}")
    await asyncio.gather(*(one(i) for i in syms))
    core.say(f"全部接入完成：{len(core.engines)} 个币")


@app.get("/")
async def index():
    return FileResponse(os.path.join(HERE, "static", "of.html"))


@app.get("/api/ready")
async def ready():
    return {"ready": True}


@app.get("/api/bt")
async def bt():
    p = os.path.join(HERE, "of_backtest_result.json")
    return JSONResponse(json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {})


@app.post("/api/cfg")
async def set_cfg(body: dict):
    for k in ("enabled", "auto", "risk_pct", "max_leverage", "max_positions", "daily_loss_pct"):
        if k in body:
            core.cfg[k] = body[k]
    if "btc_ma_days" in body:
        try:
            n = int(body["btc_ma_days"])
            if n in (0, 100, 150, 200) and n != int(core.cfg.get("btc_ma_days", 0) or 0):
                core.cfg["btc_ma_days"] = n
                core.btc_bull, core.btc_info = None, ("" if n == 0 else "大盘：正在重新读 BTC 日线…")
                asyncio.create_task(_regime_once())
        except (TypeError, ValueError):
            pass
    if body.get("margin_mode") in ("isolated", "cross"):
        core.cfg["margin_mode"] = body["margin_mode"]
    from of_engine import FLUSH, SQUEEZE
    for key, base in (("flush", FLUSH), ("squeeze", SQUEEZE)):     # 两个组合打法的参数，只收认识的数字
        if isinstance(body.get(key), dict):
            cur = dict(base, **(core.cfg.get(key) or {}))
            for k, v in body[key].items():
                if k in base:
                    try:
                        cur[k] = float(v)
                    except (TypeError, ValueError):
                        pass
            core.cfg[key] = cur
    core.risk.cfg = core.cfg
    save_cfg()
    core.say(f"设置已更新：自动交易={'开' if core.cfg['auto'] else '关'}，"
             f"保证金={'全仓' if core.cfg.get('margin_mode') == 'cross' else '逐仓'}，打法={core.cfg['enabled']}")
    return {"ok": True}


@app.post("/api/live")
async def live(body: dict):
    if body.get("on"):
        ok, msg = core.enable_live(body.get("phrase", ""))
    else:
        core.disable_live()
        ok, msg = True, "已切回模拟盘"
    return JSONResponse({"ok": ok, "msg": msg})


@app.get("/api/all_insts")
async def all_insts():
    """欧易所有 USDT 永续（网页下拉框用：没在扫描的币选了就自动接入）"""
    return sorted(i for i in INSTS if i.endswith("-USDT-SWAP"))


@app.post("/api/coins")
async def coins(body: dict):
    """加币 / 删币 / 改自动选币数量"""
    msg = []
    if body.get("add"):
        inst = body["add"].strip().upper()
        if "-" not in inst:
            inst += "-USDT-SWAP"
        if inst not in INSTS:
            return JSONResponse({"ok": False, "msg": f"欧易没有 {inst}"})
        asyncio.create_task(start_symbol(inst))
        if isinstance(core.cfg.get("symbols"), list):
            core.cfg["symbols"] = sorted(set(core.cfg["symbols"]) | {inst})
        msg.append(f"正在接入 {inst}")
    if body.get("remove"):
        ok, why = stop_symbol(body["remove"])
        if not ok:
            return JSONResponse({"ok": False, "msg": why})
        if isinstance(core.cfg.get("symbols"), list):
            core.cfg["symbols"] = [x for x in core.cfg["symbols"] if x != body["remove"]]
        msg.append(f"已移除 {body['remove']}")
    if body.get("top_n"):
        core.cfg["top_n"] = max(1, min(150, int(body["top_n"])))
        core.cfg["symbols"] = "auto"
        want = await pick_symbols()
        for inst in want:
            asyncio.create_task(start_symbol(inst))
        msg.append(f"自动选 {core.cfg['top_n']} 个币，新币正在接入")
    save_cfg()
    return {"ok": True, "msg": "；".join(msg)}


@app.post("/api/signal_tf")
async def signal_tf(body: dict):
    """换自动交易用的信号周期：马上生效，不用重启，持仓不受影响"""
    tf = body.get("tf")
    if tf not in VIEW_TFS:
        return JSONResponse({"ok": False, "msg": "周期不对"})
    if tf == core.cfg["tf"]:
        return {"ok": True, "msg": "没变"}
    core.cfg["tf"] = tf
    save_cfg()
    for eng in list(core.engines.values()):
        eng.set_signal_tf(tf)
    core.say(f"信号周期改成 {tf}，{len(core.engines)} 个币已经切换，已存进 of_config.json")
    return {"ok": True, "msg": f"信号周期改成 {tf}，已经生效（已保存，重启后也是 {tf}）"}


@app.post("/api/close_all")
async def close_all():
    core.close_all()
    return {"ok": True}


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    inst, tf = "BTC-USDT-SWAP", None
    try:
        while True:
            try:
                msg = json.loads(await asyncio.wait_for(sock.receive_text(), timeout=0.5))
                inst = msg.get("inst", inst)
                tf = msg.get("tf", tf)
            except asyncio.TimeoutError:
                pass
            if inst not in core.engines and core.engines:
                inst = next(iter(core.engines))
            core.hub.focus = inst                    # 正在看的币：拉全量盘口画热力图
            st = core.state(inst, tf)
            txt = json.dumps(st, ensure_ascii=False, default=str)
            # JSON 不认 NaN/Infinity，换成 null
            txt = txt.replace("NaN", "null").replace("-Infinity", "null").replace("Infinity", "null")
            await sock.send_text(txt)
    except (WebSocketDisconnect, RuntimeError):
        return


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("OF_PORT", "8010"))
    print(f"订单流看盘： http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
