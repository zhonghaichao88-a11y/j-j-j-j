"""给已过关的打法找过滤条件：每笔进场前那个整点的特征，按训练期（2024）分三档，看每档在训练期和考试期的每笔收益。
只有训练期和考试期方向一致、差距明显的条件才算有用。"""
import numpy as np, pandas as pd
from data import TRAIN_END
X = pd.read_parquet('ml_hourly.parquet')
F = ['r24h', 'r3d', 'atr1h', 'vsurge', 'pf24h', 'oi24h', 'fund', 'fund_z', 'ls_z', 'tls_z', 'prem_z', 'btc_r24h', 'btc_r4h', 'rel24h', 'hour']
X = X[['ts', 'coin'] + F].sort_values('ts')
for name, path in (('清洗接盘', 'results/trades_flush.parquet'), ('轧空追多', 'results/trades_squeeze.parquet'), ('多日大清洗', 'results/trades_s07_dayflush_2.parquet')):
    T = pd.read_parquet(path).sort_values('t').rename(columns={'fund': 'fund_cost'})
    M = pd.merge_asof(T, X, left_on='t', right_on='ts', by='coin', direction='backward', tolerance=7_200_000)
    tr, te = M.t < TRAIN_END, M.t >= TRAIN_END
    print(f'\n=== {name}：训练 {tr.sum()} 笔 {M[tr].ret.mean()*1e4:+.0f} 基点，考试 {te.sum()} 笔 {M[te].ret.mean()*1e4:+.0f} 基点')
    for f in F:
        if M[f].notna().sum() < 100:
            continue
        q1, q2 = M.loc[tr, f].quantile([1/3, 2/3])
        g = np.where(M[f] <= q1, '低', np.where(M[f] <= q2, '中', '高'))
        g = pd.Series(g, index=M.index).where(M[f].notna())
        a = M[tr].groupby(g[tr]).ret.mean() * 1e4
        b = M[te].groupby(g[te]).ret.mean() * 1e4
        n = M[te].groupby(g[te]).size()
        if len(a) < 3 or len(b) < 3:
            continue
        # 训练期最差那档，在考试期是不是也最差（或者最好那档也最好）
        worst_tr, best_tr = a.idxmin(), a.idxmax()
        same = (b.idxmin() == worst_tr) or (b.idxmax() == best_tr)
        print(f'  {f:9s} 训练 低/中/高 {a.get("低",0):+5.0f} {a.get("中",0):+5.0f} {a.get("高",0):+5.0f} | 考试 {b.get("低",0):+5.0f} {b.get("中",0):+5.0f} {b.get("高",0):+5.0f} (笔数 {n.get("低",0)}/{n.get("中",0)}/{n.get("高",0)}) {"✔一致" if same else ""}')
