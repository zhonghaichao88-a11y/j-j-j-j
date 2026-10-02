import importlib, numpy as np, pandas as pd, runner, report, data
from multiprocessing import Pool
THR = {'s00_flush': -0.047, 's00_squeeze': -0.128, 's07_dayflush': -0.137}   # 只用 2024 年定的阈值
def r24(c):
    df = data.load(c); return c, pd.Series(df.c.pct_change(288).values, index=df.index.values)
with Pool(4) as p: R = dict(p.map(r24, data.coins()))
for mod, k in (('s00_flush', 0), ('s00_squeeze', 0), ('s07_dayflush', 2)):
    S = importlib.import_module(mod); S.GRID = [S.GRID[k]]
    T = runner.run(mod)[0][0]['T']
    T['r24'] = [R[c].get(t, np.nan) for c, t in zip(T.coin, T.t)]
    F = T[T.r24 <= THR[mod]]
    for lab, D in (('不过滤', T), ('过滤', F)):
        sp = report.split(D)
        print(f"{mod} {lab}: 考试 {sp['考试2025+'].get('笔数')}笔 PF {sp['考试2025+'].get('PF')} | 最近6个月 {sp['最近6个月'].get('笔数')}笔 每笔 {sp['最近6个月'].get('每笔基点')} PF {sp['最近6个月'].get('PF')}")
    m = F.groupby(pd.to_datetime(F.t, unit='ms').dt.to_period('M')).ret.agg(['count', 'mean'])
    print('   过滤后最近几个月:', {str(a): (int(b['count']), int(b['mean'] * 1e4)) for a, b in m.tail(9).iterrows()})
    F.to_parquet(f'results/trades_{mod}_filtered.parquet'); T.to_parquet(f'results/trades_{mod}_all.parquet')
