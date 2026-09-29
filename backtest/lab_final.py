#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""决定性测试：
A) 5m MR 宽止损(k=2) rr1.5，在 maker/返佣 0.04~0.08% 成本 + 共振子集下的表现。
B) 1h 趋势“1R分批+保本+吊灯runner”（趋势跟踪正确出场）全周期表现。"""
import os, sys, copy
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lab_v4 as L
import lab_htf as H
from lab_edge2 import sim as mr_sim, agg as agg2, period as per2, load as mr_load


def partA():
    print("\n================= A) 5m MR k=2 rr=1.5 =================")
    frames = mr_load()   # 5m MR 原始信号（含特征标签）
    for cost in (0.0008, 0.0006, 0.0004):
        t = pd.concat([mr_sim(d, sigs, cost, 1.5, 2.0, None, None) for _, d, sigs in frames], ignore_index=True)
        t["period"] = t.ct.apply(per2)
        # 共振子集：扫损 或 RSI更极端(<=10) 或 量能高潮(>=1.5)
        conf = t[(t.sweep) | (t.rsi <= 10) | (t.volr >= 1.5)]
        for name, g in (("ALL", t), ("CONFLUENCE", conf)):
            ins = g[g.period == "INS"]; oos = g[g.period == "OOS"]
            print(f"cost{cost*100:.2f}% {name:11} n={len(g):4} | INS {dict(agg2(ins))} | OOS {dict(agg2(oos))}")


def partB():
    print("\n================= B) 1h 趋势 runner（分批+保本+吊灯） =================")
    for cost in (0.0012, 0.0008, 0.0006):
        frames = []
        for sym in L.SYMS:
            df = pd.read_csv(os.path.join(L.DATA, f"{sym}_5m.csv"))
            r = H.htf_features(df, "1h", 60)
            sigs = H.trend_signals(r)
            atr = r.atr.to_numpy(float); c = r.close.to_numpy(float)
            for s in sigs:
                i = s["i"]; au = atr[i]
                stop = float(np.clip(s["anchor"] + 0.5 * au, 2.0 * au, 3.0 * au))
                s["sl"] = stop / c[i]; s["tp"] = 4.0 * stop / c[i]   # runner 上限 4R
                s["regime"] = 1 if s["side"] == "LONG" else -1; s["sym"] = sym; s["engine"] = "TR"
            p = dict(use_partial=True, part_frac=0.5, tp1_r=1.0, trail_k=3.0,
                     stag_bars=10**9, stag_r=0.0, max_hold=80, cooldown=2)
            t = L.simulate(r, sigs, p, cost)
            frames.append(t)
        t = pd.concat(frames, ignore_index=True); t["period"] = t.ct.apply(per2)
        def ag(g):
            if not len(g): return {}
            pf = g.net_r[g.net_r > 0].sum() / max(-g.net_r[g.net_r < 0].sum(), 1e-9)
            return {"n": len(g), "win": round(100 * (g.net_r > 0).mean(), 1),
                    "avgR": round(g.net_r.mean(), 3), "totR": round(g.net_r.sum(), 0), "PF": round(pf, 2)}
        for per in ("INS", "OOS"):
            print(f"cost{cost*100:.2f}% {per}", ag(t[t.period == per]))
        print(f"cost{cost*100:.2f}% ALL", ag(t), "reasons",
              t.groupby("reason").net_r.agg(["count", "mean"]).round(2).to_dict())


if __name__ == "__main__":
    partA()
    partB()
