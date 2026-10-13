"""策略机网页服务：http://127.0.0.1:8020
读取同目录的 .env（PROXY_URL、OKX_API_KEY/SECRET/PASSPHRASE、SB_ALLOW_LIVE）和 sb_config.json（没有就用 sb_config.default.json）。"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from contextlib import asynccontextmanager  # noqa: E402

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402

import sb_bot  # noqa: E402
import sb_notify  # noqa: E402

CFG_FILE = os.path.join(HERE, "sb_config.json")
CFG_DEFAULT = os.path.join(HERE, "sb_config.default.json")
BT_FILE = os.path.join(HERE, "sb_backtest.json")
PORT = int(os.environ.get("SB_PORT", "8020"))

class _Fmt(logging.Formatter):
    """黑窗口里看得懂：时间 + 中文，警告/出错前面加标记，不打 INFO/WARNING 这些英文"""
    TAG = {logging.WARNING: "【注意】", logging.ERROR: "【出错】", logging.CRITICAL: "【出错】"}

    def format(self, r):
        return f"{self.formatTime(r, '%m-%d %H:%M:%S')} {self.TAG.get(r.levelno, '')}{r.getMessage()}" + (
            "\n" + self.formatException(r.exc_info) if r.exc_info else "")


_handlers = [logging.FileHandler(os.path.join(HERE, "sb_log.txt"), encoding="utf-8"), logging.StreamHandler()]
for _h in _handlers:
    _h.setFormatter(_Fmt())
logging.basicConfig(level=logging.INFO, handlers=_handlers)
for _n in ("httpx", "numexpr", "numexpr.utils"):                 # 每次请求一行、"NumExpr defaulting to 4 threads" 这类太吵
    logging.getLogger(_n).setLevel(logging.WARNING)


def load_env():
    env = {}
    p = os.path.join(HERE, ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8-sig"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    for k in ("PROXY_URL", "OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE", "SB_ALLOW_LIVE",
              "OF_PUSHPLUS_TOKEN", "OF_SERVERCHAN_KEY"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def load_cfg():
    """sb_config.json 是你自己的设置（升级不会覆盖）；第一次运行从默认设置复制一份。默认设置里新加的策略会自动补进来（不勾选）"""
    if not os.path.exists(CFG_FILE):
        shutil.copy(CFG_DEFAULT, CFG_FILE)
    cfg = json.load(open(CFG_FILE, encoding="utf-8"))
    d = json.load(open(CFG_DEFAULT, encoding="utf-8"))
    for k, v in d.items():
        cfg.setdefault(k, v)
    for name, sc in d.get("strategies", {}).items():
        if name not in cfg["strategies"]:
            cfg["strategies"][name] = dict(sc, enabled=False)
        else:
            for k in ("title", "note"):
                cfg["strategies"][name][k] = sc.get(k, "")
    return cfg


ENV = load_env()
sb_notify.setup(ENV, ENV.get("PROXY_URL") or None)
bot = sb_bot.Bot(load_cfg(), ENV, CFG_FILE)


@asynccontextmanager
async def lifespan(_app):
    task = asyncio.get_running_loop().create_task(bot.start())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"), headers={"Cache-Control": "no-store"})


@app.get("/api/ready")
def ready():
    return {"ready": True}            # 网页能打开就算就绪（行情在后台加载，网页上显示进度）


@app.get("/api/status")
def status():
    return JSONResponse(bot.snapshot())


@app.get("/api/backtest")
def backtest():
    return json.load(open(BT_FILE, encoding="utf-8")) if os.path.exists(BT_FILE) else {}


@app.get("/api/trades")
def trades(limit: int = 200):
    rows = bot.closed_since(0)
    return {"rows": rows[-limit:][::-1], "stats": bot.stats(rows)}


def _num(v, lo, hi, name):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{name} 要填数字")
    if not lo <= x <= hi:
        raise HTTPException(400, f"{name} 要在 {lo} ~ {hi} 之间")
    return x


@app.post("/api/strategy")
async def set_strategy(body: dict):
    name = body.get("name")
    if name not in bot.cfg["strategies"]:
        raise HTTPException(404, "没有这个策略")
    sc = bot.cfg["strategies"][name]
    if "enabled" in body:
        if body["enabled"] and name not in bot.strats:
            raise HTTPException(400, f"这个策略加载失败，不能勾选：{bot.load_errors.get(name)}")
        sc["enabled"] = bool(body["enabled"])
    if "max_open" in body:
        sc["max_open"] = int(_num(body["max_open"], 1, 50, "最多同时几单"))
    if "capital" in body:
        sc["capital"] = _num(body["capital"], 1, 10_000_000, "本金")
    bot.save_cfg()
    if sc.get("enabled"):
        await asyncio.to_thread(bot._analyze_all)
    return {"ok": True}


@app.post("/api/settings")
async def set_settings(body: dict):
    if "daily_loss_limit_pct" in body:
        bot.cfg["daily_loss_limit_pct"] = _num(body["daily_loss_limit_pct"], 0, 100, "当天亏损上限")
    before = (bot.cfg.get("coins", "backtest"), bot.cfg.get("top_n", 150))
    if "coins" in body:
        if body["coins"] not in ("backtest", "all", "top"):
            raise HTTPException(400, "选币方式不对")
        bot.cfg["coins"] = body["coins"]
    if "top_n" in body:
        bot.cfg["top_n"] = int(_num(body["top_n"], 10, 500, "成交额前几个"))
    bot.save_cfg()
    after = (bot.cfg.get("coins", "backtest"), bot.cfg.get("top_n", 150))
    if bot.ready and (after[0] != before[0] or (after[0] == "top" and after[1] != before[1])):
        bot.coin_task = asyncio.create_task(bot.apply_coins())        # 不用重启：后台马上换币
        return {"ok": True, "note": "已保存，正在切换选币：新币要先下载历史K线（几十个币约 1 分钟），顶上的币数会变；下一根K线起照常开单"}
    return {"ok": True, "note": "已保存"}


@app.post("/api/pause")
def pause(body: dict):
    bot.paused = bool(body.get("paused"))
    bot.save_state()
    bot.say("暂停开新单（已有的仓位照常按规则平）" if bot.paused else "恢复开新单")
    return {"ok": True}


@app.post("/api/close")
async def close(body: dict):
    coin = body.get("coin")
    async with bot.lock:
        for t in list(bot.trades):
            if sb_bot.coin_of(t.pair) == coin:
                px = bot.prices.get(t.pair) or t.open_rate
                await bot.close(t, px, "manual")
                return {"ok": True}
    raise HTTPException(404, "没有这个币的持仓")


@app.post("/api/mode")
async def mode(body: dict):
    try:
        async with bot.lock:
            r = await bot.set_mode(body.get("mode"), body.get("confirm", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"切换失败：{e}")
    return {"ok": True, "msg": r}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
