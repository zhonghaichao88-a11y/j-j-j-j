"""
ALPHA-X FAST · v5-XS 横截面动量市场中性篮子（独立模块，不影响 v3/v4/v5 逐币引擎）
====================================================================================
专业做法（学术与 CTA 多空基金的日频截面动量）：
  每天按过去 lookback 根 K线的收益率给一篮子币排名，
  等权做多最强 k 个、做空最弱 k 个，对冲掉大部分大盘方向(beta≈0)，
  赚"强者恒强、弱者恒弱"的相对强弱钱；次日调仓，只对变化的腿下单(精确换手)。

与 FAST 逐币裸K引擎的区别：
  - 这是【组合层】策略，一次持有 2k 个仓(默认多5空5)，目标是市场中性、低回撤；
  - 胜率不高(~45-50%)，靠盈亏比(PF)赚钱，不是高胜率打法；
  - 本模块只产出"目标篮子/权重/调仓指令"，纯函数、可单测、不联网、不下单；
    实盘执行由上层(模拟盘优先)按订单清单调用 okx_client，绝不自动加杠杆/越权。

回测见 backtest/xs_backtest.py（直接 import 本模块，保证回测=实盘同一套信号）。

诚实边界(199天 31 小币实测，详见 FAST_v5 报告)：
  INS 多空对冲 taker 扣费 PF≈2.1/夏普≈4.0/t≈2.0；OOS PF≈1.2/夏普≈1.2/t≈0.5；
  样本外统计显著性不足，牛市跑输单纯持币，需 ≥1 年样本与模拟盘确认后再考虑实盘。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence
import numpy as np
import pandas as pd

DEFAULT_LOOKBACK = 288   # 24h（5m K线 288 根）
DEFAULT_K = 5            # 多、空各 5 个
DEFAULT_GROSS = 2.0      # 总名义=权益×2（多1倍 + 空1倍），市场中性标准 gross


def momentum_score(close: pd.DataFrame, lookback: int = DEFAULT_LOOKBACK) -> pd.Series:
    """过去 lookback 根的简单收益率作为截面动量分（t 收盘可得，无未来函数）。"""
    if not isinstance(close, pd.DataFrame):
        raise TypeError("close 必须是列=币、行=时间的 DataFrame")
    if lookback <= 0:
        raise ValueError("lookback 必须为正")
    return close.iloc[-1] / close.iloc[-1 - lookback] - 1.0


def select_basket(score: pd.Series, k: int = DEFAULT_K,
                  tradable: Optional[Sequence[str]] = None) -> Dict[str, List[str]]:
    """按动量分排名，返回 {'longs': 最强k, 'shorts': 最弱k}。tradable 白名单可再过滤。"""
    s = score.replace([np.inf, -np.inf], np.nan).dropna()
    if tradable is not None:
        s = s[s.index.isin(list(tradable))]
    if len(s) < 2 * k + 5:
        # 币不够就不凑数（宁可空仓也不硬开），保证篮子质量
        return {"longs": [], "shorts": [], "reason": f"可排名币 {len(s)} < {2*k+5}，放弃调仓"}
    order = s.sort_values()
    return {"longs": list(order.index[-k:][::-1]),   # 最强在前
            "shorts": list(order.index[:k]),         # 最弱在前
            "reason": "ok"}


def target_weights(basket: Dict[str, List[str]], gross: float = DEFAULT_GROSS) -> pd.Series:
    """多空等权目标权重：多腿合计 +gross/2，空腿合计 -gross/2。"""
    longs, shorts = basket.get("longs", []), basket.get("shorts", [])
    w = pd.Series(0.0, index=list(dict.fromkeys(longs + shorts)))
    if longs:
        w[longs] = gross / 2.0 / len(longs)
    if shorts:
        w[shorts] = -gross / 2.0 / len(shorts)
    return w


def rebalance_orders(prev_w: pd.Series, target_w: pd.Series,
                     price: pd.Series, equity: float,
                     min_notional: float = 5.0) -> List[Dict]:
    """对比新旧权重，产出精确换手的调仓订单（只动需要变的腿）。
    每条: {symbol, side:'long'/'short', action:'open'/'close'/'add'/'trim',
           target_weight, delta_weight, target_notional, delta_notional}。
    数量级由上层结合合约面值/张数换算；这里只给名义与方向，保持交易所无关。"""
    idx = target_w.index.union(prev_w.index)
    tw = target_w.reindex(idx).fillna(0.0)
    pw = prev_w.reindex(idx).fillna(0.0)
    px = price.reindex(idx)
    orders: List[Dict] = []
    for sym in idx:
        d = float(tw[sym] - pw[sym])
        if not np.isfinite(d) or abs(d) * equity < min_notional:
            continue
        tgt = float(tw[sym])
        if tgt > 0:
            side = "long"
        elif tgt < 0:
            side = "short"
        else:
            side = "close_long" if pw[sym] > 0 else "close_short"
        if abs(pw[sym]) < 1e-12:
            action = "open"
        elif abs(tgt) < 1e-12:
            action = "close"
        else:
            action = "add" if abs(tgt) > abs(pw[sym]) else "trim"
        orders.append({
            "symbol": sym, "side": side, "action": action,
            "target_weight": round(tgt, 5), "delta_weight": round(d, 5),
            "price": float(px[sym]) if np.isfinite(float(px[sym])) else None,
            "target_notional": round(tgt * equity, 2),
            "delta_notional": round(d * equity, 2),
        })
    return orders


@dataclass
class XSState:
    """组合层运行状态（模拟盘/实盘共用的轻量账本）。"""
    weights: pd.Series
    last_rebalance: Optional[pd.Timestamp] = None

    @classmethod
    def empty(cls, symbols: Sequence[str]):
        return cls(weights=pd.Series(0.0, index=list(symbols)))
