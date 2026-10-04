"""订单流做空第三轮（看结果前写好规则和参数网格；只用 2024 年挑参数，过关标准不放松）。
R1 清洗没人接：1 小时跌超 2%、持仓量 1 小时降超 5%（多单被清），但现货 1 小时也在主动卖（卖比买多 5%）→ 做空（清洗接盘的另一半：没人接）
R2 反弹无力：24 小时跌超 8%，最近 4 小时从低点反弹超 3%，反弹这 4 小时持仓量在涨（≥3%）而现货在卖 → 做空
R3 现货在跑、合约在抄底：24 小时跌超 3%，现货 24 小时主动卖比买多 8%，合约 24 小时主动买多于卖 → 做空
R4 多头摊平：24 小时跌超 5%，持仓量 24 小时涨超 5%，资金费率仍为正（多单还在付钱）→ 做空
R5 追高被套：最近 4 小时内创过 24 小时新高、追高那段合约主动买 > 5% 且持仓涨 > 3%，现在比高点回落超 h，持仓量还没降（4 小时变化 ≥ 0）→ 做空
下一根市价进场；止损固定 sd；拿 hold 根 5 分钟K线；同一个币拿着时不再开。bear=1 时只在 BTC 昨收低于 200 天均线时做。"""
import numpy as np, pandas as pd
import sim
from data import flow
from s16_bearshort import bear_days
NAME = '订单流做空第三轮（清洗没人接 / 反弹无力 / 现货在跑 / 多头摊平 / 追高被套）'
RULES = __doc__
GRID = ([{'k': k, 'sd': sd, 'hold': h, 'bear': b} for k in ('R1', 'R2', 'R3', 'R4') for sd in (0.05, 0.10) for h in (144, 288) for b in (0, 1)] +
        [{'k': 'R5', 'h': x, 'sd': sd, 'hold': h, 'bear': b} for x in (0.03, 0.06) for sd in (0.05, 0.10) for h in (144, 288) for b in (0, 1)])


def prep(df):
    df['r60'] = df.c.pct_change(12)
    df['r24h'] = df.c.pct_change(288)
    df['oi60'] = df.oi.pct_change(12)
    df['oi4h'] = df.oi.pct_change(48)
    df['oi24h'] = df.oi.pct_change(288)
    df['sf60'] = flow(df.sbq, df.sqv, 12)
    df['sf4h'] = flow(df.sbq, df.sqv, 48)
    df['sf24h'] = flow(df.sbq, df.sqv, 288)
    df['pf24h'] = flow(df.bq, df.qv, 288)
    df['pf4h_prev'] = flow(df.bq, df.qv, 48).shift(48)
    df['lo4h'] = df.l.rolling(48).min()
    df['hi24'] = df.h.rolling(288).max()
    df['hi4'] = df.h.rolling(48).max()
    df['oi4h_prev'] = df.oi.pct_change(48).shift(48)
    df['bear'] = pd.Series(df.index.values // 86_400_000, index=df.index).map(bear_days()).fillna(False).astype(bool)
    return df


def trades(df, p):
    k = p['k']
    if k == 'R1':
        m = (df.r60 < -0.02) & (df.oi60 < -0.05) & (df.sf60 < -0.05)
    elif k == 'R2':
        m = (df.r24h < -0.08) & (df.c / df.lo4h - 1 > 0.03) & (df.oi4h > 0.03) & (df.sf4h < 0)
    elif k == 'R3':
        m = (df.r24h < -0.03) & (df.sf24h < -0.08) & (df.pf24h > 0)
    elif k == 'R4':
        m = (df.r24h < -0.05) & (df.oi24h > 0.05) & (df.fund > 0)
    else:
        m = (df.hi4 >= df.hi24) & (df.pf4h_prev > 0.05) & (df.oi4h_prev > 0.03) & (df.c < df.hi4 * (1 - p['h'])) & (df.oi4h >= 0)
    if p['bear']:
        m &= df.bear
    sig = np.flatnonzero(m.fillna(False).values)
    return sim.run(df, sig, -1, p['sd'], hold=p['hold'], entry='market')
