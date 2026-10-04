"""用户的想法：订单流里所有打法的信号加起来，看多的多就开多，看空的多就开空。
数据：欧易逐笔成交压出的 1 分钟足迹（BTC/ETH/SOL，2026-04 ~ 2026-09，和网页上每个打法的回测同一份）。
做法：每根K线收盘，所有打法（全部打开）各自出信号；票数 = 看多个数 - 看空个数。
  票数 ≥ k 开多，≤ -k 开空；下一根K线第一分钟开盘价进场（吃单 + 滑点）。
  出场两种：拿固定根数后平；或者拿到反向票出现（最多 48 根）。手里有单时再来同向票不加仓，反向票（够 k）就反手。
成本：每边吃单 0.05% + 滑点 0.02%。同时也看"反着做"。"""
import sys, os, numpy as np, json
from collections import Counter
sys.path.insert(0, '/home/user/j-j-j-j/orderflow')
import of_backtest as B
from of_core import Detector
FEE, SLIP = 0.0005, 0.0002

def votes(d, tf):
    bars, row = B.build_bars(d, tf)
    det = Detector(row, {}, enabled=None)
    out = []
    for bar in bars:
        sigs = det.on_bar(bar)
        out.append((bar.t, sum(s.side for s in sigs), Counter(s.kind for s in sigs)))
    return out

def sim(d, V, tf, k, hold, flip, fade=False):
    om, mo, mc = d['om'], d['o'], d['c']
    idx = {int(m): i for i, m in enumerate(om)}
    rets, ts = [], []
    pos = None                      # (side, entry, end_min)
    def close(px, t):
        s, e, _ = pos
        rets.append(s * (px * (1 - s * SLIP) / e - 1) - 2 * FEE); ts.append(t)
    for t, v, _ in V:
        start = t // 60000 + tf
        if start not in idx:
            continue
        px = mo[idx[start]]
        side = (1 if v >= k else -1 if v <= -k else 0) * (-1 if fade else 1)
        if pos and start >= pos[2]:
            close(px, start); pos = None
        if side == 0:
            continue
        if pos and pos[0] == side:
            continue
        if pos and pos[0] != side:
            close(px, start); pos = None
        end = start + (48 if flip else hold) * tf
        pos = (side, px * (1 + side * SLIP), end)
    return np.array(rets), np.array(ts)

def pf(r):
    n = -r[r < 0].sum(); return r[r > 0].sum() / n if n > 0 else float('inf')

if __name__ == '__main__':
    D = '/home/user/ext/of/fp'
    res = {}
    for tf in (5, 15, 60):
        allV = {}
        for sym in ('BTC', 'ETH', 'SOL'):
            d = B.load(D, sym)
            allV[sym] = (d, votes(d, tf))
        cnt = Counter(); 
        for sym, (d, V) in allV.items():
            cnt.update(Counter(v for _, v, _ in V))
        print(f'\n=== {tf} 分钟  票数分布', dict(sorted(cnt.items())))
        for k in (1, 2, 3):
            for hold, flip in ((1, False), (3, False), (12, False), (0, True)):
                for fade in (False, True):
                    R, T = [], []
                    for sym, (d, V) in allV.items():
                        r, t = sim(d, V, tf, k, hold, flip, fade); R.append(r); T.append(t)
                    r = np.concatenate(R); t = np.concatenate(T)
                    if len(r) == 0: continue
                    mid = np.median(t)
                    a, b = r[t < mid], r[t >= mid]
                    name = f'{tf}m 票≥{k} {"拿到反向票" if flip else f"拿{hold}根"} {"反着做" if fade else "顺着做"}'
                    res[name] = dict(n=len(r), win=round(float((r > 0).mean()), 3), pf=round(pf(r), 2), pf_a=round(pf(a), 2), pf_b=round(pf(b), 2), bp=round(float(r.mean() * 1e4), 1), gross_bp=round(float(r.mean() * 1e4 + 14), 1))
                    print(name, res[name], flush=True)
    json.dump(res, open('结果.json', 'w'), ensure_ascii=False, indent=1)
