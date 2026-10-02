import numpy as np, sim, feat
NAME = '持仓量确认的突破'
RULES = '出处：OI 分析"价格突破 + 持仓量增加 = 新钱入场"。整点：收盘突破之前 24 小时最高价，1 小时持仓量涨超 O，合约主动买卖差 > 10%，现货也在买（没现货就不看）→ 做多；跌破 24 小时最低反过来做空。'
GRID = [{'O': o, 'hold': h} for o in (.02, .04) for h in (144, 288)]


def prep(df):
    df = feat.add_basic(df)
    df['high24'] = df.h.rolling(288).max().shift(12)
    df['low24'] = df.l.rolling(288).min().shift(12)
    return df


def trades(df, p):
    hc = feat.hour_close(df)
    sf = df.sf60.values
    nos = np.isnan(sf)
    L = hc & (df.c > df.high24).values & (df.oi60 > p['O']).values & (df.pf60 > .1).values & (nos | (sf > 0))
    S = hc & (df.c < df.low24).values & (df.oi60 > p['O']).values & (df.pf60 < -.1).values & (nos | (sf < 0))
    sig = np.flatnonzero(L | S)
    return sim.run(df, sig, np.where(L[sig], 1, -1), feat.vol_stop(df, p['hold'])[sig], hold=p['hold'], entry='market')
