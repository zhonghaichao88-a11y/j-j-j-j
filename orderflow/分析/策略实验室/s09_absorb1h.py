import numpy as np, sim, feat
from data import flow
NAME = '1 小时级吸收（放量、K线短、在极端价位）'
RULES = '出处：订单流"吸收"。1 小时成交 ≥ M 倍平时、振幅 ≤ 0.8 倍平时、创 24 小时新低、这小时主动卖多（买卖差 < -10%）但收在上半段 → 做多；新高反过来做空。止损在这根K线外 0.25 个振幅。'
GRID = [{'M': m, 'hold': h} for m in (2, 3) for h in (48, 144)]


def prep(df):
    df = feat.add_basic(df)
    df['avgv'] = df.v1h.rolling(288, min_periods=144).mean()
    df['rng'] = df.h1h - df.l1h
    df['avgr'] = df.rng.rolling(288, min_periods=144).mean()
    df['d1h'] = flow(df.bq, df.qv, 12)
    df['low24'] = df.l.rolling(288).min().shift(12)
    df['high24'] = df.h.rolling(288).max().shift(12)
    return df


def trades(df, p):
    hc = feat.hour_close(df)
    base = hc & (df.v1h >= p['M'] * df.avgv).values & (df.rng <= 0.8 * df.avgr).values
    mid = ((df.h1h + df.l1h) / 2).values
    c = df.c.values
    L = base & (df.l1h <= df.low24).values & (df.d1h < -0.1).values & (c >= mid)
    S = base & (df.h1h >= df.high24).values & (df.d1h > 0.1).values & (c <= mid)
    sig = np.flatnonzero(L | S)
    side = np.where(L[sig], 1, -1)
    rng = df.rng.values[sig]
    st = np.where(side == 1, df.l1h.values[sig] - 0.25 * rng, df.h1h.values[sig] + 0.25 * rng)
    sd = side * (c[sig] - st) / c[sig]
    return sim.run(df, sig, side, sd, hold=p['hold'], entry='market')
