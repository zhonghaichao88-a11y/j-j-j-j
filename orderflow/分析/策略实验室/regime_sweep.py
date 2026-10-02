import sys; sys.path.insert(0, '.')
import numpy as np, pandas as pd, data, report
b = data.load('BTC'); d = b.c.groupby(b.index.values // 86_400_000).last()
T = pd.read_parquet('results/trades_s00_flush_all.parquet')
day = T.t // 86_400_000
for N in (120,150,180,200,220,250):
    bull = (d > d.rolling(N).mean()).shift(1)
    ok = day.map(bull).fillna(False).astype(bool)
    sp = report.split(T[ok]); spo = report.split(T[~ok & day.map(bull).notna()])
    g = lambda s,k: (s[k].get('笔数'), s[k].get('PF'))
    print(N, '多头:', g(sp,'训练2024'), g(sp,'考试2025+'), g(sp,'最近6个月'), '| 空头:', g(spo,'考试2025+'), g(spo,'最近6个月'))
# 按季度看 MA200
bull = (d > d.rolling(200).mean()).shift(1); ok = day.map(bull).fillna(False).astype(bool)
q = pd.to_datetime(T.t, unit='ms').dt.to_period('Q')
pf = lambda x: x[x>0].sum()/-x[x<0].sum()
print(T[ok].groupby(q[ok]).ret.agg(['count', pf, 'mean']))
print(T[~ok].groupby(q[~ok]).ret.agg(['count', pf, 'mean']))
