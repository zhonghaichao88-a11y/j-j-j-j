import sys, glob, pickle, numpy as np, pandas as pd
from collections import defaultdict
sys.path.insert(0, '/home/user/j-j-j-j/orderflow')
from of_playbook import PB_NAMES
from pb_full import EXITS, SETS

def pf(r):
    n = -r[r < 0].sum(); return float(r[r > 0].sum() / n) if n > 0 else (float('inf') if len(r) else 0.0)

def agg(nm):
    A = defaultdict(lambda: {'r': [], 'b': [], 'gp': 0.0, 'gl': 0.0, 'cp': 0, 'c': 0})
    for f in glob.glob(f'/home/user/ext/of/pbfull/{nm}_*.pkl'):
        for k, (r, b, gp, gl) in pickle.load(open(f, 'rb')).items():
            a = A[k]; a['r'].append(r); a['b'].append(b); a['gp'] += gp; a['gl'] += gl
            if len(r): a['c'] += 1; a['cp'] += int(r.sum() > 0)
    out = {}
    for k, a in A.items():
        r = np.concatenate(a['r']).astype(float); b = np.concatenate(a['b'])
        out[k] = dict(n=len(r), pf=pf(r), pf_a=pf(r[~b]), pf_b=pf(r[b]), rand=a['gp'] / a['gl'] if a['gl'] > 0 else 0, cp=a['cp'], c=a['c'],
                      win=float((r > 0).mean()) if len(r) else 0)
    return out

def ok(x):
    return bool(x) and x['n'] >= 100 and x['pf'] >= 1.1 and x['pf_a'] >= 1 and x['pf_b'] >= 1 and x['pf'] - x['rand'] >= 0.1 and x['cp'] * 2 > x['c']

def nm(k):
    tf, kind, ei = k; ex = EXITS[ei]
    if ex[0] == '补仓':
        (lv, wt), tp, sl, h = ex[1:]
        e = f'补仓{len(lv)}笔({":".join(map(str, wt))}，' + '/'.join(f'-{x * 100:g}%' for x in lv[1:]) + f'补) 均价止盈{tp:.0%} 止损离第一笔{sl * 100:g}% 拿{h}h'
    elif ex[0] == '原版':
        e = f'原版止损 止盈{ex[2]:g}倍 拿{ex[3]}h'
    else:
        e = f'止损{ex[1]:.0%} 止盈{"不设" if ex[2] == 0 else f"{ex[2]:.0%}"} 拿{ex[3]}h'
    return f'{tf}分钟 {PB_NAMES[kind]} {e}'

R = {s: agg(s) for s in SETS}
keys = sorted(set().union(*[set(v) for v in R.values()]))
rows = []
for k in keys:
    row = {'组合': nm(k)}
    for s in SETS:
        x = R[s].get(k)
        row.update({f'{s}_单数': x['n'] if x else 0, f'{s}_胜率%': round(x['win'] * 100) if x else None, f'{s}_PF': round(x['pf'], 2) if x else None,
                    f'{s}_随机PF': round(x['rand'], 2) if x else None, f'{s}_过关': '✔' if ok(x) else ''})
    rows.append(row)
pd.DataFrame(rows).to_csv('实战版_全部组合.csv', index=False, encoding='utf-8-sig')
print('组合总数', len(rows))
for s in SETS:
    v = [x for x in R[s].values() if x['n'] >= 30]
    print(f'{s}: ≥30 笔 {len(v)} 组，PF 中位 {np.median([x["pf"] for x in v]):.2f}，随机中位 {np.median([x["rand"] for x in v]):.2f}，PF>1 的 {sum(x["pf"] > 1 for x in v)}，过关 {sum(ok(x) for x in R[s].values())}')
cnt = {k: sum(ok(R[s].get(k)) for s in SETS) for k in keys}
print('三份数据里过关次数分布', {i: sum(v == i for v in cnt.values()) for i in range(4)})
for k in [k for k, v in cnt.items() if v >= 2]:
    print(' ', nm(k)); print('     ' + ' | '.join(f"{s} {R[s].get(k, {}).get('n', 0)}笔 胜{R[s].get(k, {}).get('win', 0) * 100:.0f}% PF{R[s].get(k, {}).get('pf', 0):.2f} 随机{R[s].get(k, {}).get('rand', 0):.2f} {'✔' if ok(R[s].get(k)) else '✘'}" for s in SETS))
D = pd.DataFrame([dict(tf=k[0], kind=PB_NAMES[k[1]], **{s: R[s].get(k, {}).get('pf', np.nan) for s in SETS}) for k in keys])
print(D.groupby(['kind', 'tf'])[list(SETS)].median().round(2).to_string())
