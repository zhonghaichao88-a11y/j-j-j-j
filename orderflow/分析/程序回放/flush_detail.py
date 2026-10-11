"""清洗接盘 全部币补测的详细表：几种选币方式 × 每年（笔数、胜率、PF、每笔平均、当年 100U 变成、回撤）。"""
import sys, os, numpy as np, pandas as pd; sys.argv = ['x']
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compare as C, run_all
from itemsets import evaluate
Q = pd.read_parquet('/home/user/ext/more/daily_qv.parquet'); Q['day'] = (Q.ts // 86400000 * 86400000).astype(np.int64)
Q = Q.groupby(['day', 'coin']).quote_volume.sum().reset_index(); Q['rank'] = Q.groupby('day').quote_volume.rank(ascending=False)
first = Q.groupby('coin').day.min()
P, _ = C.program('flush'); P = P[P.t_in >= C.ms('2021-12-01')].copy()
P['day'] = (P.t_in // 86400000 * 86400000 - 86400000).astype(np.int64); P = P.merge(Q[['day', 'coin', 'rank']], on=['day', 'coin'], how='left')
P['age'] = (P.t_in - P.coin.map(first)) / 86400000; old = set(run_all.coins())


def row(x):
    x = x.sort_values('t_in'); cid = x.coin.astype('category').cat.codes.values.astype(np.int64)
    d = ((x.t_out - x.t_in) // 300000 + 1).values.astype(np.int64)
    n, win, gp, gl, p1, l1, p2, l2, eq, dd = evaluate(x.t_in.values.astype(np.int64), cid, x.ret.values.astype(float), d, int(cid.max()) + 1)
    return f'| {n} | {win / n:.0%} | {gp / gl:.2f} | {(gp - gl) / n * 100:+.2f}% | {eq:.0f} | {-dd * 100:.0f}% |'


SETS = {'A. 全部币（441 个）': P, 'B. 原来那 148 个': P[P.coin.isin(old)], 'C. 新补的币': P[~P.coin.isin(old)],
        'D. 成交额前 150（你现在的选币）': P[P['rank'] <= 150], 'E. 成交额前 150 + 上线满 1 年（新默认）': P[(P['rank'] <= 150) & (P.age >= 365)],
        'F. 成交额前 50': P[P['rank'] <= 50]}
out = []
for nm, x in SETS.items():
    out += [f'### {nm}：{x.coin.nunique()} 个币', '| 时间 | 笔数 | 胜率 | PF | 每笔平均 | 100U 变成 | 最大回撤 |', '|---|---|---|---|---|---|---|', '| **全部** ' + row(x)]
    for y, g in x.groupby(pd.to_datetime(x.t_in, unit='ms').dt.year):
        out.append(f'| {y} ' + row(g))
    out.append('')
txt = '\n'.join(out); print(txt); open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '清洗接盘_全部币详细.md'), 'w').write('# 清洗接盘 全部币补测（程序回放）\n每笔 10%、最多 10 单、同币不重叠；"全部"那行从 100U 一直滚到 2026-09，分年那几行每年从 100U 重新算。\n\n' + txt)
