"""旧打法的进场信号 + 新打法的出场：止损固定 X%（挂在欧易，碰到就平），不设止盈，拿满 N 小时平。
数据：欧易逐笔足迹 BTC/ETH/SOL 2026-04 ~ 2026-09（旧打法唯一能回测的数据）。
信号在K线收盘出，下一根K线第一分钟开盘价进场；之后每分钟先查止损。每个打法同一时间只拿一单。
成本：吃单 0.05% + 滑点 0.02% 每边；止损再多 0.05% 滑点。
对照：同样的出场、同样多空比例、随机时间进场（各跑 20 次取平均），看信号有没有比瞎开好。"""
import sys, json, numpy as np
from collections import defaultdict
sys.path.insert(0, '/home/user/j-j-j-j/orderflow')
import of_backtest as B
from of_core import Detector, SIGNAL_NAMES
FEE, SLIP, STOP_SLIP = 0.0005, 0.0002, 0.0005
STOPS = [0.03, 0.05]
HOLDS = [12, 24, 48]          # 小时


def exit_one(mo, mh, ml, mc, om, i0, side, stop, hold_min):
    e = mo[i0] * (1 + side * SLIP)
    st = e * (1 - side * stop)
    end = om[i0] + hold_min
    j = i0
    n = len(om)
    while j < n and om[j] < end:
        if (ml[j] <= st) if side > 0 else (mh[j] >= st):
            ex = (min(mo[j], st) if side > 0 else max(mo[j], st)) * (1 - side * STOP_SLIP)
            return side * (ex / e - 1) - 2 * FEE, j
        j += 1
    j = min(j, n) - 1
    return side * (mc[j] * (1 - side * SLIP) / e - 1) - 2 * FEE, j


def run(d, entries, stop, hold_h):
    """entries: [(kind, side, 进场分钟行号)]，按时间排好；每个 kind 同时只拿一单"""
    om, mo, mh, ml, mc = d['om'], d['o'], d['h'], d['l'], d['c']
    busy = defaultdict(lambda: -1)
    out = []
    for kind, side, i0 in entries:
        if i0 <= busy[kind]:
            continue
        r, j = exit_one(mo, mh, ml, mc, om, i0, side, stop, hold_h * 60)
        busy[kind] = j
        out.append((kind, side, int(om[i0]), r))
    return out


def pf(r):
    r = np.asarray(r); n = -r[r < 0].sum()
    return float(r[r > 0].sum() / n) if n > 0 else float('inf')


if __name__ == '__main__':
    rng = np.random.default_rng(7)
    res = {}
    for tf in (5, 15, 60):
        data = {}
        for sym in ('BTC', 'ETH', 'SOL'):
            d = B.load('/home/user/ext/of/fp', sym)
            bars, row = B.build_bars(d, tf)
            det = Detector(row, {}, enabled=None)
            idx = {int(m): i for i, m in enumerate(d['om'])}
            ent = []
            for bar in bars:
                for s in det.on_bar(bar):
                    st = bar.t // 60000 + tf
                    if st in idx:
                        ent.append((s.kind, s.side, idx[st]))
            data[sym] = (d, ent)
        mid = np.median(np.concatenate([d['om'] for d, _ in data.values()]))
        for stop in STOPS:
            for hh in HOLDS:
                tr = []
                for sym, (d, ent) in data.items():
                    tr += [(sym, *x) for x in run(d, ent, stop, hh)]
                by = defaultdict(list)
                for sym, k, side, t, r in tr:
                    by[k].append((sym, side, t, r))
                for k, L in by.items():
                    r = np.array([x[3] for x in L]); t = np.array([x[2] for x in L])
                    # 随机对照：每个币按这个打法在该币的单数和多空比例，随机时间进场
                    rr = []
                    for _ in range(20):
                        for sym, (d, _) in data.items():
                            mine = [x for x in L if x[0] == sym]
                            if not mine:
                                continue
                            n = len(d['om']) - hh * 60 - 1
                            pick = np.sort(rng.integers(0, n, len(mine)))
                            sides = rng.permutation([x[1] for x in mine])
                            rr += [x[3] for x in run(d, [('r', int(sd), int(i)) for sd, i in zip(sides, pick)], stop, hh)]
                    name = f'{tf}m|{SIGNAL_NAMES.get(k, k)}|止损{stop:.0%}|拿{hh}h'
                    res[name] = dict(n=len(r), win=round(float((r > 0).mean()), 2), pf=round(pf(r), 2),
                                     pf_a=round(pf(r[t < mid]), 2), pf_b=round(pf(r[t >= mid]), 2),
                                     bp=round(float(r.mean() * 1e4), 1), rand_pf=round(pf(rr), 2))
                    print(name, res[name], flush=True)
    json.dump(res, open('结果.json', 'w'), ensure_ascii=False, indent=1)
