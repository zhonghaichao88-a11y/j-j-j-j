"""V7 路由桥：给自制 TradingView 终端提供数据与控制接口。

- 页面：GET /tv
- 数据：/tv/api/klines（K线，服务端按周期短缓存）、/tv/api/gainers（涨幅榜）、
        /tv/api/state（运行状态/持仓/信号，一次拿全）
- 控制：/tv/api/start、/stop、/emergency、/reset、/params
所有下单/平仓仍走 alpha_engine 现有全套风控管线；本模块不实现任何交易逻辑。
"""
from __future__ import annotations

import os
import math
from functools import wraps
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

import alpha_fast_v7
import tv_universe
from okx_client import okx_client

router = APIRouter(prefix="/tv", tags=["V7 自制终端"])
_BASE = Path(__file__).parent

def _control(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        import alpha_engine
        with alpha_engine.CONTROL_LOCK:
            return fn(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------- K线

_BAR_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000,
           "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
_KC: Dict[tuple, tuple] = {}


def _ensure_okx():
    """页面在引擎启动前也要能看行情：未连接则自动连接一次。"""
    if not okx_client.is_connected:
        okx_client.connect()


def _cache_ttl(tf: str) -> float:
    return min(max(_BAR_MS[tf] / 1000.0 / 15.0, 4.0), 10.0)


@router.get("/api/klines")
def tv_klines(symbol: str, tf: str = "5m", limit: int = 500):
    tf = str(tf).lower()
    if tf not in _BAR_MS:
        raise HTTPException(status_code=400, detail="周期无效")
    symbol = str(symbol).strip().upper()
    limit = max(50, min(int(limit or 500), 1000))
    key = (symbol, tf, limit)
    now = time.time()
    hit = _KC.get(key)
    if hit and now - hit[1] < _cache_ttl(tf):
        return {"success": True, "symbol": symbol, "tf": tf, "klines": hit[0]}
    try:
        _ensure_okx()
        rows = okx_client.get_ohlcv(symbol=symbol, timeframe=tf, limit=limit) or []
    except Exception as exc:
        if hit:  # 网络抖动时回退缓存，不让页面报错
            return {"success": True, "symbol": symbol, "tf": tf,
                    "klines": hit[0], "stale": True}
        raise HTTPException(status_code=502, detail=f"读取K线失败: {exc}")
    klines = [{"timestamp": int(r[0]), "open": float(r[1]), "high": float(r[2]),
               "low": float(r[3]), "close": float(r[4]),
               "volume": float(r[5] or 0)} for r in rows]
    _KC[key] = (klines, now)
    return {"success": True, "symbol": symbol, "tf": tf, "klines": klines}


# ---------------------------------------------------------------- 涨幅榜

@router.get("/api/gainers")
def tv_gainers():
    snap = tv_universe.snapshot()
    if not snap.get("last"):  # 首次打开页面：自动连一次并立即刷新榜单
        try:
            _ensure_okx()
            tv_universe.top_gainers([], force=True)
        except Exception:
            pass
    return {"success": True, **tv_universe.snapshot()}


class UniverseConfigRequest(BaseModel):
    basis: Optional[str] = None
    top: Optional[int] = None
    min_turnover_usdt: Optional[float] = None


@router.post("/api/universe/config")
def tv_universe_config(req: UniverseConfigRequest):
    opts = {k: v for k, v in req.dict().items() if v is not None}
    try:
        tv_universe.configure(opts)
        tv_universe.top_gainers([], force=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"success": True, **tv_universe.snapshot()}


# ---------------------------------------------------------------- 状态

def _ccxt_to_internal(ccxt_sym: str) -> str:
    # BTC/USDT:USDT -> BTC-USDT-SWAP
    try:
        base, quote = ccxt_sym.split("/")
        quote = quote.split(":")[0]
        return f"{base}-{quote}-SWAP"
    except Exception:
        return ccxt_sym


@router.get("/api/state")
def tv_state():
    try:
        _ensure_okx()
    except Exception:
        pass
    try:
        import alpha_engine
        st = alpha_engine.status()
    except Exception as exc:
        return {"success": False, "error": f"状态读取失败: {exc}"}

    mode = st.get("mode")
    positions: List[Dict[str, Any]] = []

    if mode == "live":
        real_map = {}
        try:
            for rp in okx_client.get_positions():
                real_map[_ccxt_to_internal(rp.get("symbol", ""))] = rp
        except Exception as exc:
            st["live_error"] = f"持仓读取失败: {exc}"
        for sym, lp in (st.get("positions") or {}).items():
            rp = real_map.get(sym) or {}
            positions.append({
                "symbol": sym,
                "side": lp.get("side"),
                "entry": float(lp.get("entry") or rp.get("entry_price") or 0),
                "last": float(rp.get("mark_price") or lp.get("entry") or 0),
                "upnl": float(rp.get("unrealized_pnl") or 0),
                "tp": float(lp.get("tp") or 0),
                "sl": float(lp.get("sl") or 0),
                "opened_at": lp.get("opened_at"),
                "leverage": rp.get("leverage"),
            })
    else:
        for sym, p in (st.get("positions") or {}).items():
            last = float(p.get("entry") or 0)
            try:
                tk = okx_client.get_ticker(sym) or {}
                last = float(tk.get("last") or last)
            except Exception:
                pass
            side = p.get("side")
            d = 1 if side == "long" else -1
            entry = float(p.get("entry") or 0)
            notional = float(p.get("notional") or 0)
            upnl = notional * d * (last / entry - 1) if entry > 0 else 0.0
            positions.append({
                "symbol": sym, "side": side, "entry": entry,
                "last": last, "upnl": upnl,
                "tp": float(p.get("tp") or 0),
                "sl": float(p.get("sl") or 0),
                "opened_at": p.get("opened_at"),
            })

    last_signals = {s: {"time": v.get("time"), "signal": v.get("signal"),
                        "tier": v.get("tier"), "tp": v.get("tp"),
                        "sl": v.get("sl")}
                    for s, v in (st.get("last") or {}).items()}
    return {
        "success": True,
        "running": st.get("running"),
        "mode": mode,
        "strategy_mode": st.get("strategy_mode"),
        "balance": st.get("balance"),
        "equity": st.get("equity"),
        "live_start_equity": st.get("live_start_equity"),
        "live_error": st.get("live_error"),
        "risk_block": st.get("risk_block"),
        "consecutive_losses": st.get("consecutive_losses"),
        "last_cycle": st.get("last_cycle"),
        "fast_engine_version": st.get("fast_engine_version"),
        "base_tf": alpha_fast_v7.get_runtime_params().get("base_tf", "5m"),
        "positions": positions,
        "last_signals": last_signals,
        "history": (st.get("history") or [])[-60:],
        "gates": st.get("gate_switches"),
        "universe": tv_universe.snapshot(),
    }


# ---------------------------------------------------------------- 控制

class TVStartRequest(BaseModel):
    live: bool = False
    leverage: int = 3
    max_positions: int = 5
    risk_pct: float = 0.02
    top: int = 20
    confirm: Optional[str] = None
    params: Optional[Dict[str, Any]] = None


@router.post("/api/start")
@_control
def tv_start(req: TVStartRequest):
    import alpha_engine
    if req.leverage < 1 or req.leverage > 50:
        raise HTTPException(status_code=400, detail="杠杆须在 1~50 之间")
    if req.max_positions < 1 or req.max_positions > 10:
        raise HTTPException(status_code=400, detail="最大持仓须在 1~10 之间")
    if not math.isfinite(req.risk_pct) or req.risk_pct <= 0 or req.risk_pct > 0.10:
        raise HTTPException(status_code=400, detail="单笔风险须在 0~10% 之间")
    if req.live:
        if req.confirm != "V7-LIVE":
            raise HTTPException(status_code=400,
                                detail="实盘启动必须带确认口令 V7-LIVE")
        if os.getenv("ALPHA_LIVE_ALLOWED", "0") != "1":
            raise HTTPException(status_code=403,
                                detail="未开启实盘总许可（环境变量 ALPHA_LIVE_ALLOWED=1）")
    with alpha_engine.LOCK:
        if alpha_engine.STATE.get("running"):
            raise HTTPException(status_code=409, detail="交易引擎已经运行，请先停止再切换模式或重新启动")
        worker = getattr(alpha_engine, "LOOP_THREAD", None)
        if worker is not None and worker.is_alive():
            raise HTTPException(status_code=409, detail="上一轮仍在收尾，请稍后再启动")
        target_mode = "live" if req.live else "paper"
        if alpha_engine.STATE.get("positions") and alpha_engine.STATE.get("mode") != target_mode:
            raise HTTPException(status_code=409, detail="仍有持仓或待对账记录，不能切换实盘/模拟模式")
    # 1) 切到 V7 引擎；2) 配涨幅榜；3) 写策略参数；4) 启动
    try:
        params = alpha_fast_v7.validate_params(req.params)
        if not 1 <= req.top <= 100:
            raise ValueError("选币数量须在 1~100 之间")
        alpha_engine.set_fast_engine("v7")
        tv_universe.configure({"top": int(req.top)})
        alpha_fast_v7.set_runtime_params(params)
        settings = {
            "live_enabled": bool(req.live),
            "strategy_mode": "FAST",
            "auto_select": True,
            "max_positions": int(req.max_positions),
            "risk_pct": float(req.risk_pct),
            "leverage": int(req.leverage),
        }
        if not alpha_engine.start(settings):
            raise ValueError("引擎未启动：已有运行任务，请刷新状态")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"启动失败: {exc}")
    return {"success": True,
            "message": "V7 实盘已启动（涨幅榜选币 + 指标信号自动交易）" if req.live
                       else "V7 模拟盘已启动（涨幅榜选币 + 指标信号自动交易）",
            "live": bool(req.live)}


@router.post("/api/stop")
@_control
def tv_stop():
    import alpha_engine
    try:
        alpha_engine.stop()
        return {"success": True, "message": "V7 已停止"}
    except Exception as exc:
        return {"success": False, "stopped": True,
                "message": f"已禁止新开仓，收尾尚未完成: {exc}"}


@router.post("/api/emergency")
@_control
def tv_emergency():
    import alpha_engine
    try:
        r = alpha_engine.emergency_flatten()
        return {"success": bool(r.get("ok", True)), **r}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"紧急平仓异常: {exc}")


@router.post("/api/reset")
@_control
def tv_reset():
    import alpha_engine
    with alpha_engine.LOCK:
        if alpha_engine.STATE.get("running"):
            raise HTTPException(status_code=400, detail="请先停止再重置模拟盘")
        worker = getattr(alpha_engine, "LOOP_THREAD", None)
        if worker is not None and worker.is_alive():
            raise HTTPException(status_code=409, detail="上一轮仍在收尾，不能重置")
        if alpha_engine.STATE.get("mode") != "paper" or any(
                p.get("live") for p in (alpha_engine.STATE.get("positions") or {}).values()):
            raise HTTPException(status_code=409, detail="当前为实盘状态，不能使用模拟账户重置")
        alpha_engine.STATE["day_start_equity"] = 10000.0
        alpha_engine.STATE["day"] = time.strftime("%Y-%m-%d")
        alpha_engine.STATE["consecutive_losses"] = 0
        alpha_engine.STATE["cycle_errors"] = 0
        alpha_engine.STATE["last_cycle"] = None
        alpha_engine.STATE["live_start_equity"] = None
        alpha_engine.STATE["v7_attempted"] = {}
        alpha_engine.STATE["v6_attempted"] = {}
        alpha_engine.STATE["maker_skip_until"] = {}
        alpha_engine.STATE["last"] = {}
        alpha_engine.STATE["balance"] = 10000.0
        alpha_engine.STATE["equity"] = 10000.0
        alpha_engine.STATE["positions"] = {}
        alpha_engine.STATE["paper_trades"] = []
        alpha_engine.STATE["history"] = []
        alpha_engine.STATE["risk_block"] = ""
        alpha_engine.STATE["live_error"] = ""
    alpha_engine._persist()
    return {"success": True, "message": "V7 模拟账户已重置为 10000 USDT"}


class TVExitSwitchRequest(BaseModel):
    trend_trail: Optional[bool] = None
    partial_tp: Optional[bool] = None


def _exit_switches():
    import alpha_engine
    return {k: alpha_engine.gate_enabled(k) for k in ("trend_trail", "partial_tp")}


@router.get("/api/exit-switches")
def tv_get_exit_switches():
    """全局出场开关：移动止损(趋势跟踪)与分批止盈。对 V6/V7 所有策略生效。"""
    return {"success": True, "switches": _exit_switches()}


@router.post("/api/exit-switches")
@_control
def tv_set_exit_switches(req: TVExitSwitchRequest):
    import alpha_engine
    values = {k: v for k, v in (("trend_trail", req.trend_trail), ("partial_tp", req.partial_tp)) if v is not None}
    try:
        alpha_engine.set_gate_switches(values)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"success": True, "switches": _exit_switches(),
            "message": "全局出场开关已生效；实盘已有持仓同步挂/撤交易所原生单"}


class TVParamsRequest(BaseModel):
    params: Optional[Dict[str, Any]] = None
    top: Optional[int] = None


@router.post("/api/params")
@_control
def tv_set_params(req: TVParamsRequest):
    try:
        params = alpha_fast_v7.validate_params(req.params)
        if req.top is not None and not 1 <= req.top <= 100:
            raise ValueError("选币数量须在 1~100 之间")
        alpha_fast_v7.set_runtime_params(params)
        if req.top is not None:
            tv_universe.configure({"top": int(req.top)})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"success": True, "message": "V7 策略参数已更新",
            "params": alpha_fast_v7.get_runtime_params()}


@router.get("/api/params")
def tv_get_params():
    return {"success": True, "params": alpha_fast_v7.get_runtime_params(), "top": int(tv_universe.S["cfg"].get("top", 20))}


# ---------------------------------------------------------------- 页面

@router.get("")
@router.get("/")
def tv_page():
    html_path = _BASE / "templates" / "tv.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="未找到 V7 终端页面")
    return FileResponse(str(html_path))


@router.get("/api/ready")
def tv_ready():
    return {"ready": True, "launch_id": os.getenv("ALPHA_V7_LAUNCH_ID", "")}
