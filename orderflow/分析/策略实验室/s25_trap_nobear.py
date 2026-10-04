"""多头摊平做空 调试（第四轮，看结果前写好）。
基础（不加大盘过滤，任何时候都做）：24 小时跌超 d、持仓量 24 小时涨超 o、资金费率 > 0 → 下一根市价做空。
加一个订单流条件 x：none 不加 / pf 合约 24 小时主动卖多于买 / sf 现货 24 小时主动卖多于买 / low 价格还在 24 小时最低点 2% 以内（没反弹）
止损 sd，拿 hold 根 5 分钟K线。
新的划分（因为 2024 年熊市日子太少，只有 48 笔）：调参期 2024-01 ~ 2025-06，考试期 2025-07 ~ 2026-09（挑参数时不看）。
过关：考试期笔数 ≥ 50、PF ≥ 1.15、每笔为正；新币考试期 PF ≥ 1.1；最近 6 个月 PF ≥ 1；调参期也要赚。"""
import numpy as np, pandas as pd
import sim
from data import flow
from s21_ofshort import prep as _p
from s16_bearshort import bear_days
NAME = '多头摊平做空 调试（不加大盘过滤）'
RULES = __doc__
GRID = [{'d': d, 'o': o, 'x': x, 'sd': sd, 'hold': h} for d in (0.05, 0.08) for o in (0.05, 0.10)
        for x in ('none', 'pf', 'sf', 'low') for sd in (0.05, 0.10) for h in (144, 288)]


def prep(df):
    df = _p(df)
    df['sf24h'] = flow(df.sbq, df.sqv, 288)
    df['lo24'] = df.l.rolling(288).min()
    df['bear'] = pd.Series(df.index.values // 86_400_000, index=df.index).map(bear_days()).fillna(False).astype(bool)
    return df


def trades(df, p):
    m = (df.r24h < -p['d']) & (df.oi24h > p['o']) & (df.fund > 0)
    x = p['x']
    if x == 'pf':
        m &= df.pf24h < 0
    elif x == 'sf':
        m &= df.sf24h < 0
    elif x == 'low':
        m &= df.c <= df.lo24 * 1.02
    sig = np.flatnonzero(m.fillna(False).values)
    return sim.run(df, sig, -1, p['sd'], hold=p['hold'], entry='market')
