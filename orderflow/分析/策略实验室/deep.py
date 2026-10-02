"""深挖：按季度、按天合并、去掉最好的几笔、不同仓位/同时持仓上限下的组合结果"""
import sys, importlib, json, numpy as np, pandas as pd
import runner, report
mod, k = sys.argv[1], int(sys.argv[2])
S = importlib.import_module(mod)
S.GRID = [S.GRID[k]]
res, errs, secs = runner.run(mod)
T = res[0]['T']
r = T.ret.sort_values(ascending=False)
print(mod, S.GRID[0], '笔数', len(T))
print(' 每笔', round(T.ret.mean() * 1e4, 1), '中位数', round(T.ret.median() * 1e4, 1), '去掉最好1%后', round(r.iloc[int(len(r) * .01):].mean() * 1e4, 1), '去掉最好5%后', round(r.iloc[int(len(r) * .05):].mean() * 1e4, 1))
q = T.groupby(pd.to_datetime(T.t, unit='ms').dt.to_period('Q')).ret.agg(['count', 'mean'])
print(' 各季度:', {str(a): (int(b['count']), int(b['mean'] * 1e4)) for a, b in q.iterrows()})
d = T.groupby(T.t // 86_400_000).ret.mean()
print(f' 按天合并: {len(d)} 天, 每天平均 {d.mean() * 1e4:+.1f} 基点, 赚钱天 {(d > 0).mean() * 100:.0f}%')
top = T.groupby(T.t // 86_400_000).ret.sum().sort_values(ascending=False)
print(' 最赚钱的 5 天占总利润:', round(top.head(5).sum() / T.ret.sum() * 100), '%', [str(pd.to_datetime(x * 86_400_000, unit='ms').date()) for x in top.head(5).index])
for size, cap in ((0.10, 10), (0.05, 10), (0.05, 5), (0.03, 10)):
    print(f' 组合 每笔{size:.0%} 最多{cap}单:', report.portfolio(T, size, cap))
T.to_parquet(f'/home/user/ext/long/lab/results/trades_{mod}_{k}.parquet')
