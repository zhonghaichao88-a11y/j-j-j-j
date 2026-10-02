"""校验用：轧空追多，和程序里的一样"""
import numpy as np
import sim
NAME = '轧空追多 校验'
RULES = '1小时涨超3%、持仓1小时降超2% → 下一根市价做多；止损=涨幅；拿12小时'
GRID = [{'rise': .03, 'oi': .02}]


def prep(df):
    df['r60'] = df.c.pct_change(12)
    df['oi60'] = df.oi.pct_change(12)
    return df


def trades(df, p):
    m = (df.r60 > p['rise']) & (df.oi60 < -p['oi'])
    sig = np.flatnonzero(m.fillna(False).values)
    return sim.run(df, sig, 1, df.r60.values[sig], hold=144, entry='market')
