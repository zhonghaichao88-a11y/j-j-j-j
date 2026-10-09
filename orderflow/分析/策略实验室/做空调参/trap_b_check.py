"""用和程序一样的挂单规则，复查 B（反弹2% + 4 小时 SAR 在上方 + ATR 止盈2倍止损3倍）和新找的 C（反弹2% + 离 24h 低点 >4% + 同样出场），
以及两个一起（SAR 在上方 且 离低点 >4%）。"""
import os, sys, numpy as np, pandas as pd
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trap_tune as T
import data, talib


def job(a):
    root, c = a
    data.ROOT = root
    if not os.path.exists(f'{root}/met/{c}.parquet'): return None
    try: df = data.load(c)
    except Exception: return None
    if len(df) < 20000 or df.oi.notna().sum() < 10000 or df.ls.notna().sum() < 10000: return None
    k = df[['o', 'h', 'l', 'c']].copy(); k.attrs = {}; k.index = pd.to_datetime(k.index, unit='ms')
    k4 = k.resample('4h', label='left', closed='left').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}).dropna()
    sar = pd.Series((talib.SAR(k4.h.values, k4.l.values) > k4.c.values).astype(float),
                    index=(k4.index + pd.Timedelta('4h')).values.astype('datetime64[ms]').astype(np.int64)).reindex(df.index, method='ffill').values
    out = []
    for nm, f in (('B SAR在上方', lambda i, d: sar[i] > 0.5), ('C 离低点>4%', lambda i, d: d > 0.04), ('B+C 都要', lambda i, d: sar[i] > 0.5 and d > 0.04)):
        r = T.walk(df, c, f)
        if r is not None: out.append(r.assign(mode=nm))
    return pd.concat(out) if out else None


if __name__ == '__main__':
    jobs = [(r, f[:-8]) for r in T.ROOTS for f in sorted(os.listdir(f'{r}/k')) if f.endswith('.parquet')]
    with Pool(4) as p: R = [x for x in p.map(job, jobs, chunksize=1) if x is not None]
    D = pd.concat(R).drop_duplicates(['coin', 'mode', 't']); D.to_parquet('/home/user/ext/nfisig/trap_bc.parquet')
    D = D[(D.t >= pd.Timestamp('2021-12-01').value // 10**6)]
    D['seg'] = np.select([D.t < pd.Timestamp('2024-01-01').value // 10**6, D.t < pd.Timestamp('2025-04-01').value // 10**6], [0, 1], 2)
    D['d'] = ((D.t_out - D.t_in) // 300_000 + 1).astype(np.int64)
    D = D.sort_values('t_in').reset_index(drop=True); D['cid'] = D.coin.astype('category').cat.codes.astype(np.int64); nc = int(D.cid.max()) + 1
    from itemsets import evaluate
    DAYS = [761, 455, 548]; out = [__doc__]
    for m, g in D.groupby('mode'):
        line = [m]
        for s in range(3):
            x = g[g.seg == s]; n, win, gp, gl, p1, l1, p2, l2, eq, dd = evaluate(x.t_in.values, x.cid.values, x.ret.values.astype(float), x.d.values, nc)
            line.append(f"{n}笔 每天{n / DAYS[s]:.2f} 胜{win / max(n, 1):.0%} PF{gp / gl:.2f} 组合{eq:.0f} 撤{-dd * 100:.0f}% 半{p1 / l1:.1f}/{p2 / l2:.1f}")
        out.append(' | '.join(line))
    txt = '\n'.join(out); print(txt); open(os.path.dirname(os.path.abspath(__file__)) + '/B复查.txt', 'w').write(txt)
