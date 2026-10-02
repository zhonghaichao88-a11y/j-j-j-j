import numpy as np, pandas as pd, sim
from s16_bearshort import prep as _p
NAME = '熊市里追强势币（做多）'
RULES = '只在 BTC 低于 200 天均线的日子：币 24 小时涨超 a、1 小时成交额是平时 3 倍以上、1 小时还在涨 → 下一根市价做多，拿 hold 根，止损 sd。'
GRID = [{'a': a, 'sd': sd, 'hold': h} for a in (0.10, 0.20, 0.40) for sd in (0.08, 0.15) for h in (12, 48, 288)]


def prep(df):
    df = _p(df)
    v1 = df.qv.rolling(12).sum()
    df['vsurge'] = v1 / (df.qv.rolling(288 * 7, min_periods=288).mean() * 12)
    return df


def trades(df, p):
    m = (df.r24h > p['a']) & (df.vsurge > 3) & (df.r60 > 0) & df.bear
    return sim.run(df, np.flatnonzero(m.fillna(False).values), 1, p['sd'], hold=p['hold'], entry='market', cooldown=288)
