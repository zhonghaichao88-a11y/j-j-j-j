import numpy as np, sim, feat
NAME = '日内动量（开头半小时预测结尾半小时）'
RULES = '出处：Shen, Urquhart, Wang《Bitcoin intraday time-series momentum》。utc：00:00-00:30 的涨跌方向 → 23:30 进场同方向，拿到 24:00；ny：13:30-14:00 → 19:30-20:00。涨跌幅要超过 thr。'
GRID = [{'s': 'utc', 'thr': 0}, {'s': 'utc', 'thr': .003}, {'s': 'ny', 'thr': 0}, {'s': 'ny', 'thr': .003}]


def prep(df):
    return df


def trades(df, p):
    m = feat.mod(df)
    day = df.index.values // 86_400_000
    O, C = df.o.values, df.c.values
    a0, a1, tr = (0, 25, 23 * 60 + 25) if p['s'] == 'utc' else (810, 835, 1165)
    i0 = {d: i for i, d in zip(np.flatnonzero(m == a0), day[m == a0])}
    i1 = {d: i for i, d in zip(np.flatnonzero(m == a1), day[m == a1])}
    sig, side = [], []
    for i, d in zip(np.flatnonzero(m == tr), day[m == tr]):
        if d in i0 and d in i1:
            r = C[i1[d]] / O[i0[d]] - 1
            if abs(r) > p['thr']:
                sig.append(i)
                side.append(1 if r > 0 else -1)
    return sim.run(df, sig, side, 0.05, hold=6, entry='market')
