import numpy as np, sim, feat
from data import flow
NAME = '连续爆仓时跟着做（多单爆仓做空 / 空单爆仓做多，短线）'
RULES = ('用户提出：页面连续出现多单爆仓就做空；连续出现空单爆仓就做多。'
         '多单爆仓：5 分钟跌超 d、这 5 分钟持仓量降超 o、主动卖为主 → 下一根市价做空；'
         '空单爆仓：5 分钟涨超 d、持仓量降超 o、主动买为主 → 下一根市价做多。拿 hold 根（5 分钟一根），止损 = 2 倍这 5 分钟涨跌幅。')
GRID = [{'side': s, 'd': d, 'o': d, 'hold': h} for s in (-1, 1) for d in (.01, .015, .02) for h in (1, 3, 6, 12)]


def prep(df):
    df['r5'] = df.c.pct_change(1)
    df['oi5'] = df.oi.pct_change(1)        # 币安持仓快照的时间戳 = 这根K线开始，数值是收盘那一刻的（已用相关性核对），收盘时已知
    df['pf5'] = flow(df.bq, df.qv, 1)
    return df


def trades(df, p):
    if p['side'] == -1:
        m = (df.r5 < -p['d']) & (df.oi5 < -p['o']) & (df.pf5 < 0)
    else:
        m = (df.r5 > p['d']) & (df.oi5 < -p['o']) & (df.pf5 > 0)
    sig = np.flatnonzero(m.fillna(False).values)
    return sim.run(df, sig, p['side'], 2 * np.abs(df.r5.values[sig]), hold=p['hold'], entry='market', cooldown=12)
