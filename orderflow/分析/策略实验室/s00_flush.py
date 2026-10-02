"""校验用：清洗接盘（严格版），和程序里的一样"""
import numpy as np
import sim
from data import flow
NAME = '清洗接盘（现货）校验'
RULES = '1小时跌超2%、持仓1小时降超5%、币安现货1小时主动买超5% → 收盘价挂买单1根内成交；止损3倍跌幅；拿12小时'
GRID = [{'drop': .02, 'oi': .05, 'sf': .05}]


def prep(df):
    df['r60'] = df.c.pct_change(12)
    df['oi60'] = df.oi.pct_change(12)
    df['sf60'] = flow(df.sbq, df.sqv, 12)
    return df


def trades(df, p):
    m = (df.r60 < -p['drop']) & (df.oi60 < -p['oi']) & (df.sf60 > p['sf'])
    sig = np.flatnonzero(m.fillna(False).values)
    return sim.run(df, sig, 1, 3 * df.r60.abs().values[sig], hold=144, entry='limit', lpx=df.c.values[sig], lbars=1)
