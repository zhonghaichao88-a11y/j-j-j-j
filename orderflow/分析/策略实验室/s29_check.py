"""选中：c=ls, fr=2%, fo=3%。随机对照：同一个币"散户偏多 + 资金费 > 0"的时候随机挑时间做空，同样的清洗平仓和止损。"""
import numpy as np, pandas as pd, data, report
import s28_short4y as B, s29_short4y_refine as S
P = {'c': 'ls', 'fr': 0.02, 'fo': 0.03}
z = lambda s: (s - s.rolling(288 * 7, min_periods=288).mean()) / s.rolling(288 * 7, min_periods=288).std()
old = open('/home/user/ext/oos/flush_old_coins.txt').read().split(); new = data.coins()
T, R = [], []; rng = np.random.default_rng(3)
for root, coins in (('/home/user/ext/oos/f5', old), ('/home/user/ext/long', new)):
    for c, df in B.load_set(root, coins):
        df['ls_z'] = z(df.ls)
        tr = S.run(df, P, c); T += tr
        cand = np.flatnonzero(((df.ls_z > 0) & (df.fund > 0)).fillna(False).values[:-600])
        if tr and len(cand):
            m = np.zeros(len(df), bool); m[rng.choice(cand, size=min(len(cand), 3 * len(tr)), replace=False)] = True
            R += B.run(df.assign(r60=df.r60, oi60=df.oi60), m, dict(e='E1', o=0.05, x='X2', sd=0.05), c)
cols = ['coin', 't', 'ret', 'why', 'bars']
T, R = pd.DataFrame(T, columns=cols), pd.DataFrame(R, columns=cols); pf = report.pf
for nm, a, b in B.SEG:
    ta, tb = pd.Timestamp(a).value // 10**6, pd.Timestamp(b).value // 10**6
    X, Y = T[(T.t >= ta) & (T.t < tb)], R[(R.t >= ta) & (R.t < tb)]
    print(nm, '策略', len(X), '笔 PF', round(pf(X.ret), 2), '| 随机', len(Y), '笔 PF', round(pf(Y.ret), 2))
print('全部 策略 PF', round(pf(T.ret), 2), '随机 PF', round(pf(R.ret), 2), '| 胜率', round((T.ret > 0).mean(), 2), '每笔基点', round(T.ret.mean() * 1e4, 1),
      '持仓中位小时', round(T.bars.median() / 12, 1), T.why.value_counts(normalize=True).round(2).to_dict())
T2 = T.rename(columns={'t': 't_in'}); T2['t'] = T2.t_in; T2['t_out'] = T2.t_in + T2.bars * 300_000
for s in (0.05, 0.10):
    print(f'100U 每笔{s:.0%}', report.portfolio(T2, size=s))
T.to_parquet('results/s29_pick.parquet')
