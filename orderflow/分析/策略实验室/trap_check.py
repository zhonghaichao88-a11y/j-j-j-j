"""挑出来的参数再查：随机做空对照（同样的熊市日子、同样的币、同样止损和持仓时间）、按季度、去掉最赚的币、组合回撤"""
import numpy as np, pandas as pd, runner, report, sim, data
import s24_trap_tune as S
P = {'d': 0.05, 'o': 0.05, 'x': 'low', 'sd': 0.05, 'hold': 144}
S.GRID = [P]
res, _, _ = runner.run('s24_trap_tune')
T = res[0]['T']
SPLIT = int(pd.Timestamp('2025-07-01').value // 10**6)
pf = report.pf
print('笔数', len(T), 'PF', round(pf(T.ret), 2), '胜率', round((T.ret > 0).mean(), 2))
q = T.groupby(pd.to_datetime(T.t, unit='ms').dt.to_period('Q')).ret.agg(['size', lambda r: round(pf(r), 2), lambda r: round(r.mean() * 1e4)])
print(q.to_string())
cs = T.groupby('coin').ret.sum().sort_values(ascending=False)
print('去掉最赚 5 个币 PF', round(pf(T[~T.coin.isin(cs.index[:5])].ret), 2), list(cs.index[:5]))
for size in (0.05, 0.10):
    print(f'组合 每笔 {size:.0%}', report.portfolio(T, size=size))
# 随机做空对照：每笔信号，换成同一个币、熊市日子里随机挑 5 个时间点做空，同样止损和持仓
rng = np.random.default_rng(1); R = []
for c, g in T.groupby('coin'):
    df = S.prep(data.load(c))
    bear_idx = np.flatnonzero(df.bear.values[:-200])
    if len(bear_idx) == 0: continue
    sig = np.sort(rng.choice(bear_idx, size=min(len(bear_idx), 5 * len(g)), replace=False))
    R += sim.run(df, sig, -1, P['sd'], hold=P['hold'], entry='market', cooldown=1)
R = pd.DataFrame(R, columns=sim.COLS)
for nm, X in (('全部', R), ('调参期', R[R.t < SPLIT]), ('考试期', R[R.t >= SPLIT])):
    print('随机做空', nm, len(X), '笔 PF', round(pf(X.ret), 2), '每笔基点', round(X.ret.mean() * 1e4, 1))
for nm, X in (('调参期', T[T.t < SPLIT]), ('考试期', T[T.t >= SPLIT])):
    print('策略    ', nm, len(X), '笔 PF', round(pf(X.ret), 2), '每笔基点', round(X.ret.mean() * 1e4, 1))
T.to_parquet('results/trap_trades.parquet')
