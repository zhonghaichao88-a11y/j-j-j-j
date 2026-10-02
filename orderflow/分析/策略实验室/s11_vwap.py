import numpy as np, sim, feat
NAME = 'VWAP 偏离 K 倍标准差回归'
RULES = '出处：VWAP 标准差带均值回归。UTC 每天重算 VWAP 和成交量加权标准差，开盘 2 小时后，价格低于 VWAP-K×σ 做多、高于 +K×σ 做空，目标回到 VWAP，止损再多 1 个 σ，最晚当天结束平。'
GRID = [{'K': k} for k in (2, 2.5, 3)]


def prep(df):
    day, starts = feat.day_index(df)
    tp = ((df.h + df.l + df.c) / 3).values
    v = df.qv.fillna(0).values
    seg = np.repeat(np.arange(len(starts)), np.diff(np.r_[starts, len(day)]))
    def cum(x):
        cs = np.cumsum(x)
        return cs - np.r_[0, cs][starts][seg]
    cv, cpv, cp2v = cum(v), cum(tp * v), cum(tp * tp * v)
    with np.errstate(invalid='ignore', divide='ignore'):
        vw = cpv / cv
        sd = np.sqrt(np.maximum(cp2v / cv - vw * vw, 0))
    df['vw'], df['vsd'] = vw, sd
    df['end'] = np.r_[starts[1:], len(day)][seg] - 1
    return df


def trades(df, p):
    c, vw, s = df.c.values, df.vw.values, df.vsd.values
    ok = (feat.mod(df) >= 120) & (s > 0)
    L = ok & (c < vw - p['K'] * s)
    S = ok & (c > vw + p['K'] * s)
    sig = np.flatnonzero(L | S)
    side = np.where(L[sig], 1, -1)
    td = np.abs(vw[sig] / c[sig] - 1)
    sd = np.maximum(s[sig] / c[sig], 0.003)
    hold = df.end.values[sig] - sig
    keep = hold >= 6
    return sim.run(df, sig[keep], side[keep], sd[keep], td[keep], hold=hold[keep], entry='market', cooldown=24)
