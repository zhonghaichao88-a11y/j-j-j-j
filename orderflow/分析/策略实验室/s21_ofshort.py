"""订单流做空，三种（看结果前写好规则和参数网格；只用 2024 年挑参数）：
S1 跌 + 持仓增（新空单进场，延续）：24 小时跌超 d、24 小时持仓量涨超 o、合约 24 小时主动卖多于买 → 下一根市价做空
S2 杠杆推涨、现货不跟：1 小时涨超 r、1 小时持仓量涨超 2%、现货 1 小时主动卖多于买、合约对现货溢价 7 天 z > 2 → 下一根市价做空
S3 溢价极端 + 杠杆堆积：合约对现货溢价 7 天 z > z0、24 小时持仓量涨超 10% → 下一根市价做空
止损固定 sd；拿 hold 根 5 分钟K线到时间平；同一个币拿着时不再开。"""
import numpy as np, pandas as pd
import sim
from data import flow
NAME = '订单流做空三种（跌+持仓增 / 杠杆推涨现货不跟 / 溢价极端）'
RULES = __doc__
GRID = ([{'k': 'S1', 'd': d, 'o': o, 'sd': sd, 'hold': h} for d in (0.03, 0.06) for o in (0.05, 0.10) for sd in (0.05, 0.10) for h in (144, 288)] +
        [{'k': 'S2', 'r': r, 'sd': sd, 'hold': h} for r in (0.02, 0.04) for sd in (0.05, 0.10) for h in (144, 288)] +
        [{'k': 'S3', 'z0': z, 'sd': sd, 'hold': h} for z in (2.0, 3.0) for sd in (0.05, 0.10) for h in (144, 288)])


def prep(df):
    df['r60'] = df.c.pct_change(12)
    df['r24h'] = df.c.pct_change(288)
    df['oi60'] = df.oi.pct_change(12)
    df['oi24h'] = df.oi.pct_change(288)
    df['pf24h'] = flow(df.bq, df.qv, 288)
    df['sf60'] = flow(df.sbq, df.sqv, 12)
    pr = df.c / df.sc - 1
    df['prem_z'] = (pr - pr.rolling(288 * 7, min_periods=288).mean()) / pr.rolling(288 * 7, min_periods=288).std()
    return df


def trades(df, p):
    k = p['k']
    if k == 'S1':
        m = (df.r24h < -p['d']) & (df.oi24h > p['o']) & (df.pf24h < 0)
    elif k == 'S2':
        m = (df.r60 > p['r']) & (df.oi60 > 0.02) & (df.sf60 < 0) & (df.prem_z > 2)
    else:
        m = (df.prem_z > p['z0']) & (df.oi24h > 0.10)
    sig = np.flatnonzero(m.fillna(False).values)
    return sim.run(df, sig, -1, p['sd'], hold=p['hold'], entry='market')
