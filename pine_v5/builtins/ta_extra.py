"""Pine v5 `ta` namespace extras — indicators added through the registry.

Formulas follow the TradingView Pine v5 reference:
  WPR  = (highest(high,n) - close) / (highest(high,n) - lowest(low,n)) * -100
  CCI  = (TP - SMA(TP,n)) / (0.015 * mean(|TP - SMA(TP,n)|))
  MFI  = 100 - 100 / (1 + positive_flow / negative_flow)  (flow = typical*vol)
  OBV  = cumulative sum of volume signed by close direction
  SAR  = Wilder's Parabolic SAR (stateful)
  KC   = EMA(source,n) +/- mult * RMA(TR or range, n)
  Donchian = [highest(high,n), lowest(low,n), midpoint]
"""
from __future__ import annotations
import math

import numpy as np

from .registry import register
from ._helpers import PineError, isna, na, num, arg, arg_int


def _call(fn, *children):
    return ('call', fn, list(children), {})


# ── ta.wpr ──────────────────────────────────────────────────────────────────
@register('ta.wpr')
def ta_wpr(runtime, node, i):
    source = node[2][0] if len(node[2]) > 0 else ('name', 'close')
    n = arg_int(runtime, node, i, 1, 'length', 14, lo=1, hi=5000)
    highs = runtime.window(('name', 'high'), n, i)
    lows = runtime.window(('name', 'low'), n, i)
    if not highs:
        return na()
    hh, ll = max(highs), min(lows)
    c = runtime.evaluate(source, i)
    if isna(c):
        return na()
    if hh == ll:
        return na()
    return (hh - c) / (hh - ll) * -100.0


# ── ta.cci ───────────────────────────────────────────────────────────────────
@register('ta.cci')
def ta_cci(runtime, node, i):
    source = node[2][0] if len(node[2]) > 0 else ('name', 'close')
    n = arg_int(runtime, node, i, 1, 'length', 14, lo=1, hi=5000)
    values = runtime.window(source, n, i)
    if len(values) < n:
        return na()
    arr = np.asarray(values, dtype=float)
    ma = float(np.mean(arr))
    md = float(np.mean(np.abs(arr - ma)))
    if md == 0:
        return na()
    return float((arr[-1] - ma) / (0.015 * md))


# ── ta.mfi ──────────────────────────────────────────────────────────────────
@register('ta.mfi')
def ta_mfi(runtime, node, i):
    n = arg_int(runtime, node, i, 0, 'length', 14, lo=1, hi=5000)
    if i + 1 < n:
        return na()
    f = runtime.f
    pos = neg = 0.0
    for j in range(i - n + 1, i + 1):
        tp = (float(f['high'][j]) + float(f['low'][j]) + float(f['close'][j])) / 3.0
        rmf = tp * float(f['volume'][j])
        prev_tp = ((float(f['high'][j - 1]) + float(f['low'][j - 1])
                    + float(f['close'][j - 1])) / 3.0) if j else tp
        if tp > prev_tp:
            pos += rmf
        elif tp < prev_tp:
            neg += rmf
    if neg == 0:
        return 100.0 if pos > 0 else na()
    return 100.0 - 100.0 / (1.0 + pos / neg)


# ── ta.obv (stateful) ────────────────────────────────────────────────────────
@register('ta.obv')
def ta_obv(runtime, node, i):
    key = ('obv', id(node))
    f = runtime.f
    if i == 0:
        runtime.synthetic[key] = 0.0
        return 0.0
    prev = runtime.synthetic.get(key, 0.0)
    c0, c1 = float(f['close'][i]), float(f['close'][i - 1])
    v = float(f['volume'][i])
    if c0 > c1:
        out = prev + v
    elif c0 < c1:
        out = prev - v
    else:
        out = prev
    runtime.synthetic[key] = out
    return out


# ── ta.sar (stateful, Wilder) ───────────────────────────────────────────────
@register('ta.sar')
def ta_sar(runtime, node, i):
    start = num(arg(runtime, node, i, 0, 'start', 0.02))
    inc = num(arg(runtime, node, i, 1, 'increment', 0.02))
    mx = num(arg(runtime, node, i, 2, 'maximum', 0.2))
    for v in (start, inc, mx):
        if isna(v) or v <= 0:
            raise PineError('ta.sar 参数必须为正数')
    key = ('sar', id(node))
    state = runtime.synthetic.setdefault(key, {})
    f = runtime.f
    h, l = float(f['high'][i]), float(f['low'][i])
    if not state:
        state.update(trend=1, af=start, ep=h, sar=l)
        return na()
    trend, af, ep, sar = state['trend'], state['af'], state['ep'], state['sar']
    prev_l = float(f['low'][i - 1])
    prev_h = float(f['high'][i - 1])
    if trend == 1:  # long
        sar = sar + af * (ep - sar)
        sar = min(sar, prev_l, float(f['low'][i - 2]) if i >= 2 else prev_l)
        if h > ep:
            ep, af = h, min(af + inc, mx)
        if l < sar:  # flip down
            trend, sar, ep, af = -1, ep, l, start
    else:  # short
        sar = sar - af * (ep - sar)
        sar = max(sar, prev_h, float(f['high'][i - 2]) if i >= 2 else prev_h)
        if l < ep:
            ep, af = l, min(af + inc, mx)
        if h > sar:  # flip up
            trend, sar, ep, af = 1, ep, h, start
    state.update(trend=trend, af=af, ep=ep, sar=sar)
    return float(sar)


# ── ta.kc ────────────────────────────────────────────────────────────────────
@register('ta.kc')
def ta_kc(runtime, node, i):
    source = node[2][0] if len(node[2]) > 0 else ('name', 'close')
    n = arg_int(runtime, node, i, 1, 'length', 20, lo=1, hi=5000)
    mult = num(arg(runtime, node, i, 2, 'mult', 2.0))
    use_tr = arg(runtime, node, i, 3, 'use_tr', True)
    if isna(mult):
        return [na(), na(), na()]
    basis = runtime.evaluate(_call('ta.ema', source, ('const', float(n))), i)
    if use_tr:
        rng = _call('ta.tr', ('const', True))
    else:
        rng = ('binary', '-', ('name', 'high'), ('name', 'low'))
    span = runtime.evaluate(_call('ta.rma', rng, ('const', float(n))), i)
    if isna(basis) or isna(span):
        return [na(), na(), na()]
    return [float(basis), float(basis + mult * span),
            float(basis - mult * span)]


# ── ta.donchian ──────────────────────────────────────────────────────────────
@register('ta.donchian')
def ta_donchian(runtime, node, i):
    n = arg_int(runtime, node, i, 0, 'length', 20, lo=1, hi=5000)
    highs = runtime.window(('name', 'high'), n, i)
    lows = runtime.window(('name', 'low'), n, i)
    if not highs:
        return [na(), na(), na()]
    upper, lower = max(highs), min(lows)
    return [float(upper), float(lower), float((upper + lower) / 2.0)]


# ── ta.highestbars / ta.lowestbars ────────────────────────────────────────────
def _bars_offset(runtime, node, i, want_max):
    if len(node[2]) >= 2:
        source, n_node = node[2][0], node[2][1]
    else:
        n_node = node[2][0]
        source = ('name', 'high' if want_max else 'low')
    n = num(runtime.evaluate(n_node, i))
    if isna(n) or int(n) != n or not 1 <= n <= 5000:
        raise PineError('bars 周期无效')
    values = runtime.window(source, int(n), i)
    if not values:
        return na()
    ext = max(values) if want_max else min(values)
    pos = values.index(ext)  # first occurrence (oldest, Pine-compatible)
    return pos - (len(values) - 1)  # negative offset or 0


@register('ta.highestbars')
def ta_highestbars(runtime, node, i):
    return _bars_offset(runtime, node, i, True)


@register('ta.lowestbars')
def ta_lowestbars(runtime, node, i):
    return _bars_offset(runtime, node, i, False)


# ── ta.momentum ──────────────────────────────────────────────────────────────
@register('ta.momentum')
def ta_momentum(runtime, node, i):
    source = node[2][0] if len(node[2]) > 0 else ('name', 'close')
    n = arg_int(runtime, node, i, 1, 'length', 10, lo=1, hi=5000)
    x = runtime.evaluate(source, i)
    prev = runtime.evaluate(source, i - n)
    if isna(x) or isna(prev):
        return na()
    return x - prev


# ── ta.slope (linear regression slope) ────────────────────────────────────────
@register('ta.slope')
def ta_slope(runtime, node, i):
    source = node[2][0] if len(node[2]) > 0 else ('name', 'close')
    n = arg_int(runtime, node, i, 1, 'length', 9, lo=2, hi=5000)
    values = runtime.window(source, n, i)
    if len(values) < 2:
        return na()
    slope, _ = np.polyfit(np.arange(len(values), dtype=float),
                          np.asarray(values, dtype=float), 1)
    return float(slope)


# ── ta.percentrank ───────────────────────────────────────────────────────────
@register('ta.percentrank')
def ta_percentrank(runtime, node, i):
    source = node[2][0] if len(node[2]) > 0 else ('name', 'close')
    n = arg_int(runtime, node, i, 1, 'length', 20, lo=1, hi=5000)
    values = runtime.window(source, n, i)
    if len(values) < n:
        return na()
    x = values[-1]
    if isna(x):
        return na()
    below = sum(1 for v in values if not isna(v) and v < x)
    return 100.0 * below / len(values)
