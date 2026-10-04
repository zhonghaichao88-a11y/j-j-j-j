"""和 TradingView Pine 一样算法的指标（输入输出都是 numpy 数组 / pandas Series，按K线顺序，只用当前及以前的数据）"""
import numpy as np, pandas as pd
from numba import njit

S = lambda x: x if isinstance(x, pd.Series) else pd.Series(np.asarray(x, float))


def sma(x, n): return S(x).rolling(int(n)).mean()
def stdev(x, n): return S(x).rolling(int(n)).std(ddof=0)
def highest(x, n): return S(x).rolling(int(n)).max()
def lowest(x, n): return S(x).rolling(int(n)).min()
def change(x, n=1): return S(x).diff(n)
def tsum(x, n): return S(x).rolling(int(n)).sum()


@njit(cache=True)
def _rec(x, a, seed_n):
    out = np.full(len(x), np.nan)
    s, cnt, acc = np.nan, 0, 0.0
    for i in range(len(x)):
        v = x[i]
        if np.isnan(v):
            out[i] = s
            continue
        if np.isnan(s):
            acc += v; cnt += 1
            if cnt >= seed_n:
                s = acc / cnt
        else:
            s = a * v + (1 - a) * s
        out[i] = s
    return out


def ema(x, n): return pd.Series(_rec(S(x).values.astype(float), 2.0 / (n + 1), int(n)), index=S(x).index)
def rma(x, n): return pd.Series(_rec(S(x).values.astype(float), 1.0 / n, int(n)), index=S(x).index)


def _conv(x, w):
    """滚动加权和：out[i] = sum_k w[k] * x[i-n+1+k]（前 n-1 根为 NaN）"""
    x = np.asarray(x, float); n = len(w)
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = np.convolve(np.nan_to_num(x), w[::-1], 'valid')
        bad = pd.Series(np.isnan(x)).rolling(n).sum().values > 0
        out[bad] = np.nan
    return out


def wma(x, n):
    n = int(n); w = np.arange(1, n + 1, dtype=float)
    s = S(x)
    return pd.Series(_conv(s.values, w) / w.sum(), index=s.index)


def hma(x, n): return wma(2 * wma(x, n // 2) - wma(x, n), int(np.floor(np.sqrt(n))))
def vwma(x, v, n): return sma(S(x) * S(v), n) / sma(v, n)


def rsi(x, n):
    d = S(x).diff()
    up, dn = rma(d.clip(lower=0), n), rma((-d).clip(lower=0), n)
    r = 100 - 100 / (1 + up / dn)
    return r.where(dn != 0, 100).where(up != 0, 0)


def tr(h, l, c):
    pc = S(c).shift()
    return pd.concat([S(h) - S(l), (S(h) - pc).abs(), (S(l) - pc).abs()], axis=1).max(axis=1, skipna=False).fillna(S(h) - S(l))


def atr(h, l, c, n): return rma(tr(h, l, c), n)
def stoch(c, h, l, n): return 100 * (S(c) - lowest(l, n)) / (highest(h, n) - lowest(l, n))
def cross_over(a, b): a, b = S(a), (S(b) if not np.isscalar(b) else b); return (a > b) & (a.shift() <= (b.shift() if not np.isscalar(b) else b))
def cross_under(a, b): a, b = S(a), (S(b) if not np.isscalar(b) else b); return (a < b) & (a.shift() >= (b.shift() if not np.isscalar(b) else b))
def cross(a, b): return cross_over(a, b) | cross_under(a, b)


def macd(x, f=12, s=26, sig=9):
    m = ema(x, f) - ema(x, s); g = ema(m, sig)
    return m, g, m - g


def bb(x, n, k):
    b = sma(x, n); d = k * stdev(x, n)
    return b, b + d, b - d


@njit(cache=True)
def _mad(x, n):
    out = np.full(len(x), np.nan)
    for i in range(n - 1, len(x)):
        m = 0.0
        for k in range(i - n + 1, i + 1): m += x[k]
        m /= n; d = 0.0
        for k in range(i - n + 1, i + 1): d += abs(x[k] - m)
        out[i] = d / n
    return out


def cci(x, n):
    s = S(x); m = sma(s, n)
    return (s - m) / (0.015 * pd.Series(_mad(s.values.astype(float), int(n)), index=s.index))


def mfi(src, v, n):
    ch = S(src).diff()
    up = tsum(S(v) * S(src) * (ch > 0), n); dn = tsum(S(v) * S(src) * (ch < 0), n)
    return 100 - 100 / (1 + up / dn)


def dmi(h, l, c, n, sm):
    h, l = S(h), S(l)
    up, dn = h.diff(), -l.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0); mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    t = rma(tr(h, l, c), n)
    pdi = 100 * rma(pd.Series(pdm, index=h.index), n) / t
    mdi = 100 * rma(pd.Series(mdm, index=h.index), n) / t
    s = (pdi + mdi).replace(0, np.nan)
    adx = rma((pdi - mdi).abs() / s, sm) * 100
    return pdi, mdi, adx


@njit(cache=True)
def _st(h, l, c, a, f):
    n = len(c); st = np.full(n, np.nan); dr = np.ones(n)
    up_p, dn_p = np.nan, np.nan
    for i in range(n):
        if np.isnan(a[i]):
            continue
        m = (h[i] + l[i]) / 2
        up, dn = m + f * a[i], m - f * a[i]
        pc = c[i - 1] if i > 0 else c[i]
        if not np.isnan(dn_p):
            dn = dn if (dn > dn_p or pc < dn_p) else dn_p
        if not np.isnan(up_p):
            up = up if (up < up_p or pc > up_p) else up_p
        if i == 0 or np.isnan(st[i - 1]):
            d = 1.0
        elif st[i - 1] == up_p:
            d = -1.0 if c[i] > up else 1.0
        else:
            d = 1.0 if c[i] < dn else -1.0
        dr[i] = d
        st[i] = dn if d < 0 else up
        up_p, dn_p = up, dn
    return st, dr


def supertrend(h, l, c, factor, n):
    """返回 (线, 方向)；方向 -1 = 多头（TV 的约定）"""
    a = atr(h, l, c, n).values
    st, d = _st(S(h).values, S(l).values, S(c).values, a, float(factor))
    return pd.Series(st, index=S(c).index), pd.Series(d, index=S(c).index)


@njit(cache=True)
def _sar(h, l, start, inc, mx):
    n = len(h); out = np.full(n, np.nan)
    if n < 2:
        return out
    up = True if h[1] + l[1] >= h[0] + l[0] else False
    ep = h[1] if up else l[1]; s = l[0] if up else h[0]; af = start
    out[1] = s
    for i in range(2, n):
        s = s + af * (ep - s)
        if up:
            s = min(s, l[i - 1], l[i - 2])
            if l[i] < s:
                up, s, ep, af = False, ep, l[i], start
            elif h[i] > ep:
                ep = h[i]; af = min(af + inc, mx)
        else:
            s = max(s, h[i - 1], h[i - 2])
            if h[i] > s:
                up, s, ep, af = True, ep, h[i], start
            elif l[i] < ep:
                ep = l[i]; af = min(af + inc, mx)
        out[i] = s
    return out


def sar(h, l, start=0.02, inc=0.02, mx=0.2):
    return pd.Series(_sar(S(h).values.astype(float), S(l).values.astype(float), start, inc, mx), index=S(h).index)


def pivothigh(h, left, right):
    """TV 一样：在第 i 根时，确认 right 根之前那根是高点（只用到当前为止的数据）"""
    h = S(h); w = left + right + 1
    m = h.rolling(w).max()
    c = h.shift(right)
    return c.where(c == m)


def pivotlow(l, left, right):
    l = S(l); w = left + right + 1
    m = l.rolling(w).min()
    c = l.shift(right)
    return c.where(c == m)


def barssince(cond):
    c = S(cond).fillna(False).values.astype(bool)
    out = np.full(len(c), np.nan); last = -1
    for i in range(len(c)):
        if c[i]:
            last = i
        out[i] = i - last if last >= 0 else np.nan
    return pd.Series(out, index=S(cond).index)


def valuewhen(cond, src, occ=0):
    s = S(src).where(S(cond).fillna(False).astype(bool))
    return s.ffill() if occ == 0 else s.dropna().shift(occ).reindex(s.index).ffill()


def linreg(x, n, off=0):
    """TV ta.linreg：窗口内最小二乘直线在 (n-1-off) 位置的值"""
    n = int(n); s = S(x); y = s.values.astype(float)
    t = np.arange(n, dtype=float)
    sy = _conv(y, np.ones(n)); sty = _conv(y, t)
    st, stt = t.sum(), (t * t).sum()
    b = (n * sty - st * sy) / (n * stt - st * st)
    a = (sy - b * st) / n
    return pd.Series(a + b * (n - 1 - off), index=s.index)


def vwap_day(df):
    """按 UTC 自然日重置的 VWAP（TV 默认 session 锚点），用 hlc3"""
    tp = (df.h + df.l + df.c) / 3
    day = (df.t.values // 86_400_000)
    g = pd.Series(day, index=df.index)
    pv = (tp * df.v).groupby(g.values).cumsum(); vv = df.v.groupby(g.values).cumsum()
    return pv / vv


def heikin(df):
    c = (df.o + df.h + df.l + df.c) / 4
    o = np.empty(len(df)); o[0] = (df.o.iloc[0] + df.c.iloc[0]) / 2
    cv = c.values
    for i in range(1, len(df)):
        o[i] = (o[i - 1] + cv[i - 1]) / 2
    o = pd.Series(o, index=df.index)
    return o, pd.concat([df.h, o, c], axis=1).max(axis=1), pd.concat([df.l, o, c], axis=1).min(axis=1), c
