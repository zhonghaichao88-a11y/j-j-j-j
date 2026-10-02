import numpy as np, sim, feat
NAME = '多日大清洗后做多'
RULES = '出处：清算级联逆向（"资金费率转负且持仓量降 20% 以上后做多"）。24 小时持仓量降超 oi、24 小时跌超 r、资金费率为负 → 做多，拿 hold，止损 1.5 倍 24 小时跌幅。'
GRID = [{'oi': o, 'r': .05, 'hold': h} for o in (.15, .20) for h in (288, 864)]


def prep(df):
    return feat.add_basic(df)


def trades(df, p):
    m = ((df.oi24h < -p['oi']) & (df.r24h < -p['r']) & (df.fund < 0)).fillna(False).values
    sig = np.flatnonzero(m)
    return sim.run(df, sig, 1, 1.5 * np.abs(df.r24h.values[sig]), hold=p['hold'], entry='market')
