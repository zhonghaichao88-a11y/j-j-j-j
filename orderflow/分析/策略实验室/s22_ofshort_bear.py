"""第二轮（明确是第二轮：看过第一轮"跌+持仓增"最近半年熊市里 PF>1 才提出来，有挖数据的风险，过关标准不放松）。
和清洗接盘+大盘过滤对称：只在 BTC 昨天收盘低于 200 天均线时做。
S1b：24 小时跌超 d、24 小时持仓量涨超 o、合约 24 小时主动卖多于买、现货 24 小时也是主动卖多于买 → 下一根市价做空；止损 sd；拿 hold。"""
import numpy as np, pandas as pd
import sim
from data import flow
from s21_ofshort import prep as _p
from s16_bearshort import bear_days
NAME = '订单流做空第二轮：熊市里 跌+持仓增+合约和现货都在卖'
RULES = __doc__
GRID = [{'d': d, 'o': o, 'sd': sd, 'hold': h, 'spot': s} for d in (0.03, 0.06) for o in (0.05, 0.10) for sd in (0.05, 0.10) for h in (144, 288) for s in (0, 1)]


def prep(df):
    df = _p(df)
    df['sf24h'] = flow(df.sbq, df.sqv, 288)
    df['bear'] = pd.Series(df.index.values // 86_400_000, index=df.index).map(bear_days()).fillna(False).astype(bool)
    return df


def trades(df, p):
    m = (df.r24h < -p['d']) & (df.oi24h > p['o']) & (df.pf24h < 0) & df.bear
    if p['spot']:
        m &= df.sf24h < 0
    sig = np.flatnonzero(m.fillna(False).values)
    return sim.run(df, sig, -1, p['sd'], hold=p['hold'], entry='market')
