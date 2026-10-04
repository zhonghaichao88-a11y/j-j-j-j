import numpy as np, pandas as pd, runner, report, sim, data
import s27_trap_more as S
SPLIT = int(pd.Timestamp('2025-07-01').value // 10**6); pf = report.pf
for P in ({'c': 'tls', 'o': 0.03, 'hold': 288}, {'c': 'none', 'o': 0.03, 'hold': 288}):
    S.GRID = [P]
    res, _, _ = runner.run('s27_trap_more'); T = res[0]['T']
    rng = np.random.default_rng(1); R = []
    for c, g in T.groupby('coin'):
        df = S.prep(data.load(c))
        ok = (df.m_ls > 0) & ((df.tls_z < 0) if P['c'] == 'tls' else True)      # 同样的市场状态（和同样的大户条件），随机挑时间
        idx = np.flatnonzero(ok.fillna(False).values[:-300])
        if len(idx):
            R += sim.run(df, np.sort(rng.choice(idx, size=min(len(idx), 5 * len(g)), replace=False)), -1, 0.10, hold=P['hold'], entry='market', cooldown=1)
    R = pd.DataFrame(R, columns=sim.COLS)
    print('==', P, len(T), '笔，胜率', round((T.ret > 0).mean(), 2), '每笔基点', round(T.ret.mean() * 1e4, 1))
    for nm, X, Y in (('前段', R[R.t < SPLIT], T[T.t < SPLIT]), ('后段', R[R.t >= SPLIT], T[T.t >= SPLIT])):
        print('  ', nm, '随机做空', len(X), '笔 PF', round(pf(X.ret), 2), '| 策略', len(Y), '笔 PF', round(pf(Y.ret), 2))
    print('   季度PF', T.groupby(pd.to_datetime(T.t, unit='ms').dt.to_period('Q')).ret.apply(lambda r: round(pf(r), 2)).to_dict())
    print('   100U 每笔5%', report.portfolio(T, size=0.05), '每笔10%', report.portfolio(T, size=0.10))
