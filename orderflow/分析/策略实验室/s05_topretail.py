import numpy as np, sim, feat
NAME = '大户和散户分歧时跟大户'
RULES = '出处：多空比分析（散户人数比 vs 大户持仓比）。7 天 z 分数：散户人数比 > a 且大户持仓比 < -b → 做空；反过来做多。整点判断，拿 hold，止损 2×每小时振幅×√小时。'
GRID = [{'a': a, 'b': b, 'hold': h} for a, b in ((1.5, .5), (2, 1)) for h in (144, 288)]


def prep(df):
    df['zls'] = feat.z(df.ls)
    df['ztls'] = feat.z(df.tls)
    return feat.add_basic(df)


def trades(df, p):
    hc = feat.hour_close(df)
    L = hc & (df.zls < -p['a']).values & (df.ztls > p['b']).values
    S = hc & (df.zls > p['a']).values & (df.ztls < -p['b']).values
    sig = np.flatnonzero(L | S)
    return sim.run(df, sig, np.where(L[sig], 1, -1), feat.vol_stop(df, p['hold'])[sig], hold=p['hold'], entry='market')
