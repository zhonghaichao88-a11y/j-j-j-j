import sys, os, pickle, glob, numpy as np, pandas as pd
from collections import defaultdict
from big_variants import EXITS, SETS

def pf(r):
    n = -r[r < 0].sum(); return float(r[r > 0].sum() / n) if n > 0 else (float('inf') if len(r) else 0.0)

def agg(nm):
    A = defaultdict(lambda: {'r': [], 'b': [], 'gp': 0.0, 'gl': 0.0, 'cp': 0, 'c': 0})
    for f in glob.glob(f'/home/user/ext/of/bigvar/{nm}_*.pkl'):
        for k, (r, b, gp, gl) in pickle.load(open(f, 'rb')).items():
            a = A[k]; a['r'].append(r); a['b'].append(b); a['gp'] += gp; a['gl'] += gl
            if len(r): a['c'] += 1; a['cp'] += int(r.sum() > 0)
    out = {}
    for k, a in A.items():
        r = np.concatenate(a['r']).astype(float); b = np.concatenate(a['b'])
        out[k] = dict(n=len(r), pf=pf(r), pf_a=pf(r[~b]), pf_b=pf(r[b]), rand=a['gp'] / a['gl'] if a['gl'] > 0 else 0, cp=a['cp'], c=a['c'])
    return out

def ok(x):
    return x and x['n'] >= 100 and x['pf'] >= 1.1 and x['pf_a'] >= 1 and x['pf_b'] >= 1 and x['pf'] - x['rand'] >= 0.1 and x['cp'] * 2 > x['c']

def nm(k):
    tf, m, conf, dirn, ei = k; mode, sp, tp, h = EXITS[ei]
    ex = f'原版止损 止盈{tp:g}倍' if mode == '原版' else f'止损{sp:.0%} 止盈{"不设" if tp == 0 else f"{tp:.0%}"} 拿{h}h'
    return f'{tf}分钟 {m:g}倍 {conf} {dirn} {ex}'

R = {s: agg(s) for s in SETS}
rows = []
for k in R['挑选']:
    row = {'组合': nm(k)}
    for s in SETS:
        x = R[s].get(k)
        row[f'{s}_单数'] = x['n'] if x else 0; row[f'{s}_PF'] = round(x['pf'], 2) if x else None
        row[f'{s}_随机'] = round(x['rand'], 2) if x else None; row[f'{s}_过关'] = '✔' if ok(x) else ''
    rows.append(row)
D = pd.DataFrame(rows); D.to_csv('大单跟随变体_全部.csv', index=False, encoding='utf-8-sig')
print('组合总数', len(D))
for s in SETS:
    v = [x for x in R[s].values() if x['n'] >= 30]
    print(f'{s}: ≥30 笔 {len(v)} 组，PF 中位 {np.median([x["pf"] for x in v]):.2f}，随机中位 {np.median([x["rand"] for x in v]):.2f}，过关 {sum(ok(x) for x in R[s].values())} 组')
pick = [k for k in R['挑选'] if ok(R['挑选'][k])]
both = [k for k in pick if ok(R['验证1换时间'].get(k)) and ok(R['验证2换币'].get(k))]
print(f'挑选过关 {len(pick)} 组；挑选 + 两个验证都过关 {len(both)} 组')
for k in sorted(pick, key=lambda k: -R['挑选'][k]['pf'])[:15]:
    print(' ', nm(k), '|', ' | '.join(f"{s} {R[s].get(k, {}).get('n', 0)}笔 PF{R[s].get(k, {}).get('pf', 0):.2f} 随机{R[s].get(k, {}).get('rand', 0):.2f} {'✔' if ok(R[s].get(k)) else '✘'}" for s in SETS))
# 各维度平均（看哪种定义好一点）
D2 = pd.DataFrame([dict(tf=k[0], mult=k[1], conf=k[2], dirn=k[3], **{s: R[s].get(k, {}).get('pf', np.nan) for s in SETS}) for k in R['挑选']])
for col in ('dirn', 'conf', 'mult', 'tf'):
    print(D2.groupby(col)[list(SETS)].median().round(2).to_string()); print()
