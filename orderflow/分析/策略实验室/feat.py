"""各打法共用的特征（都只用当时及以前的数据）"""
import numpy as np, pandas as pd
from data import flow

H1, D1 = 12, 288


def mod(df):
    """每根K线开始时间在一天里的第几分钟（UTC）"""
    return (df.index.values // 60000) % 1440


def hour_close(df):
    """这根 5 分钟K线收盘正好是整点"""
    return ((df.index.values + 300_000) % 3_600_000) == 0


def add_basic(df):
    df['r60'] = df.c.pct_change(12)
    df['r24h'] = df.c.pct_change(288)
    df['oi60'] = df.oi.pct_change(12)
    df['oi24h'] = df.oi.pct_change(288)
    df['pf60'] = flow(df.bq, df.qv, 12)
    df['sf60'] = flow(df.sbq, df.sqv, 12)
    df['h1h'] = df.h.rolling(12).max()
    df['l1h'] = df.l.rolling(12).min()
    df['o1h'] = df.o.shift(11)
    df['v1h'] = df.qv.rolling(12).sum()
    df['rng1h'] = (df.h1h - df.l1h) / df.c
    df['atr1h'] = df.rng1h.rolling(288, min_periods=144).mean()      # 过去 24 小时平均每小时振幅（占价格比例）
    return df


def z(s, win=2016):
    m = s.rolling(win, min_periods=win // 2).mean()
    sd = s.rolling(win, min_periods=win // 2).std()
    return (s - m) / sd


def vol_stop(df, hold_bars, k=2.0):
    """按波动给止损：k × 每小时振幅 × √持有小时数"""
    return k * df.atr1h.values * np.sqrt(max(hold_bars / 12, 1))


def day_index(df):
    T = df.index.values
    day = T // 86_400_000
    starts = np.flatnonzero(np.r_[True, day[1:] != day[:-1]])
    return day, starts


def day_profiles(df, bin_frac=0.001, va=0.70):
    """每天的成交量分布（每根 5 分钟K线的成交额平均摊到它的高低价之间）→ {day: (poc, vah, val)}"""
    H, L, C, V = df.h.values, df.l.values, df.c.values, df.qv.values
    day, starts = day_index(df)
    out = {}
    ends = np.r_[starts[1:], len(day)]
    for a, b in zip(starts, ends):
        if b - a < 200 or not np.isfinite(C[a]) or C[a] <= 0:
            continue
        step = C[a] * bin_frac
        lo = np.floor(L[a:b] / step).astype(np.int64)
        hi = np.floor(H[a:b] / step).astype(np.int64)
        base = lo.min()
        size = int(hi.max() - base + 1)
        if size > 20000 or size <= 0:
            continue
        prof = np.zeros(size)
        for x, y, v in zip(lo - base, hi - base, V[a:b]):
            if v > 0:
                prof[x:y + 1] += v / (y - x + 1)
        tot = prof.sum()
        if tot <= 0:
            continue
        p = int(prof.argmax())
        i = j = p
        acc = prof[p]
        while acc < va * tot and (i > 0 or j < size - 1):
            up = prof[j + 1] if j < size - 1 else -1
            dn = prof[i - 1] if i > 0 else -1
            if up >= dn:
                j += 1
                acc += up
            else:
                i -= 1
                acc += dn
        out[int(day[a])] = ((base + p + 0.5) * step, (base + j + 1) * step, (base + i) * step)
    return out


def hourly(df):
    """不重叠的 1 小时K线（只要满 12 根的小时）。返回 DataFrame：o h l c qv bq，以及 end（这小时最后一根 5 分钟K线的行号）"""
    hid = df.index.values // 3_600_000
    g = pd.DataFrame({'hid': hid, 'o': df.o.values, 'h': df.h.values, 'l': df.l.values, 'c': df.c.values,
                      'qv': df.qv.values, 'bq': df.bq.values, 'pos': np.arange(len(df))})
    a = g.groupby('hid').agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'), qv=('qv', 'sum'),
                             bq=('bq', 'sum'), end=('pos', 'last'), n=('pos', 'size'))
    return a[a.n == 12].copy()
