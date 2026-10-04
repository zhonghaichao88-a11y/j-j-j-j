"""山寨币假设检验（事先定好）：
  挑选：20 个山寨币，2026-04 ~ 09（上一轮的"新币"）——按 ok_test 标准挑
  验证 1：同样 20 个币，换一段时间 2025-10 ~ 2026-03
  验证 2：另外 20 个没用过的山寨币，2026-04 ~ 09
  两个验证都按同一标准过关，才算"对山寨币有用"。"""
import sys, os, pickle, numpy as np
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze import pf, name, ok_test
from full_bt import TEST
ALT2 = 'ZEC PUMP STRK HYPE PEPE TRUMP BNB ENA TAO ONDO ZRO GRASS VIRTUAL PENGU ICP HBAR XLM SHIB FET WIF'.split()

def agg(d, syms):
    A = defaultdict(lambda: {'r': [], 'b': [], 'gp': 0.0, 'gl': 0.0, 'cp': 0, 'c': 0})
    for s in syms:
        f = f'{d}/{s}.pkl'
        if not os.path.exists(f):
            continue
        for k, (r, b, gp, gl) in pickle.load(open(f, 'rb')).items():
            a = A[k]; a['r'].append(r); a['b'].append(b); a['gp'] += gp; a['gl'] += gl
            if len(r):
                a['c'] += 1; a['cp'] += int(r.sum() > 0)
    out = {}
    for k, a in A.items():
        r = np.concatenate(a['r']).astype(float); b = np.concatenate(a['b'])
        out[k] = dict(n=len(r), pf=pf(r), pf_a=pf(r[~b]), pf_b=pf(r[b]), rand=a['gp'] / a['gl'] if a['gl'] > 0 else 0,
                      coin_pos=a['cp'], coins=a['c'])
    return out

def fmt(x):
    return f'{x["n"]}笔 PF{x["pf"]:.2f}({x["pf_a"]:.2f}/{x["pf_b"]:.2f}) 随机{x["rand"]:.2f} 赚钱币{x["coin_pos"]}/{x["coins"]}' if x else '0 笔'

if __name__ == '__main__':
    S = agg('/home/user/ext/of/full', TEST)
    V1 = agg('/home/user/ext/of/full_old', TEST)
    V2 = agg('/home/user/ext/of/full_alt2', ALT2)
    pick = sorted([k for k, x in S.items() if ok_test(x)], key=lambda k: -S[k]['pf'])
    print(f'挑选（20 山寨币 2026-04~09）过关 {len(pick)} 组；验证1 有 {len(V1)} 组数据，验证2 有 {len(V2)} 组数据')
    both = 0
    for k in pick:
        a, b, c = S[k], V1.get(k), V2.get(k)
        p1, p2 = bool(b) and ok_test(b), bool(c) and ok_test(c)
        both += p1 and p2
        print(f'{name(k)}\n   挑选 {fmt(a)}\n   验证1(同币 2025-10~2026-03) {fmt(b)} {"✔" if p1 else "✘"}\n   验证2(另 20 币) {fmt(c)} {"✔" if p2 else "✘"}')
    print(f'\n两个验证都过关：{both} 组')
    for nm, V in (('验证1', V1), ('验证2', V2)):
        v = [x for x in V.values() if x['n'] >= 30]
        if v:
            print(f'{nm} 全部组合：{len(v)} 组，PF 中位 {np.median([x["pf"] for x in v]):.2f}，随机中位 {np.median([x["rand"] for x in v]):.2f}，PF≥1.1 的 {sum(x["pf"] >= 1.1 for x in v)}')
