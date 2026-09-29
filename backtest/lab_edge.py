#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""零成本静态 edge 诊断：信号只生成一次，网格测止损倍数×RR 的原始方向 edge（无未来函数）。"""
import os, sys, copy
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lab_v4 as L

MAXHOLD = 48; COOL = 3

def static_sim(d, sigs, cost=0.0):
    o = d.open.to_numpy(float); h = d.high.to_numpy(float); l = d.low.to_numpy(float)
    c = d.close.to_numpy(float); n = len(d)
    rows = []; busy = -1
    for s in sigs:
        i = s["i"]
        if i < busy:
            continue
        side = 1 if s["side"] == "LONG" else -1
        entry = o[i + 1]; slp = s["sl"]; tpp = s["tp"]
        if slp <= 0 or tpp <= 0 or not np.isfinite(entry):
            continue
        risk = entry * slp
        if side == 1:
            stop = entry * (1 - slp); tp = entry * (1 + tpp)
        else:
            stop = entry * (1 + slp); tp = entry * (1 - tpp)
        px = None; rsn = None; ej = None
        for j in range(i + 1, min(i + 1 + MAXHOLD, n)):
            hi = h[j]; lo = l[j]
            if (side == 1 and lo <= stop) or (side == -1 and hi >= stop):
                px, rsn, ej = stop, "SL", j; break
            if (side == 1 and hi >= tp) or (side == -1 and lo <= tp):
                px, rsn, ej = tp, "TP", j; break
            if j - i >= MAXHOLD:
                px, rsn, ej = c[j], "TIME", j; break
        if px is None:
            ej = min(i + MAXHOLD, n - 1); px = c[ej]; rsn = "END"
        gross = side * (px - entry) / risk
        net = gross - cost / slp
        rows.append(dict(engine=s["engine"], side=s["side"], ct=d.ct.iloc[i + 1],
                         sl_pct=slp, gross=gross, net=net, rsn=rsn))
        busy = ej + 1 + COOL
    return pd.DataFrame(rows)


def period(ts):
    if L.INS0 <= ts <= L.INS1: return "INS"
    if L.OOS0 <= ts <= L.OOS1: return "OOS"
    return "?"


def main(cost=0.0012):
    cache = {}
    for sym in L.SYMS:
        df = pd.read_csv(os.path.join(L.DATA, f"{sym}_5m.csv"))
        d = L.build_features(df)
        d, sigs = L.raw_signals(d, L.DEFAULT)
        cache[sym] = (d, sigs)
    print(f"{'eng':3} {'k':>3} {'rr':>4} | {'period':5} {'n':>5} {'win%':>6} {'grossR':>7} {'netR@'+f'{cost*100:.2f}%':>9} {'TP%':>5} {'medSL%':>7}")
    for eng in ("MR", "TR"):
        for k in (1.0, 1.5, 2.0):
            for rr in (0.8, 1.0, 1.2, 1.5):
                frames = []
                for sym, (d, base) in cache.items():
                    p = copy.deepcopy(L.DEFAULT)
                    if eng == "MR":
                        p["mr_sl_k"], p["mr_sl_cap"], p["mr_rr"] = k, max(k, 2.2), rr
                        p["mr_target_vwap"] = False
                    else:
                        p["tr_sl_k"], p["tr_sl_cap"], p["tr_rr"] = k, max(k, 2.0), rr
                    sigs = L.attach_levels(d, [dict(x) for x in base if x["engine"] == eng], p)
                    t = static_sim(d, sigs, cost)
                    frames.append(t)
                t = pd.concat(frames, ignore_index=True)
                t["period"] = t.ct.apply(period)
                for per in ("INS", "OOS"):
                    g = t[t.period == per]
                    if not len(g):
                        continue
                    win = 100 * (g.net > 0).mean()
                    tpr = 100 * (g.rsn == "TP").mean()
                    print(f"{eng:3} {k:>3.1f} {rr:>4.1f} | {per:5} {len(g):>5} {win:>6.1f} "
                          f"{g.gross.mean():>7.3f} {g.net.mean():>9.3f} {tpr:>5.1f} {100*g.sl_pct.median():>7.3f}")
                print("-" * 78)


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 0.0012)
