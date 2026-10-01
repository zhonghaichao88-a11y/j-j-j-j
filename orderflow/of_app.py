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
from of_feed import OkxFeed  # noqa: E402

CFG_FILE = os.path.join(HERE, "of_config.json")
BACKFILL_BARS = int(os.environ.get("OF_BACKFILL_BARS", "8"))


def load_env():
    env = {}
    for p in (os.path.join(HERE, ".env"), os.path.join(HERE, "..", ".env")):
        if os.path.exists(p):
            for line in open(p, encoding="utf-8-sig"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    for k in ("PROXY_URL", "OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE", "OF_ALLOW_LIVE"):
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
                                     "max_positions", "daily_loss_pct", "paper_equity")}
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
    eng.builder.seed([c for c in cs if c[0] < since])
    core.engines[inst] = eng
    core.say(f"{inst} 启动：{core.cfg['tf']} 足迹，每格 {row:g}，正在补最近 {BACKFILL_BARS} 根足迹…")
    try:
        hist = await feed.backfill(since, max_pages=600)
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


@app.on_event("startup")
async def _startup():
    for inst in core.cfg["symbols"]:
        asyncio.create_task(start_symbol(inst))


@app.get("/")
async def index():
    return FileResponse(os.path.join(HERE, "static", "of.html"))


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
    inst = core.cfg["symbols"][0]
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
