import sys, heapq, numpy as np, pandas as pd
sys.path.insert(0, '/home/user/ext/long/lab')
import report
from trap_dca import CFG, SCH
SEG = [('2022', '2022-01-01', '2023-01-01'), ('2023', '2023-01-01', '2024-01-01'), ('2024', '2024-01-01', '2025-01-01'),
       ('2025上', '2025-01-01', '2025-07-01'), ('2025下+', '2025-07-01', '2026-10-01')]
pf = report.pf

def port(X, cap=10, size=0.10, start=100.0):
    X = X.sort_values('t_in', kind='stable').reset_index(drop=True)
    eq, op, busy, curve = start, [], set(), []
    for i, (cn, ti, to, rt) in enumerate(zip(X.coin.values, X.t_in.values, X.t_out.values, X.ret.values)):
        while op and op[0][0] <= ti:
            t, k, amt, r_ = heapq.heappop(op); eq += amt * r_; curve.append(eq); busy.discard(X.coin.values[k])
        if cn in busy or len(op) >= cap:
            continue
        busy.add(cn); heapq.heappush(op, (to, i, eq * size, rt))
    while op:
        t, k, amt, r_ = heapq.heappop(op); eq += amt * r_; curve.append(eq)
    c = pd.Series(curve)
    return round(float(c.iloc[-1]), 1), round(float((c / c.cummax() - 1).min()) * 100, 1)

T = pd.read_parquet('/home/user/ext/of/trap_dca.parquet')
rows = []
for ci, (sc, ex) in enumerate(CFG):
    X = T[T.ci == ci]
    segs = [pf(X[(X.t >= pd.Timestamp(a).value // 10**6) & (X.t < pd.Timestamp(b).value // 10**6)].ret) for _, a, b in SEG]
    fin, dd = port(X)
    lv, wt = SCH[sc]
    rows.append({'补仓': sc + ('' if sc.startswith('不补') else f"（{':'.join(f'{w:g}' for w in wt)}，" + '/'.join(f'+{x * 100:g}%' for x in lv[1:]) + '补）'),
                 '止损': f'离第一笔 {(lv[-1] + ex) * 100:g}%', '单数': len(X), '胜率%': round((X.ret > 0).mean() * 100), 'PF': round(pf(X.ret), 2),
                 **{f'PF_{s}': round(v, 2) for (s, _, _), v in zip(SEG, segs)}, '赚的段数': sum(v >= 1 for v in segs),
                 '补满的单%': round((X.legs == len(lv)).mean() * 100), '最差一单%': round(X.ret.min() * 100, 1), '组合100U变成': fin, '组合回撤%': dd})
D = pd.DataFrame(rows)
D.to_csv('多头摊平做空_补仓_全部.csv', index=False, encoding='utf-8-sig')
pd.set_option('display.width', 250)
print(D.to_string(index=False))
