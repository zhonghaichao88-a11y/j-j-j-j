"""清洗接盘的镜像：急涨 + 持仓大降（空单被清掉）+ 现货主动卖 → 做空"""
import numpy as np, pandas as pd
import sim
from data import flow
from s16_bearshort import bear_days
NAME = '反向清洗（做空）：急涨 + 空单被清 + 现货在卖'
RULES = ('事先定好的规则（看结果前写好，和清洗接盘完全镜像）：1 小时涨超 rise、持仓量 1 小时降超 5%、币安现货 1 小时主动卖比买多 5% → '
         '收盘价挂卖单 1 根内成交；止损 = 3 倍涨幅；拿 12 小时。bear=1 时只在 BTC 昨天收盘低于 200 天均线时做（镜像清洗接盘的大盘过滤）。')
GRID = [{'rise': r, 'bear': b} for r in (0.02, 0.03) for b in (0, 1)]


def prep(df):
    df['r60'] = df.c.pct_change(12)
    df['oi60'] = df.oi.pct_change(12)
    df['sf60'] = flow(df.sbq, df.sqv, 12)
    df['bear'] = pd.Series(df.index.values // 86_400_000, index=df.index).map(bear_days()).fillna(False).astype(bool)
    return df


def trades(df, p):
    m = (df.r60 > p['rise']) & (df.oi60 < -0.05) & (df.sf60 < -0.05)
    if p['bear']:
        m &= df.bear
    sig = np.flatnonzero(m.fillna(False).values)
    return sim.run(df, sig, -1, 3 * df.r60.abs().values[sig], hold=144, entry='limit', lpx=df.c.values[sig], lbars=1)
