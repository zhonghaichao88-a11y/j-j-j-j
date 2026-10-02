"""加过滤：只做 24 小时涨跌在训练期最低三分之一的信号（阈值只用训练期数据定）"""
import numpy as np, pandas as pd, report
from data import TRAIN_END
X = pd.read_parquet('ml_hourly.parquet')[['ts', 'coin', 'r24h', 'btc_r24h']].sort_values('ts')
out = {}
for name, path in (('清洗接盘', 'results/trades_flush.parquet'), ('轧空追多', 'results/trades_squeeze.parquet'), ('多日大清洗', 'results/trades_s07_dayflush_2.parquet')):
    T = pd.read_parquet(path).sort_values('t').rename(columns={'fund': 'fund_cost'})
    M = pd.merge_asof(T, X, left_on='t', right_on='ts', by='coin', direction='backward', tolerance=7_200_000)
    tr = M.t < TRAIN_END
    thr = M.loc[tr, 'r24h'].quantile(1 / 3)
    F = M[M.r24h <= thr]
    out[name] = (T, F)
    print(f'\n{name}：阈值 24 小时涨跌 ≤ {thr:+.1%}')
    for lab, D in (('不过滤', M), ('过滤后', F)):
        sp = report.split(D)
        print(f'  {lab}: 笔数 {len(D)} | 训练 {sp["训练2024"]} | 考试 {sp["考试2025+"]} | 新币PF {sp["新币"].get("PF")}')
        print(f'     组合 10%/10单 {report.portfolio(D, .10, 10)} | 5%/10单 {report.portfolio(D, .05, 10)}')
def merge(*ts):
    T = pd.concat(ts).sort_values('t_in'); keep, busy = [], {}
    for i, r in enumerate(T.itertuples()):
        if r.t_in >= busy.get(r.coin, 0): keep.append(i); busy[r.coin] = r.t_out
    return T.iloc[keep]
allraw = merge(*[v[0] for v in out.values()]); allf = merge(*[v[1] for v in out.values()])
for lab, D in (('三个都不过滤', allraw), ('三个都过滤', allf)):
    te = D[D.t >= TRAIN_END]
    print(f'\n{lab}: {len(D)} 笔；只看考试期 {report.stats(te)}')
    for s, c in ((.10, 10), (.05, 10), (.15, 10)):
        print(f'   全期 每笔{s:.0%}: {report.portfolio(D, s, c)}   只看考试期: {report.portfolio(te, s, c)}')
