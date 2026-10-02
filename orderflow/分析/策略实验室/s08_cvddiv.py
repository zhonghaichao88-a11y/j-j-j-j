import numpy as np, sim, feat
from data import flow
NAME = '1 小时级 CVD 背离'
RULES = '出处：订单流课程常见"价格新高、主动买没跟上"。整点：收盘价创 24 小时新高但过去 24 小时主动买卖差为负 → 做空；创新低但主动买卖差为正 → 做多。flow 用合约或币安现货。'
GRID = [{'f': f, 'hold': h} for f in ('perp', 'spot') for h in (48, 144)]


def prep(df):
    df = feat.add_basic(df)
    df['hi24'] = df.c >= df.c.rolling(288).max()
    df['lo24'] = df.c <= df.c.rolling(288).min()
    df['pf24'] = flow(df.bq, df.qv, 288)
    df['sf24'] = flow(df.sbq, df.sqv, 288)
    return df


def trades(df, p):
    hc = feat.hour_close(df)
    f = (df.pf24 if p['f'] == 'perp' else df.sf24).values
    S = hc & df.hi24.values & (f < 0)
    L = hc & df.lo24.values & (f > 0)
    sig = np.flatnonzero(L | S)
    return sim.run(df, sig, np.where(L[sig], 1, -1), feat.vol_stop(df, p['hold'])[sig], hold=p['hold'], entry='market')
