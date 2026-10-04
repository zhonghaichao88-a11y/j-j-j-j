import sys, numpy as np, pandas as pd
sys.path.insert(0, '.')
import report
from data import TRAIN_END, NEW
A = pd.read_parquet('s30/all.parquet')
RECENT = report.RECENT
pf = report.pf
def st(r):
    r = np.asarray(r); return f'{len(r)}笔 胜{(r>0).mean():.0%} 均{r.mean()*1e4:+.0f}bp PF{pf(r):.2f}' if len(r) else '-'
res = []
for (side, tp, sl, hd), g in A.groupby(['side', 'tp', 'sl', 'hold']):
    for cost, fee in (('挂单进', MAKER := 0.0002), ('吃单进', 0.0007)):
        r = g.ret.values - fee
        lg = ~g.old.values
        tr = lg & (g.t.values < TRAIN_END); te = lg & (g.t.values >= TRAIN_END)
        res.append(dict(side=side, tp=tp, sl=sl, hold=hd, cost=cost, n_tr=tr.sum(), pf_tr=pf(r[tr]), bp_tr=r[tr].mean()*1e4,
                        pf_te=pf(r[te]), bp_te=r[te].mean()*1e4, pf_new=pf(r[lg & g.coin.isin(NEW).values]),
                        pf_rc=pf(r[lg & (g.t.values >= RECENT)]), pf_old=pf(r[g.old.values]), n_all=len(r), win=(r>0).mean()))
R = pd.DataFrame(res)
pd.set_option('display.width', 250); pd.set_option('display.max_rows', 400)
for side in (1, -1):
    for cost in ('挂单进', '吃单进'):
        X = R[(R.side == side) & (R.cost == cost)].sort_values('pf_tr', ascending=False)
        print('\n==', '做多(跌破24h低)' if side > 0 else '做空(涨破24h高)', cost, '按训练期排序前8 / 共', len(X))
        print(X.head(8).round(2).to_string(index=False))
        print('  所有组合 训练PF 中位', round(X.pf_tr.median(), 2), '最高', round(X.pf_tr.max(), 2), '| 考试PF 中位', round(X.pf_te.median(), 2), '最高', round(X.pf_te.max(), 2), '| 老数据 中位', round(X.pf_old.median(), 2), '最高', round(X.pf_old.max(), 2))
R.to_csv('s30/grid.csv', index=False)
