import numpy as np, pandas as pd, sim, data
from data import flow
NAME = '熊市专用做空（BTC 在 200 天均线下方才做）'
RULES = ('事先定好的规则（没看结果之前写的）。只在 BTC 昨天收盘低于 200 天均线的日子出信号，全部只做空，下一根开盘市价进场，同一个币拿着时不再开。\n'
         'A 拉高没人接：1 小时涨超 a、持仓量 1 小时在涨（杠杆在推）、币安现货 1 小时主动卖多于买 → 做空；止损 = 涨幅 × k。\n'
         'B 跌破：收盘跌破过去 24 小时最低价、合约 1 小时主动卖超 5% → 做空；止损固定 sd。\n'
         'C 反弹到均线被压：币本身在 20 日均线下方，这根最高碰到均线但收在均线下方 → 做空；止损固定 sd。\n'
         'D 多头太挤：资金费率 > 0.03%（多单付钱）且 24 小时在涨 → 做空；止损固定 sd。\n'
         '拿 hold 根 5 分钟K线到时间平。挑参数只用 2026 年以前的熊市日子，2026 年的熊市日子当考试。')
GRID = ([{'k': 'A', 'a': a, 'm': m, 'hold': h} for a in (0.02, 0.04) for m in (1.0, 2.0) for h in (48, 144)] +
        [{'k': 'B', 'sd': sd, 'hold': h} for sd in (0.03, 0.06) for h in (48, 144, 288)] +
        [{'k': 'C', 'sd': sd, 'hold': h} for sd in (0.03, 0.06) for h in (48, 144, 288)] +
        [{'k': 'D', 'sd': sd, 'hold': h} for sd in (0.05, 0.10) for h in (144, 288)])
_BEAR = None


def bear_days():
    global _BEAR
    if _BEAR is None:
        b = data.load('BTC')
        d = b.c.groupby(b.index.values // 86_400_000).last()
        ma = d.rolling(200).mean()
        _BEAR = ((d < ma) & ma.notna()).shift(1).fillna(False).astype(bool)
    return _BEAR


def prep(df):
    df['r60'] = df.c.pct_change(12)
    df['r24h'] = df.c.pct_change(288)
    df['oi60'] = df.oi.pct_change(12)
    df['sf60'] = flow(df.sbq, df.sqv, 12)
    df['pf60'] = flow(df.bq, df.qv, 12)
    df['lo24'] = df.l.rolling(288).min().shift(1)
    df['ema20d'] = df.c.ewm(span=288 * 20, min_periods=288 * 10).mean()
    df['bear'] = pd.Series(df.index.values // 86_400_000, index=df.index).map(bear_days()).fillna(False).astype(bool)
    return df


def trades(df, p):
    k = p['k']
    if k == 'A':
        m = (df.r60 > p['a']) & (df.oi60 > 0) & (df.sf60 < 0)
        sd = p['m'] * df.r60.abs().values
    elif k == 'B':
        m = (df.c < df.lo24) & (df.pf60 < -0.05)
        sd = p['sd']
    elif k == 'C':
        m = (df.c.shift(1) < df.ema20d.shift(1)) & (df.h >= df.ema20d) & (df.c < df.ema20d)
        sd = p['sd']
    else:
        m = (df.fund > 0.0003) & (df.r24h > 0)
        sd = p['sd']
    m = m & df.bear
    sig = np.flatnonzero(m.fillna(False).values)
    if not np.isscalar(sd):
        sd = sd[sig]
    return sim.run(df, sig, -1, sd, hold=p['hold'], entry='market')
