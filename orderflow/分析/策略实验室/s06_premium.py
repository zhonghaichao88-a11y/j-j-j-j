import numpy as np, sim, feat
NAME = '合约对现货溢价极端回归'
RULES = '出处：永续合约基差/溢价（资金费率机制）。溢价 = 合约价/现货价 - 1，7 天 z 分数 < -Z（合约太便宜）做多，> Z 做空；整点判断，拿 hold。'
GRID = [{'Z': z, 'hold': h} for z in (2.5, 3.5) for h in (48, 144)]


def prep(df):
    df['prem'] = (df.c / df.attrs['mult']) / df.sc - 1
    df['zp'] = feat.z(df.prem)
    return feat.add_basic(df)


def trades(df, p):
    hc = feat.hour_close(df)
    L = hc & (df.zp < -p['Z']).values
    S = hc & (df.zp > p['Z']).values
    sig = np.flatnonzero(L | S)
    return sim.run(df, sig, np.where(L[sig], 1, -1), feat.vol_stop(df, p['hold'])[sig], hold=p['hold'], entry='market')
