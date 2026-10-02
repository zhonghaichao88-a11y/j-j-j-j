"""订单流看盘 + 自动交易 服务器。浏览器打开 http://127.0.0.1:8010
启动: python of_app.py
读取同目录的 .env（PROXY_URL、OKX_API_KEY/SECRET/PASSPHRASE、OF_ALLOW_LIVE）和 of_config.json。"""
from __future__ import annotations

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
from of_engine import OrderFlowApp, SymbolEngine, TF_MS  # noqa: E402
from of_feed import OkxExtras, OkxFeed, REST  # noqa: E402
import of_v7data  # noqa: E402
import httpx  # noqa: E402

CFG_FILE = os.path.join(HERE, "of_config.json")
BACKFILL_BARS = int(os.environ.get("OF_BACKFILL_BARS", "2"))   # 启动时补最近几根足迹（太多会让启动很慢）


def load_env():
    env = {}
    for p in (os.path.join(HERE, ".env"), os.path.join(HERE, "..", ".env")):
        if os.path.exists(p):
            for line in open(p, encoding="utf-8-sig"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    for k in ("PROXY_URL", "OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE", "OF_ALLOW_LIVE", "OF_V7_DATA"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


ENV = load_env()
PROXY = ENV.get("PROXY_URL") or None
KEYS = {"key": ENV.get("OKX_API_KEY", ""), "secret": ENV.get("OKX_API_SECRET", ""),
        "passphrase": ENV.get("OKX_API_PASSPHRASE", "")}
cfg = json.load(open(CFG_FILE, encoding="utf-8")) if os.path.exists(CFG_FILE) else {}
core = OrderFlowApp(cfg, PROXY, KEYS, ENV.get("OF_ALLOW_LIVE") == "1")
app = FastAPI()


def save_cfg():
    keep = {k: core.cfg[k] for k in ("symbols", "tf", "enabled", "auto", "risk_pct", "max_leverage",
                                     "max_positions", "daily_loss_pct", "paper_equity", "top_n", "v7_days")
            if k in core.cfg}
    json.dump(keep, open(CFG_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


async def start_symbol(inst: str):
    feed = OkxFeed(inst, on_trade=lambda *a: None, proxy=PROXY, mode=os.environ.get("OF_FEED", "auto"))
    core.feeds[inst] = feed
    try:
        info = await feed.instrument()
        ct, tick = float(info["ctVal"]), float(info["tickSz"])
        raw = await feed.candles(core.cfg["tf"], 100)
    except Exception as e:  # noqa: BLE001
        feed.status = f"连不上欧易：{e}（检查 .env 里的 PROXY_URL）"
        core.say(feed.status)
        return
    cs = sorted([(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]) * ct)
                 for r in raw if r[8] == "1"])
    row = auto_row_size([h - l for _, _, h, l, _, _ in cs[-50:]], tick)
    eng = SymbolEngine(core, inst, core.cfg["tf"], row, ct)
    tf_ms = TF_MS[core.cfg["tf"]]
    now = int(time.time() * 1000)
    since = now - now % tf_ms - BACKFILL_BARS * tf_ms
    seeded = [c for c in cs if c[0] < since]
    eng.builder.seed(seeded)
    await seed_derivs(eng, inst)
    seed_from_v7(eng, inst)
    core.engines[inst] = eng
    ex = OkxExtras(inst, PROXY, eng.on_liq, eng.on_oi, eng.on_funding, eng.on_ratio)
    core.extras[inst] = ex
    asyncio.create_task(ex.run())
    core.say(f"{inst} 启动：{core.cfg['tf']} 足迹，每格 {row:g}，正在补最近 {BACKFILL_BARS} 根足迹…")
    try:
        feed.status = "正在补最近的成交（最多等 40 秒）…"
        try:
            hist = await asyncio.wait_for(feed.backfill(since, max_pages=60), timeout=40)
        except asyncio.TimeoutError:
            hist = []
            core.say(f"{inst} 补历史超时，跳过（不影响实时）")
        if hist and int(hist[0]["ts"]) > since + 60_000:
            # 成交太多没拉全：从第一根完整的K线开始画，避免半根K线的足迹不准
            f0 = int(hist[0]["ts"])
            cut = f0 - f0 % tf_ms + tf_ms
            hist = [t for t in hist if int(t["ts"]) >= cut]
        eng.backfilling = True                   # 补历史时只画图、不下单
        for t in hist:
            eng.on_trade(float(t["px"]), float(t["sz"]), t["side"] == "buy", int(t["ts"]))
        core.say(f"{inst} 补了 {len(hist)} 笔历史成交")
    except Exception as e:  # noqa: BLE001
        core.say(f"{inst} 补历史失败（不影响实时）：{e}")
    finally:
        eng.backfilling = False
    feed.on_trade = eng.on_trade
    await feed.run()


V7 = {"dir": None, "data": {}}


def seed_from_v7(eng, inst):
    """用 V7 录的数据填历史K线：主动买卖、爆仓、持仓量、资金费率、盘口失衡"""
    rows = V7["data"].get(inst)
    if not rows:
        return
    vb = of_v7data.bars(rows, eng.builder.tf_ms)
    n = 0
    for b in eng.builder.bars:
        v = vb.get(b.t)
        if not v:
            continue
        r = int(math.floor(b.c / eng.row))
        b.rows = {r: [v["sell"], v["buy"]]}           # 没有逐价位明细，买卖量放在收盘价那一格（delta、总量是真的）
        b.liq_long, b.liq_short = v["liq_long"], v["liq_short"]
        for k in ("oi", "funding", "obi"):
            if not math.isnan(v[k]):
                setattr(b, k, v[k])
        n += 1
    if n:
        eng.det = type(eng.det)(eng.row, enabled=list(eng.det.enabled))
        for b in eng.builder.bars:
            eng.det.on_bar(b)
        core.say(f"{inst} 用 V7 数据填了 {n} 根历史K线（爆仓、持仓、盘口、主动买卖）")


def pick_symbols():
    """启动时扫描 V7 数据：自动挑最活跃的币；没有 V7 数据就用配置里的币"""
    d = of_v7data.find_dir(ENV.get("OF_V7_DATA"))
    V7["dir"] = d
    if d is None:
        core.say("没找到 V7 的录制数据（recorder_data），按配置里的币运行")
        cfg_syms = core.cfg["symbols"] if isinstance(core.cfg["symbols"], list) else []
        return [s for s in cfg_syms if s != "auto"] or ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]
    V7["data"] = of_v7data.load(d, days=int(core.cfg.get("v7_days", 2)))
    if core.cfg.get("symbols") in ("auto", ["auto"]):
        syms = of_v7data.top_symbols(V7["data"], int(core.cfg.get("top_n", 5)))
        core.say(f"读取 V7 数据：{d}，按成交额自动选币：{', '.join(syms)}")
    else:
        syms = list(core.cfg["symbols"])
        core.say(f"读取 V7 数据：{d}")
    keep = set(syms)
    V7["data"] = {k: v for k, v in V7["data"].items() if k in keep}
    return syms or ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]


async def seed_derivs(eng, inst):
    """历史K线补上持仓量、多空比（欧易 5 分钟数据，最近 100 条），让相关打法一启动就能算"""
    try:
        async with httpx.AsyncClient(proxy=PROXY, timeout=15) as c:
            oi = (await c.get(f"{REST}/api/v5/rubik/stat/contracts/open-interest-history",
                              params={"instId": inst, "period": "5m", "limit": 100})).json().get("data", [])
            ls = (await c.get(f"{REST}/api/v5/rubik/stat/contracts/long-short-account-ratio-contract",
                              params={"instId": inst, "period": "5m", "limit": 100})).json().get("data", [])
        oi = sorted((int(r[0]), float(r[2])) for r in oi)
        ls = sorted((int(r[0]), float(r[1])) for r in ls)
        tf = eng.builder.tf_ms
        for b in eng.builder.bars:
            end = b.t + tf
            v = [x for t, x in oi if t <= end]
            if v:
                b.oi = v[-1]
            v = [x for t, x in ls if t <= end]
            if v:
                b.ls = v[-1]
        # 用补好的数据重新跑一遍识别器，让它记住持仓和多空比的历史
        eng.det = type(eng.det)(eng.row, enabled=list(eng.det.enabled))
        for b in eng.builder.bars:
            eng.det.on_bar(b)
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


@app.on_event("startup")
async def _startup():
    syms = await asyncio.to_thread(pick_symbols)
    core.cfg["symbols_live"] = syms
    for inst in syms:
        asyncio.create_task(start_symbol(inst))
    asyncio.create_task(book_sampler())


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
    core.risk.cfg = core.cfg
    save_cfg()
    core.say(f"设置已更新：自动交易={'开' if core.cfg['auto'] else '关'}，打法={core.cfg['enabled']}")
    return {"ok": True}


@app.post("/api/live")
async def live(body: dict):
    if body.get("on"):
        ok, msg = core.enable_live(body.get("phrase", ""))
    else:
        core.disable_live()
        ok, msg = True, "已切回模拟盘"
    return JSONResponse({"ok": ok, "msg": msg})


@app.post("/api/close_all")
async def close_all():
    core.close_all()
    return {"ok": True}


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    inst = (core.cfg.get("symbols_live") or ["BTC-USDT-SWAP"])[0]
    try:
        while True:
            try:
                msg = await asyncio.wait_for(sock.receive_text(), timeout=0.5)
                inst = json.loads(msg).get("inst", inst)
            except asyncio.TimeoutError:
                pass
            st = core.state(inst)
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
