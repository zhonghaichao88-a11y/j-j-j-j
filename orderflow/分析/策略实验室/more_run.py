"""每组：两个时期 PF、新币、最近 6 个月、去掉最赚 5 个币；和候选（c=none, o=0.05, hold=144）比"""
import json, numpy as np, pandas as pd, runner, report
from data import NEW
mod = 's27_trap_more'
res, _, _ = runner.run(mod)
SPLIT = int(pd.Timestamp('2025-07-01').value // 10**6); pf = report.pf
rows = []
for r in res:
    T = r['T']
    if not len(T):
        continue
    a, b = T[T.t < SPLIT], T[T.t >= SPLIT]
    cs = T.groupby('coin').ret.sum().sort_values(ascending=False)
    q = T.groupby(pd.to_datetime(T.t, unit='ms').dt.to_period('Q')).ret.mean()
    rows.append({'参数': json.dumps(r['p']), '笔数': len(T), '前段PF': round(pf(a.ret), 2), '前段笔': len(a), '后段PF': round(pf(b.ret), 2), '后段笔': len(b),
                 '新币后段PF': round(pf(b[b.coin.isin(NEW)].ret), 2), '近6月PF': round(pf(T[T.t >= report.RECENT].ret), 2),
                 '去前5币PF': round(pf(T[~T.coin.isin(cs.index[:5])].ret), 2), '赚钱季度': f'{(q > 0).sum()}/{len(q)}',
                 '100U': report.portfolio(T, size=0.10).get('27个月后')})
D = pd.DataFrame(rows)
base = D[D['参数'] == json.dumps({'c': 'none', 'o': 0.05, 'hold': 144})].iloc[0]
D['两段都更好'] = (D['前段PF'] > base['前段PF']) & (D['后段PF'] > base['后段PF']) & (D['去前5币PF'] > base['去前5币PF']) & (D['前段笔'] >= 50) & (D['后段笔'] >= 50)
txt = D.sort_values(['两段都更好', '去前5币PF'], ascending=False).to_markdown(index=False)
open(f'/home/user/ext/long/lab/results/{mod}.md', 'w').write(txt)
print('候选：', base.to_dict())
print(D[D['两段都更好']].sort_values('去前5币PF', ascending=False).to_string(index=False))
