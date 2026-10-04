import sys, json, numpy as np, pandas as pd
import runner, report
from data import NEW
SPLIT = int(pd.Timestamp('2025-07-01').value // 10**6)
RECENT = report.RECENT
mod = sys.argv[1]
res, errs, secs = runner.run(mod)
st = lambda X: {'笔数': len(X), 'PF': round(report.pf(X.ret), 2) if len(X) else None, '每笔基点': round(X.ret.mean() * 1e4, 1) if len(X) else None}
rows = []
for r in res:
    T = r['T']
    tr, te = T[T.t < SPLIT], T[T.t >= SPLIT]
    a, b, n, rc = st(tr), st(te), st(te[te.coin.isin(NEW)]), st(T[T.t >= RECENT])
    ok = (b['笔数'] >= 50 and (b['PF'] or 0) >= 1.15 and (b['每笔基点'] or -1) > 0 and (n['PF'] or 0) >= 1.1
          and (rc['PF'] or 0) >= 1.0 and (a['每笔基点'] or -1) > 0)
    rows.append({'参数': json.dumps(r['p']), '调参笔数': a['笔数'], '调参PF': a['PF'], '考试笔数': b['笔数'], '考试PF': b['PF'],
                 '考试每笔': b['每笔基点'], '新币考试PF': n['PF'], '最近6月PF': rc['PF'], '过关': '✔' if ok else ''})
D = pd.DataFrame(rows)
txt = D.to_markdown(index=False)
cand = D[D['调参笔数'] >= 50].sort_values('调参PF', ascending=False)
pick = cand.iloc[0] if len(cand) else None
txt += '\n\n调参期挑出的（调参期笔数 ≥ 50 里 PF 最高）：\n' + (pick.to_string() if pick is not None else '没有')
txt += f"\n\n调参 PF>1 的组里，考试也 >1 的：{int(((D['调参PF']>1)&(D['考试PF']>1)).sum())} / {int((D['调参PF']>1).sum())}；过关组数 {int((D['过关']=='✔').sum())}"
open(f'/home/user/ext/long/lab/results/{mod}.md', 'w').write(txt)
print(txt[-1500:])
