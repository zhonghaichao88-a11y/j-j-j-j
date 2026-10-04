"""汇总 full_bt.py 的结果：挑选期（BTC/ETH/SOL）按事先定好的标准挑，验证期（20 个新币）原样检验。"""
import sys, pickle, os, json, numpy as np
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from full_bt import EXITS, TRAIN, TEST
sys.path.insert(0, '/home/user/j-j-j-j/orderflow')
from of_core import SIGNAL_NAMES

def pf(r):
    n = -r[r < 0].sum(); return float(r[r > 0].sum() / n) if n > 0 else (float('inf') if len(r) else 0.0)

def agg(syms):
    A = defaultdict(lambda: {'r': [], 'b': [], 'gp': 0.0, 'gl': 0.0, 'coin_pos': 0, 'coins': 0})
    for s in syms:
        f = f'/home/user/ext/of/full/{s}.pkl'
        if not os.path.exists(f):
            continue
        for key, (r, b, gp, gl) in pickle.load(open(f, 'rb')).items():
            a = A[key]; a['r'].append(r); a['b'].append(b); a['gp'] += gp; a['gl'] += gl
            if len(r):
                a['coins'] += 1; a['coin_pos'] += int(r.sum() > 0)
    out = {}
    for key, a in A.items():
        r = np.concatenate(a['r']).astype(float); b = np.concatenate(a['b'])
        out[key] = dict(n=len(r), pf=pf(r), pf_a=pf(r[~b]), pf_b=pf(r[b]), bp=float(r.mean() * 1e4) if len(r) else 0,
                        win=float((r > 0).mean()) if len(r) else 0, rand=a['gp'] / a['gl'] if a['gl'] > 0 else 0,
                        coin_pos=a['coin_pos'], coins=a['coins'])
    return out

def name(key):
    tf, kind, ei = key; mode, sp, tp, h = EXITS[ei]
    ex = f'原版止损 止盈{tp:g}倍' if mode == '原版' else f'止损{sp:.0%} 止盈{"不设" if tp == 0 else f"{tp:.0%}"} 拿{h}h'
    return f'{tf}分钟 {SIGNAL_NAMES.get(kind, kind)} {ex}'

def ok_train(x):
    return x['n'] >= 50 and x['pf'] >= 1.1 and x['pf_a'] >= 1 and x['pf_b'] >= 1 and x['pf'] - x['rand'] >= 0.1

def ok_test(x):
    return x['n'] >= 100 and x['pf'] >= 1.1 and x['pf_a'] >= 1 and x['pf_b'] >= 1 and x['pf'] - x['rand'] >= 0.1 and x['coin_pos'] * 2 > x['coins']

if __name__ == '__main__':
    tr = agg(TRAIN); te = agg(TEST) if len(sys.argv) > 1 else {}
    v = [x for x in tr.values() if x['n'] >= 30]
    print(f'挑选期 组合 {len(tr)}（≥30 笔 {len(v)}）PF 中位 {np.median([x["pf"] for x in v]):.2f}，随机中位 {np.median([x["rand"] for x in v]):.2f}，PF>1 的 {sum(x["pf"] > 1 for x in v)}')
    pick = [k for k, x in tr.items() if ok_train(x)]
    print(f'挑选期过关 {len(pick)} 组')
    rows = []
    for k in sorted(pick, key=lambda k: -tr[k]['pf']):
        a = tr[k]; b = te.get(k)
        line = f'{name(k)} | 挑选 {a["n"]}笔 PF{a["pf"]:.2f}({a["pf_a"]:.2f}/{a["pf_b"]:.2f}) 随机{a["rand"]:.2f}'
        if te:
            line += (f' | 新币 {b["n"]}笔 PF{b["pf"]:.2f}({b["pf_a"]:.2f}/{b["pf_b"]:.2f}) 随机{b["rand"]:.2f} 赚钱币{b["coin_pos"]}/{b["coins"]} {"✔过关" if ok_test(b) else "✘"}' if b else ' | 新币 0 笔')
        print(line); rows.append(line)
    if te:
        v2 = [x for x in te.values() if x['n'] >= 30]
        print(f'\n新币全部组合（不管挑选期）：≥30 笔 {len(v2)} 组，PF 中位 {np.median([x["pf"] for x in v2]):.2f}，随机中位 {np.median([x["rand"] for x in v2]):.2f}，PF≥1.1 的 {sum(x["pf"] >= 1.1 for x in v2)}')
        both = [k for k in pick if k in te and ok_test(te[k])]
        print(f'挑选期过关又在新币过关：{len(both)} 组')
        best = sorted(te, key=lambda k: -te[k]['pf'] if te[k]['n'] >= 100 else 0)[:15]
        print('\n新币上 PF 最高的 15 组（≥100 笔，只作参考，不算过关）：')
        for k in best:
            b = te[k]; a = tr.get(k, {})
            print(f'  {name(k)} | 新币 {b["n"]}笔 PF{b["pf"]:.2f}({b["pf_a"]:.2f}/{b["pf_b"]:.2f}) 随机{b["rand"]:.2f} 赚钱币{b["coin_pos"]}/{b["coins"]} | 挑选期 PF{a.get("pf", 0):.2f}')
