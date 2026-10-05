import sys, heapq, numpy as np, pandas as pd
from dca_new import CFG, SEG, SCH

def pf(r):
    r = np.asarray(r); r = r[~np.isnan(r)]; n = -r[r < 0].sum(); return float(r[r > 0].sum() / n) if n > 0 else float('inf')

def port(T, ret, tout, cap=10, size=0.10, start=100.0):
    X = pd.DataFrame({'coin': T.coin.values, 't_in': T.t_in.values, 't_out': tout, 'ret': ret}).dropna().sort_values('t_in', kind='stable').reset_index(drop=True)
    eq, op, busy, curve = start, [], set(), []
    tin, to, cn, rt = X.t_in.values, X.t_out.values, X.coin.values, X.ret.values
    for i in range(len(X)):
        while op and op[0][0] <= tin[i]:
            t, k, amt = heapq.heappop(op); eq += amt * rt[k]; curve.append((t, eq)); busy.discard(cn[k])
        if cn[i] in busy or len(op) >= cap:
            continue
        busy.add(cn[i]); heapq.heappush(op, (to[i], i, eq * size))
    while op:
        t, k, amt = heapq.heappop(op); eq += amt * rt[k]; curve.append((t, eq))
    c = pd.Series([e for _, e in curve])
    return round(float(c.iloc[-1]), 1), round(float((c / c.cummax() - 1).min()) * 100, 1)

def name(ci):
    sc, tp, ex, h = CFG[ci]; lv, wt = SCH[sc]
    return f'补仓{len(lv)}笔({":".join(map(str, wt))}，' + '/'.join(f'-{x * 100:g}%' for x in lv[1:]) + f'补) 均价止盈{tp:.0%} 补满后再亏{ex:.0%}止损 拿{h}h'

T, res, tout = pd.read_pickle('/home/user/ext/of/dca_new.pkl')
seg = np.full(len(T), '', object)
for nm, a, b in SEG:
    seg[(T.t.values >= pd.Timestamp(a).value // 10**6) & (T.t.values < pd.Timestamp(b).value // 10**6)] = nm
rows = []
for kind in ('清洗接盘', '多头摊平做空', '两个一起'):
    m = np.ones(len(T), bool) if kind == '两个一起' else (T.kind == kind).values
    base = T.ret.values
    fin0, dd0 = port(T[m], base[m], T.t_out.values[m])
    segs0 = [pf(base[m & (seg == s)]) for s, _, _ in SEG]
    print(f'\n===== {kind}  原版出场：{m.sum()} 笔 胜率{(base[m] > 0).mean():.0%} PF {pf(base[m]):.2f}，5 段 ' + ' / '.join(f'{x:.2f}' for x in segs0) + f'，组合 100U→{fin0}U 回撤 {dd0}%')
    for ci in range(len(CFG)):
        r = res[ci]
        segs = [pf(r[m & (seg == s)]) for s, _, _ in SEG]
        fin, dd = port(T[m], r[m], tout[ci][m])
        rows.append(dict(打法=kind, 出场=name(ci), 单数=int((~np.isnan(r[m])).sum()), 胜率=round(float(np.nanmean(r[m] > 0)) * 100), PF=round(pf(r[m]), 2),
                         **{f'PF_{s}': round(x, 2) for (s, _, _), x in zip(SEG, segs)}, 赚的段数=sum(x >= 1 for x in segs),
                         最差一单=round(float(np.nanmin(r[m])) * 100, 1), 组合100U变成=fin, 组合回撤=dd, 原版PF=round(pf(base[m]), 2), 原版组合=fin0))
D = pd.DataFrame(rows)
D.to_csv('新打法补仓_全部.csv', index=False, encoding='utf-8-sig')
for kind in ('清洗接盘', '多头摊平做空', '两个一起'):
    X = D[D.打法 == kind]
    good = X[(X.赚的段数 >= 4) & (X.PF > X.原版PF) & (X.组合100U变成 > X.原版组合)]
    print(f'\n{kind}：{len(X)} 种补仓出场，PF 中位 {X.PF.median():.2f}（原版 {X.原版PF.iloc[0]}），PF 比原版高的 {int((X.PF > X.原版PF).sum())} 种，'
          f'组合比原版好的 {int((X.组合100U变成 > X.原版组合).sum())} 种，过关（≥4 段赚 + PF 和组合都比原版好）{len(good)} 种')
    for _, r in X.sort_values('组合100U变成', ascending=False).head(5).iterrows():
        print(f'   {r.出场}：{r.单数}笔 胜{r.胜率}% PF{r.PF}，5 段 {r["PF_2022"]}/{r["PF_2023"]}/{r["PF_2024"]}/{r["PF_2025上"]}/{r["PF_2025下+"]}，最差一单 {r.最差一单}%，组合 100U→{r.组合100U变成}U 回撤 {r.组合回撤}%')
