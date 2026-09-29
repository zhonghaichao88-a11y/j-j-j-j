"""ALPHA-X 入场位置优化器。

职责非常单一：方向已经由现有模型/委员会确认后，判断当前市价是否处于
更有优势的入场区域。它不改变 LONG/SHORT/FLAT，也不替代原有风控、仓位、
流动性、执行和 TP/SL 链路。关闭开关时完全旁路。
"""
from __future__ import annotations

from typing import Any, Dict
import math
import numpy as np
import pandas as pd


FACTORS = (
    "当前价格",
    "N字结构",
    "FVG",
    "BOS/CHOCH",
    "Liquidity Sweep",
    "Order Block",
    "支撑阻力",
    "回踩位置",
    "突破位置",
)


def _num(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _frame(candles: Any) -> pd.DataFrame:
    if candles is None:
        return pd.DataFrame()
    if isinstance(candles, pd.DataFrame):
        d = candles.copy()
    elif isinstance(candles, list):
        d = pd.DataFrame(candles)
    else:
        return pd.DataFrame()
    cols = ["open", "high", "low", "close"]
    if any(c not in d.columns for c in cols):
        return pd.DataFrame()
    for c in cols:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    return d.dropna(subset=cols).reset_index(drop=True)


def _atr(d: pd.DataFrame, n: int = 14) -> float:
    if len(d) < 3:
        return 0.0
    prev = d["close"].shift(1)
    tr = pd.concat([(d["high"] - d["low"]), (d["high"] - prev).abs(), (d["low"] - prev).abs()], axis=1).max(axis=1)
    return _num(tr.tail(n).mean())


def _near(px: float, level: float, tolerance: float) -> bool:
    return abs(px - level) <= max(tolerance, abs(level) * 0.001)


def _factor_scores(d: pd.DataFrame, side: str, px: float) -> Dict[str, float]:
    """Return 0..1 scores using completed bars only. Missing structures score neutral."""
    atr = _atr(d)
    if atr <= 0 or len(d) < 12 or px <= 0:
        return {k: 0.5 for k in FACTORS}
    last = d.iloc[-1]
    recent = d.tail(24)
    prior = d.iloc[-9:-1]
    scores = {k: 0.5 for k in FACTORS}

    # Current price: avoid chasing; reward being near recent structural levels.
    recent_high = _num(recent["high"].max())
    recent_low = _num(recent["low"].min())
    if side == "long":
        dist_high = (recent_high - px) / atr
        scores["当前价格"] = float(np.clip(0.45 + 0.12 * dist_high, 0.0, 1.0))
    else:
        dist_low = (px - recent_low) / atr
        scores["当前价格"] = float(np.clip(0.45 + 0.12 * dist_low, 0.0, 1.0))

    # N pattern: simple completed-bar pullback/reclaim structure.
    a, b, c = d.iloc[-6], d.iloc[-4], d.iloc[-1]
    if side == "long":
        n_ok = a["high"] < b["high"] and c["close"] >= b["close"] and c["low"] <= b["high"] + atr * 0.25
    else:
        n_ok = a["low"] > b["low"] and c["close"] <= b["close"] and c["high"] >= b["low"] - atr * 0.25
    scores["N字结构"] = 0.82 if n_ok else 0.46

    # FVG: three-bar imbalance; score proximity to an active gap.
    fvg_scores = []
    for i in range(max(2, len(d) - 20), len(d)):
        p2, p1, cur = d.iloc[i - 2], d.iloc[i - 1], d.iloc[i]
        if side == "long" and p2["high"] < cur["low"]:
            lo, hi = float(p2["high"]), float(cur["low"])
        elif side == "short" and p2["low"] > cur["high"]:
            lo, hi = float(cur["high"]), float(p2["low"])
        else:
            continue
        if lo <= px <= hi:
            fvg_scores.append(0.92)
        else:
            dist = min(abs(px - lo), abs(px - hi)) / atr
            fvg_scores.append(float(np.clip(0.88 - 0.18 * dist, 0.25, 0.88)))
    if fvg_scores:
        scores["FVG"] = max(fvg_scores[-3:])

    # BOS / CHOCH: current completed close relative to prior local structure.
    swing_hi = _num(prior["high"].max())
    swing_lo = _num(prior["low"].min())
    if side == "long":
        scores["BOS/CHOCH"] = 0.90 if last["close"] > swing_hi else (0.68 if last["close"] > prior["close"].mean() else 0.38)
    else:
        scores["BOS/CHOCH"] = 0.90 if last["close"] < swing_lo else (0.68 if last["close"] < prior["close"].mean() else 0.38)

    # Liquidity sweep: wick through a prior extreme, then close back inside.
    prev_ext = d.iloc[-7:-1]
    if side == "long":
        swept = last["low"] < prev_ext["low"].min() and last["close"] > prev_ext["low"].min()
    else:
        swept = last["high"] > prev_ext["high"].max() and last["close"] < prev_ext["high"].max()
    scores["Liquidity Sweep"] = 0.88 if swept else 0.48

    # Order Block: last opposite candle before the recent directional impulse.
    ob = None
    for i in range(len(d) - 5, max(-1, len(d) - 18), -1):
        row = d.iloc[i]
        if side == "long" and row["close"] < row["open"]:
            ob = float((row["open"] + row["close"]) / 2)
            break
        if side == "short" and row["close"] > row["open"]:
            ob = float((row["open"] + row["close"]) / 2)
            break
    scores["Order Block"] = 0.5 if ob is None else float(np.clip(0.88 - abs(px - ob) / atr * 0.16, 0.25, 0.88))

    # Support/resistance: distance to a recent structural extreme.
    level = recent_low if side == "long" else recent_high
    scores["支撑阻力"] = float(np.clip(0.92 - abs(px - level) / atr * 0.16, 0.25, 0.92))

    # Pullback: reward a moderate retracement rather than an extended chase.
    move = recent_high - recent_low
    if move > 0:
        retrace = (recent_high - px) / move if side == "long" else (px - recent_low) / move
        scores["回踩位置"] = float(np.clip(1.0 - abs(retrace - 0.45) * 1.4, 0.25, 0.92))

    # Breakout: strong completed close beyond prior extreme is valid even without pullback.
    if side == "long":
        breakout = last["close"] > prior["high"].max() and (last["close"] - last["open"]) > 0
    else:
        breakout = last["close"] < prior["low"].min() and (last["open"] - last["close"]) > 0
    scores["突破位置"] = 0.92 if breakout else 0.45

    return {k: float(np.clip(v, 0.0, 1.0)) for k, v in scores.items()}


def evaluate(candles: Any, side: str, current_price: float) -> Dict[str, Any]:
    d = _frame(candles)
    side = "long" if str(side).lower() in ("long", "buy") else "short"
    px = _num(current_price)
    if px <= 0 or len(d) < 12:
        return {"enabled": True, "decision": "WAIT", "score": 0.0, "entry_price": px, "reason": "入场位置数据不足，等待下一轮完整行情", "factors": {k: 0.0 for k in FACTORS}}
    scores = _factor_scores(d, side, px)
    weights = {
        "当前价格": 1.20, "N字结构": 1.00, "FVG": 1.20, "BOS/CHOCH": 1.15,
        "Liquidity Sweep": 0.80, "Order Block": 0.80, "支撑阻力": 1.10,
        "回踩位置": 1.10, "突破位置": 1.00,
    }
    total_w = sum(weights.values())
    score = sum(scores[k] * weights[k] for k in FACTORS) / total_w
    breakout = scores["突破位置"] >= 0.9 and scores["BOS/CHOCH"] >= 0.85
    core = np.mean([scores["当前价格"], scores["FVG"], scores["BOS/CHOCH"], scores["支撑阻力"]])
    if breakout and score >= 0.60:
        decision, reason = "ENTER", "强突破结构成立，允许正常仓位进入原有下单流程"
        tier = "很强"
    elif score >= 0.62 and core >= 0.52:
        decision, reason = "ENTER", "多因素入场位置共振，允许正常仓位进入"
        tier = "强"
    elif score >= 0.35:
        decision, reason = "ENTER", "入场位置达到可参与标准，允许正常仓位进入"
        tier = "一般"
    else:
        decision, reason = "WAIT", "当前价格位置明显不理想，等待更好位置"
        tier = "不适合进场"
    return {
        "enabled": True,
        "decision": decision,
        "score": round(float(score), 4),
        "entry_price": px,
        "side": side,
        "reason": reason,
        "entry_quality": tier,
        "entry_size_multiplier": 1.0 if tier in ("很强", "强", "一般") else 0.0,
        "factors": {k: round(float(scores[k]), 4) for k in FACTORS},
        "factor_count_good": sum(1 for v in scores.values() if v >= 0.65),
        "bars_used": int(len(d)),
        "uses_completed_bars_only": True,
    }
