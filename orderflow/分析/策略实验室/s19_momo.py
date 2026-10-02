import numpy as np, sim
from data import flow
NAME = '追强势币（做多，不管大盘）'
RULES = ('事先定好的规则（看结果前写好）：币 24 小时涨超 a；最近 1 小时成交额 ≥ 平时（过去 7 天平均每小时）的 v 倍；最近 1 小时还在涨 → '
         '下一根 5 分钟K线开盘市价做多；止损固定 sd；不设止盈，拿 hold 根到时间平；同一个币 24 小时内只做一次。'
         '参数只用 2024 年挑，2025 年以后当考试，再看后加的 58 个币和最近 6 个月。')
GRID = [{'a': a, 'v': v, 'sd': sd, 'hold': h} for a in (0.10, 0.15, 0.20) for v in (2, 3) for sd in (0.10, 0.15) for h in (144, 288)]


def prep(df):
    df['r24h'] = df.c.pct_change(288)
    df['r60'] = df.c.pct_change(12)
    df['vsurge'] = df.qv.rolling(12).sum() / (df.qv.rolling(288 * 7, min_periods=288).mean() * 12)
    return df


def trades(df, p):
    m = (df.r24h > p['a']) & (df.vsurge >= p['v']) & (df.r60 > 0)
    return sim.run(df, np.flatnonzero(m.fillna(False).values), 1, p['sd'], hold=p['hold'], entry='market', cooldown=288)
