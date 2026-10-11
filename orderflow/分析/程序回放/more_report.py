"""清洗接盘补测结果：原来 148 个币 vs 新补的币，按上线早晚分组；多少币在币安没有现货（程序实盘也出不了信号）。"""
import os, sys, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.argv = ['x']
import compare as C
import ast, re
St = pd.DataFrame([ast.literal_eval(l.split(' ', 1)[1]) for l in open('/home/user/ext/more/run.log', encoding='utf-8') if re.match(r'^\d\d:\d\d \{', l)])
for c_ in ('oi', 'spot'): St[c_] = St[c_].fillna(False).astype(bool)
St = St.drop_duplicates('coin', keep='last')
P, done = C.program('flush'); P = P[P.t_in >= C.ms('2021-12-01')]
import run_all
old148 = set(run_all.coins()); new = set(P.coin) - old148
P['grp'] = np.where(P.coin.isin(new), '新补的币', '原来148个')
out = [f'币安 U 本位合约（加密币，去掉稳定币、股票）新补 {len(St)} 个：'
       f'有持仓量 {St.oi.sum()}，其中币安有现货 {(St.oi & St.spot).sum()}（没有现货的 {(St.oi & ~St.spot).sum()} 个，程序实盘也做不了清洗接盘）；'
       f'回放出成交的 {(St.get("trades", pd.Series(dtype=float)).fillna(0) > 0).sum()} 个；出错 {St.err.notna().sum() if "err" in St else 0} 个', '']
for g, x in P.groupby('grp'):
    out.append(f'【{g}】{x.coin.nunique()} 个币：' + C.stats(x))
    for nm, a, b in C.SEG:
        y = x[(x.t_in >= C.ms(a)) & (x.t_in < C.ms(b))]
        if len(y): out.append(f'   {nm}: ' + C.stats(y))
out.append(''); out.append('【全部合起来】' + C.stats(P))
for y, g in P.groupby(pd.to_datetime(P.t_in, unit='ms').dt.year): out.append(f'   {y}: ' + C.stats(g))
# 按币上线早晚（第一笔持仓量数据的时间）
first = {}
for c in P.coin.unique():
    first[c] = P[P.coin == c].t_in.min()
st = St.set_index('coin').start.to_dict() if 'start' in St else {}
def age(c):
    s = st.get(c)
    return '2024 年以后才上的' if isinstance(s, str) and s >= '2024-01-01' else '2024 年以前就有的'
P['age'] = [age(c) if c in new else '2024 年以前就有的' for c in P.coin]
out.append('')
for g, x in P.groupby('age'):
    out.append(f'【{g}】{x.coin.nunique()} 个币：' + C.stats(x))
    x2 = x[x.t_in >= C.ms('2024-01-01')]
    out.append('   只看 2024 年以后：' + C.stats(x2))
txt = '\n'.join(out); print(txt); open(os.path.join(HERE, '清洗接盘补测结果.md'), 'w').write('```\n' + txt + '\n```\n')
