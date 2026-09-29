"""Compatibility shims for commonly-used Pine functions not yet in core.

These are best-effort implementations to improve end-to-end script
compatibility.  They are registered via the builtin registry.
"""
from __future__ import annotations
import math
from .registry import register
from ..constants import PineError, truth


def _arg(runtime, node, i, idx, key=None, default=None):
    if key is not None and key in node[3]:
        return runtime.evaluate(node[3][key], i)
    if idx < len(node[2]):
        return runtime.evaluate(node[2][idx], i)
    return default


@register('tostring')
def tostring(runtime, node, i):
    """Bare tostring() -> str.tostring()."""
    val = _arg(runtime, node, i, 0)
    fmt = _arg(runtime, node, i, 1, 'format')
    if val is None or (isinstance(val, float) and not math.isfinite(val)):
        return 'na'
    if fmt and isinstance(fmt, str) and fmt != '#.##########':
        try:
            return format(float(val), fmt.replace('#', 'g').replace('g', 'g'))
        except (ValueError, TypeError):
            pass
    if isinstance(val, bool):
        return 'true' if val else 'false'
    if isinstance(val, float):
        return str(val)
    return str(val)


@register('tonumber')
def tonumber(runtime, node, i):
    val = _arg(runtime, node, i, 0)
    if val is None:
        return float('nan')
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(str(val).strip())
    except (ValueError, TypeError):
        return float('nan')


@register('fixnan')
def fixnan(runtime, node, i):
    """Replace na with the last non-na value (series-aware)."""
    source = node[2][0] if node[2] else node[3].get('source')
    if source is None:
        raise PineError('fixnan需要source参数')
    key = ('fixnan', id(source))
    state = runtime.synthetic.setdefault(key, {'last': float('nan')})
    val = runtime.evaluate(source, i)
    if val is not None and isinstance(val, (int, float)) and math.isfinite(val):
        state['last'] = val
        return val
    return state['last']


@register('timestamp')
def timestamp(runtime, node, i):
    """Convert year/month/day/hour/minute/second to ms timestamp.

    Best-effort: uses Python datetime.  Timezone assumed UTC.
    """
    import datetime
    args = [runtime.evaluate(a, i) for a in node[2]]
    try:
        if len(args) >= 5:
            y, mo, d, h, mi = [int(x) for x in args[:5]]
            s = int(args[5]) if len(args) > 5 else 0
            dt = datetime.datetime(y, mo, d, h, mi, s, tzinfo=datetime.timezone.utc)
            return dt.timestamp() * 1000
        elif len(args) == 1 and isinstance(args[0], str):
            # ISO format string
            dt = datetime.datetime.fromisoformat(args[0].replace('Z', '+00:00'))
            return dt.timestamp() * 1000
    except (ValueError, TypeError, OverflowError):
        pass
    return float('nan')


@register('percentile_nearest_rank')
def percentile_nearest_rank(runtime, node, i):
    source = node[2][0] if len(node[2]) > 0 else None
    length = _arg(runtime, node, i, 1, default=14)
    percent = _arg(runtime, node, i, 2, default=50)
    if source is None:
        raise PineError('percentile_nearest_rank需要source参数')
    values = runtime.window(source, length, i)
    if not values:
        return float('nan')
    sorted_vals = sorted(values)
    idx = int(math.ceil(percent / 100.0 * len(sorted_vals))) - 1
    idx = max(0, min(idx, len(sorted_vals) - 1))
    return sorted_vals[idx]


@register('plotcandle')
def plotcandle(runtime, node, i):
    """Record a candle plot (no rendering)."""
    title = str(_arg(runtime, node, i, 4, 'title', 'plotcandle'))
    ident = str(runtime.line) + ':' + title
    runtime.plots.setdefault(
        ident, dict(title=title, kind='plotcandle',
                    values=[None] * runtime.n))
    return None


@register('plotbar')
def plotbar(runtime, node, i):
    title = str(_arg(runtime, node, i, 4, 'title', 'plotbar'))
    ident = str(runtime.line) + ':' + title
    runtime.plots.setdefault(
        ident, dict(title=title, kind='plotbar',
                    values=[None] * runtime.n))
    return None


@register('alert')
def alert(runtime, node, i):
    """alert() function (different from alertcondition)."""
    condition = _arg(runtime, node, i, 0, 'condition')
    if truth(condition):
        runtime.events.append(dict(
            kind='alert',
            title=str(_arg(runtime, node, i, 1, 'title', 'alert')),
            message=str(_arg(runtime, node, i, 2, 'message', '')),
            freq=str(_arg(runtime, node, i, 3, 'freq', '')),
            bar=i, known_at=int(runtime.f['ts'][i])))
    return None


@register('ticker.heikinashi')
def ticker_heikinashi(runtime, node, i):
    """Heikin-Ashi ticker modifier — requires data transformation.

    Fail-closed with a clear message since we cannot compute HA bars
    without modifying the underlying data series.
    """
    raise PineError(
        'ticker.heikinashi 需要对K线数据做Heikin-Ashi变换，'
        '当前数据源不支持；请在外部预处理K线后传入')


@register('ticker.renko')
def ticker_renko(runtime, node, i):
    raise PineError(
        'ticker.renko 需要Renko K线变换，当前数据源不支持')


@register('ticker.linebreak')
def ticker_linebreak(runtime, node, i):
    raise PineError(
        'ticker.linebreak 需要Line Break K线变换，当前数据源不支持')


@register('ticker.kagi')
def ticker_kagi(runtime, node, i):
    raise PineError(
        'ticker.kagi 需要Kagi K线变换，当前数据源不支持')


@register('ticker.pnf')
def ticker_pnf(runtime, node, i):
    raise PineError(
        'ticker.pnf 需要Point & Figure K线变换，当前数据源不支持')


@register('str.replace_all')
def str_replace_all(runtime, node, i):
    s = _arg(runtime, node, i, 0)
    target = _arg(runtime, node, i, 1)
    replacement = _arg(runtime, node, i, 2, default='')
    if not isinstance(s, str) or not isinstance(target, str):
        return float('nan')
    return s.replace(target, replacement if isinstance(replacement, str) else str(replacement))


@register('iff')
def iff(runtime, node, i):
    """v3 ternary function: iff(condition, then, else)."""
    cond = _arg(runtime, node, i, 0)
    yes = _arg(runtime, node, i, 1)
    no = _arg(runtime, node, i, 2)
    return yes if truth(cond) else no


@register('int')
def int_cast(runtime, node, i):
    val = _arg(runtime, node, i, 0)
    if val is None or (isinstance(val, float) and not math.isfinite(val)):
        return float('nan')
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return float('nan')


@register('float')
def float_cast(runtime, node, i):
    val = _arg(runtime, node, i, 0)
    if val is None:
        return float('nan')
    try:
        return float(val)
    except (ValueError, TypeError):
        return float('nan')


@register('bool')
def bool_cast(runtime, node, i):
    val = _arg(runtime, node, i, 0)
    if val is None or (isinstance(val, float) and not math.isfinite(val)):
        return False
    return truth(val)


@register('strategy.cancel')
def strategy_cancel(runtime, node, i):
    """Cancel pending orders by id or all."""
    oid = _arg(runtime, node, i, 0, 'id')
    when = _arg(runtime, node, i, 1, 'when')
    if when is not None and not truth(when):
        return None
    if hasattr(runtime, 'broker') and runtime.broker:
        if oid:
            runtime.broker.cancel(str(oid))
        else:
            runtime.broker.cancel_all()
    return None


@register('strategy.cancel_all')
def strategy_cancel_all(runtime, node, i):
    when = _arg(runtime, node, i, 0, 'when')
    if when is not None and not truth(when):
        return None
    if hasattr(runtime, 'broker') and runtime.broker:
        runtime.broker.cancel_all()
    return None


def _ts_to_dt(runtime, i):
    import datetime
    ts = runtime.f['ts'][i] if 'ts' in runtime.f else runtime.f['time'][i]
    if ts is None:
        return None
    # ts is in ms
    return datetime.datetime.fromtimestamp(ts / 1000, tz=datetime.timezone.utc)


@register('year')
def year_fn(runtime, node, i):
    dt = _ts_to_dt(runtime, i)
    return dt.year if dt else float('nan')


@register('month')
def month_fn(runtime, node, i):
    dt = _ts_to_dt(runtime, i)
    return dt.month if dt else float('nan')


@register('dayofmonth')
def dayofmonth_fn(runtime, node, i):
    dt = _ts_to_dt(runtime, i)
    return dt.day if dt else float('nan')


@register('dayofweek')
def dayofweek_fn(runtime, node, i):
    dt = _ts_to_dt(runtime, i)
    # Pine: Sunday=1, Monday=2, ... Saturday=7
    return dt.weekday() + 2 if dt else float('nan')


@register('hour')
def hour_fn(runtime, node, i):
    dt = _ts_to_dt(runtime, i)
    return dt.hour if dt else float('nan')


@register('minute')
def minute_fn(runtime, node, i):
    dt = _ts_to_dt(runtime, i)
    return dt.minute if dt else float('nan')


@register('second')
def second_fn(runtime, node, i):
    dt = _ts_to_dt(runtime, i)
    return dt.second if dt else float('nan')


@register('timenow')
def timenow_fn(runtime, node, i):
    import time
    return int(time.time() * 1000)


@register('ta.accdist')
def ta_accdist(runtime, node, i):
    """Accumulation/Distribution indicator."""
    high = runtime.f['high'][i]
    low = runtime.f['low'][i]
    close = runtime.f['close'][i]
    vol = runtime.f['volume'][i]
    rng = high - low
    if rng == 0:
        mfm = 0.0
    else:
        mfm = ((close - low) - (high - close)) / rng
    mfv = mfm * vol
    key = ('accdist', id(node))
    state = runtime.synthetic.setdefault(key, {'cum': 0.0})
    state['cum'] += mfv
    return state['cum']


@register('ta.pvt')
def ta_pvt(runtime, node, i):
    """Price Volume Trend indicator."""
    close = runtime.f['close'][i]
    vol = runtime.f['volume'][i]
    if i == 0 or close == 0:
        pvt_val = 0.0
    else:
        prev_close = runtime.f['close'][i - 1]
        pvt_val = (close - prev_close) / prev_close * vol
    key = ('pvt', id(node))
    state = runtime.synthetic.setdefault(key, {'cum': 0.0})
    state['cum'] += pvt_val
    return state['cum']


@register('ta.alma')
def ta_alma(runtime, node, i):
    """Arnaud Legoux Moving Average — simplified implementation."""
    source = node[2][0] if len(node[2]) > 0 else None
    length = _arg(runtime, node, i, 1, default=9)
    sigma = _arg(runtime, node, i, 2, default=6.0)
    offset = _arg(runtime, node, i, 3, default=0.85)
    if source is None:
        raise PineError('ta.alma需要source参数')
    values = runtime.window(source, length, i)
    n = len(values)
    if n < 2:
        return values[0] if values else float('nan')
    m = offset * (n - 1)
    s = n / sigma if sigma else 1.0
    weights = [math.exp(-((j - m) ** 2) / (2 * s * s)) for j in range(n)]
    total_w = sum(weights)
    return sum(v * w for v, w in zip(values, weights)) / total_w


@register('ta.swma')
def ta_swma(runtime, node, i):
    """Symmetrically Weighted Moving Average."""
    source = node[2][0] if len(node[2]) > 0 else None
    if source is None:
        raise PineError('ta.swma需要source参数')
    values = runtime.window(source, 4, i)
    if len(values) < 4:
        return sum(values) / len(values) if values else float('nan')
    # SWMA = (1*a + 2*b + 2*c + 1*d) / 6 for window of 4
    return (values[0] + 2 * values[1] + 2 * values[2] + values[3]) / 6


@register('strategy.opentrades.entry_price')
def strategy_opentrades_entry_price(runtime, node, i):
    if hasattr(runtime, 'broker') and runtime.broker:
        return runtime.broker.var('strategy.position_avg_price')
    return float('nan')


@register('time')
def time_fn(runtime, node, i):
    """time() function with optional timezone/resolution args."""
    return int(runtime.f['ts'][i])


@register('offset')
def offset_fn(runtime, node, i):
    """offset() — v3 function, equivalent to series[offset]."""
    source = _arg(runtime, node, i, 0)
    off = _arg(runtime, node, i, 1, default=0)
    if source is None:
        return float('nan')
    try:
        idx = i - int(off)
        if 0 <= idx < runtime.n and isinstance(source, (int, float)):
            # Can't look up history of a scalar; return na
            return float('nan')
    except (ValueError, TypeError):
        pass
    return float('nan')
