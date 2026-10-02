import numpy as np, sim
from data import flow
NAME = '连续爆仓（用户版）：连续几根多单爆仓做空 / 连续几根空单爆仓做多，止损放宽，短线'
RULES = ('事先定好的规则（没看结果之前写的）。一根"多单爆仓K线" = 5 分钟跌超 a、持仓量这 5 分钟降超 0.3%、主动卖为主；'
         '"空单爆仓K线"反过来（涨超 a、持仓量降、主动买为主）。连续 n 根都是 → 下一根开盘市价进场。'
         'mode=顺势：多单爆仓做空、空单爆仓做多（用户的想法）；mode=反向：多单爆仓做多、空单爆仓做空（对照）。'
         '止损固定 sd（放宽到 2% / 4%），不设止盈，拿 hold 根 5 分钟K线到时间平。同一个币 1 小时内只做一次。')
GRID = [{'src': src, 'mode': m, 'a': a, 'n': n, 'sd': sd, 'hold': h}
        for src in ('多单爆', '空单爆') for m in ('顺势', '反向') for a in (0.005, 0.01) for n in (2, 3)
        for sd in (0.02, 0.04) for h in (3, 12, 48)]


def prep(df):
    df['r5'] = df.c.pct_change(1)
    df['oi5'] = df.oi.pct_change(1)        # 币安持仓快照：时间戳 = K线开始，数值是收盘时的（已核对），收盘时已知
    df['pf5'] = flow(df.bq, df.qv, 1)
    return df


def trades(df, p):
    if p['src'] == '多单爆':
        one = (df.r5 < -p['a']) & (df.oi5 < -0.003) & (df.pf5 < 0)
        side = -1 if p['mode'] == '顺势' else 1
    else:
        one = (df.r5 > p['a']) & (df.oi5 < -0.003) & (df.pf5 > 0)
        side = 1 if p['mode'] == '顺势' else -1
    run = one.astype(int).rolling(p['n']).sum() >= p['n']
    sig = np.flatnonzero(run.fillna(False).values)
    return sim.run(df, sig, side, p['sd'], hold=p['hold'], entry='market', cooldown=12)
