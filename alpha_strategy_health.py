"""ALPHA-X 交易后复盘与策略健康度。
只做交易学习：记录真实平仓结果，识别重复失败模式，并给下一次信号提供软性仓位系数。
不负责安全、KILL、强制平仓，也不会单独硬拦截订单。
"""
from __future__ import annotations
import json, math, time
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List

ROOT = Path(__file__).parent
FILE = ROOT / "alpha_strategy_health.json"
LOCK = RLock()
MAX_RECORDS = 2000


def _num(v, default=0.0):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _load() -> Dict[str, Any]:
    try:
        if FILE.exists():
            x = json.loads(FILE.read_text(encoding="utf-8"))
            if isinstance(x, dict):
                x.setdefault("trades", [])
                return x
    except Exception:
        pass
    return {"trades": []}


STATE = _load()


def _save():
    try:
        tmp = FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(STATE, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        tmp.replace(FILE)
    except Exception:
        pass


def _key(symbol: str, mode: str, side: str, regime: str) -> str:
    return "|".join([str(symbol or "UNKNOWN"), str(mode or "SHORT"), str(side or "UNKNOWN"), str(regime or "UNKNOWN")])


def _failure_pattern(attr: dict, position: dict | None = None) -> str:
    reason = str(attr.get("reason") or "").upper()
    mae = abs(_num(attr.get("mae_pct")))
    mfe = max(0.0, _num(attr.get("mfe_pct")))
    hold = _num(attr.get("holding_seconds"), 0.0)
    sl = abs(_num((position or {}).get("dynamic_sl_pct"), 0.0) * 100.0)
    if reason in {"SL", "STOP", "STOP_LOSS"}:
        if hold > 0 and hold <= 15 * 60 and mfe < max(0.15, sl * 0.35):
            return "入场后快速反向"
        if mfe < max(0.20, sl * 0.50):
            return "信号方向未得到价格确认"
        if mae > max(1.0, sl * 1.15):
            return "止损前出现较大不利波动"
        return "止损"
    if reason in {"TIME", "TIMEOUT"} and _num(attr.get("net_pnl")) < 0:
        return "持仓时间过长仍未兑现"
    if _num(attr.get("net_pnl")) < 0:
        return "其他亏损"
    return ""


def record_trade(attr: dict, position: dict | None = None, prediction: dict | None = None) -> Dict[str, Any]:
    """记录一笔已经确认平仓的真实交易，并返回本次复盘结论。"""
    if not isinstance(attr, dict):
        return {"recorded": False, "reason": "无效归因"}
    p = position or {}
    pred = prediction or {}
    pnl = _num(attr.get("net_pnl"))
    now = time.time()
    opened = _num(p.get("opened_at"), 0.0)
    row = {
        "time": now,
        "symbol": str(attr.get("symbol") or p.get("symbol") or "UNKNOWN"),
        "side": str(attr.get("side") or p.get("side") or "UNKNOWN"),
        "strategy_mode": str(p.get("strategy_mode") or pred.get("strategy_mode") or "SHORT"),
        "strategy_label": str(p.get("strategy_label") or pred.get("strategy_label") or ""),
        "regime": str((pred.get("market_context") or {}).get("regime") or (p.get("market_context") or {}).get("regime") or "UNKNOWN"),
        "reason": str(attr.get("reason") or "UNKNOWN"),
        "net_pnl": pnl,
        "mfe_pct": _num(attr.get("mfe_pct")),
        "mae_pct": _num(attr.get("mae_pct")),
        "holding_seconds": max(0.0, now - opened) if opened else 0.0,
        "confidence": _num(pred.get("confidence") or p.get("confidence")),
        "signal_tier": str(pred.get("signal_tier") or p.get("signal_tier") or ""),
        "failure_pattern": _failure_pattern(attr, p),
    }
    with LOCK:
        STATE.setdefault("trades", []).append(row)
        STATE["trades"] = STATE["trades"][-MAX_RECORDS:]
        result = _analyze_rows_locked(row["symbol"], row["strategy_mode"], row["side"], row["regime"])
        _save()
    return {"recorded": True, "trade": row, **result}


def _analyze_rows_locked(symbol: str, mode: str, side: str, regime: str) -> Dict[str, Any]:
    key = _key(symbol, mode, side, regime)
    rows = [r for r in STATE.get("trades", []) if _key(r.get("symbol"), r.get("strategy_mode"), r.get("side"), r.get("regime")) == key]
    recent = rows[-20:]
    losses = [r for r in recent if _num(r.get("net_pnl")) < 0]
    wins = [r for r in recent if _num(r.get("net_pnl")) > 0]
    streak = 0
    for r in reversed(recent):
        if _num(r.get("net_pnl")) < 0: streak += 1
        else: break
    patterns: Dict[str, int] = {}
    for r in losses:
        fp = str(r.get("failure_pattern") or "")
        if fp: patterns[fp] = patterns.get(fp, 0) + 1
    repeated = sorted(patterns.items(), key=lambda x: x[1], reverse=True)
    loss_rate = len(losses) / len(recent) if recent else 0.0
    # 只改变交易策略权重，不形成硬拦截；健康度越差，下一次优先降仓/等待更强确认。
    multiplier = 1.0
    if streak >= 2: multiplier *= 0.85
    if streak >= 3: multiplier *= 0.82
    if len(recent) >= 5 and loss_rate >= 0.60: multiplier *= 0.88
    if repeated and repeated[0][1] >= 2: multiplier *= 0.90
    multiplier = max(0.55, min(1.10, multiplier))
    if multiplier < 0.90:
        health = "偏弱，建议降低该条件下策略权重"
    elif multiplier < 0.99:
        health = "轻微走弱，继续观察"
    elif len(recent) >= 5 and len(wins) >= len(losses):
        health = "健康"
    else:
        health = "观察中"
    return {
        "key": key,
        "trades": len(recent),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(recent)) if recent else 0.0,
        "loss_streak": streak,
        "loss_rate": loss_rate,
        "repeated_failures": [{"pattern": k, "count": v} for k, v in repeated[:5]],
        "health": health,
        "size_multiplier": multiplier,
        "action": "正常参与" if multiplier >= 0.99 else ("适度降仓并观察" if multiplier >= 0.75 else "明显降权，等待条件改善"),
    }


def advise(symbol: str, mode: str, side: str, regime: str) -> Dict[str, Any]:
    with LOCK:
        return _analyze_rows_locked(symbol, mode, side, regime)


def report(limit: int = 20) -> Dict[str, Any]:
    with LOCK:
        rows = list(STATE.get("trades", []))[-max(1, min(int(limit), 200)):]
        groups = {}
        for r in STATE.get("trades", []):
            k = _key(r.get("symbol"), r.get("strategy_mode"), r.get("side"), r.get("regime"))
            groups[k] = _analyze_rows_locked(r.get("symbol"), r.get("strategy_mode"), r.get("side"), r.get("regime"))
    return {"trades": rows, "groups": list(groups.values()), "total_records": len(STATE.get("trades", []))}
