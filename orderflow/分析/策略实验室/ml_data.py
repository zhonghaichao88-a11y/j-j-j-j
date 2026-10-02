"""机器学习用的整点特征表（每个币每小时一行，只用当时及以前的数据）。
目标：下一根开盘进场、拿 12 小时（144 根）的收益（做多方向），以及拿 4 小时的收益。"""
import numpy as np, pandas as pd
from multiprocessing import Pool
import data, feat
from data import flow


def coin_table(c):
    df = data.load(c)
    df = feat.add_basic(df)
    o, cl = df.o, df.c
    f = pd.DataFrame(index=df.index)
    for n, k in (('r1h', 12), ('r4h', 48), ('r24h', 288), ('r3d', 864)):
        f[n] = cl.pct_change(k)
    f['atr1h'] = df.atr1h
    hi24, lo24 = df.h.rolling(288).max(), df.l.rolling(288).min()
    f['pos24'] = (cl - lo24) / (hi24 - lo24)
    f['vsurge'] = df.v1h / df.v1h.rolling(288 * 7, min_periods=288).mean()
    for n, k in (('pf1h', 12), ('pf4h', 48), ('pf24h', 288)):
        f[n] = flow(df.bq, df.qv, k)
    for n, k in (('sf1h', 12), ('sf4h', 48)):
        f[n] = flow(df.sbq, df.sqv, k)
    f['div1h'] = f.sf1h - f.pf1h
    sv, pv = df.sqv.rolling(48).sum(), df.qv.rolling(48).sum()
    share = sv / (sv + pv)
    f['spot_share_chg'] = share - share.rolling(288 * 7, min_periods=288).mean()
    for n, k in (('oi1h', 12), ('oi4h', 48), ('oi24h', 288)):
        f[n] = df.oi.pct_change(k)
    f['fund'] = df.fund
    f['fund_z'] = feat.z(df.fund, 288 * 30)
    f['ls_z'] = feat.z(df.ls)
    f['tls_z'] = feat.z(df.tls)
    f['prem_z'] = feat.z((cl / df.attrs['mult']) / df.sc - 1)
    hrs = (df.index.values // 3_600_000)
    f['hour'] = hrs % 24
    f['dow'] = ((hrs // 24) + 3) % 7                     # 1970-01-01 是周四
    # 目标：下一根开盘进场
    O = o.values
    C = cl.values
    nxt = np.r_[O[1:], np.nan]
    for n, k in (('y4h', 48), ('y12h', 144)):
        ex = np.r_[C[k:], np.full(k, np.nan)]
        f[n] = ex / nxt - 1
    hc = feat.hour_close(df)
    f = f[hc].copy()
    f['coin'] = c
    f['new'] = df.attrs['new']
    f['row'] = np.flatnonzero(hc)                          # 在 5 分钟表里的行号（回测进场用）
    return f.replace([np.inf, -np.inf], np.nan).astype({k: 'float32' for k in f.columns if f[k].dtype == 'float64'})


def build():
    cs = data.coins()
    with Pool(4) as p:
        parts = p.map(coin_table, cs)
    X = pd.concat(parts)
    X.index.name = 'ts'
    X = X.reset_index()
    btc = X[X.coin == 'BTC'][['ts', 'r1h', 'r4h', 'r24h', 'pf1h', 'oi1h']].rename(columns=lambda k: k if k == 'ts' else 'btc_' + k)
    X = X.merge(btc, on='ts', how='left')
    X['rel24h'] = X.r24h - X.btc_r24h
    X.to_parquet('/home/user/ext/long/lab/ml_hourly.parquet')
    print(X.shape, X.coin.nunique())


if __name__ == '__main__':
    build()
