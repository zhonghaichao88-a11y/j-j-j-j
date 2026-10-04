import numpy as np, pandas as pd, runner, report, sim, data
import s26_trap_ofregime as S
P = {'d': 0.05, 'x': 'low', 'm': 'ls', 'sd': 0.1, 'hold': 144}
S.GRID = [P]
res, _, _ = runner.run('s26_trap_ofregime'); T = res[0]['T']
SPLIT = int(pd.Timestamp('2025-07-01').value // 10**6); pf = report.pf
print('笔数', len(T), 'PF', round(pf(T.ret), 2), '胜率', round((T.ret > 0).mean(), 2))
print(T.groupby(pd.to_datetime(T.t, unit='ms').dt.to_period('Q')).ret.agg(['size', lambda r: round(pf(r), 2)]).to_string())
cs = T.groupby('coin').ret.sum().sort_values(ascending=False)
print('去掉最赚 5 个币 PF', round(pf(T[~T.coin.isin(cs.index[:5])].ret), 2), list(cs.index[:5]))
print('组合 每笔 5%', report.portfolio(T, size=0.05)); print('组合 每笔 10%', report.portfolio(T, size=0.10))
rng = np.random.default_rng(1); R = []
for c, g in T.groupby('coin'):
    df = S.prep(data.load(c)); idx = np.flatnonzero((df.m_ls > 0).values[:-200])
    if len(idx): R += sim.run(df, np.sort(rng.choice(idx, size=min(len(idx), 5 * len(g)), replace=False)), -1, P['sd'], hold=P['hold'], entry='market', cooldown=1)
R = pd.DataFrame(R, columns=sim.COLS)
for nm, X, Y in (('调参期', R[R.t < SPLIT], T[T.t < SPLIT]), ('考试期', R[R.t >= SPLIT], T[T.t >= SPLIT])):
    print(nm, '随机做空', len(X), '笔 PF', round(pf(X.ret), 2), '| 策略', len(Y), '笔 PF', round(pf(Y.ret), 2))
