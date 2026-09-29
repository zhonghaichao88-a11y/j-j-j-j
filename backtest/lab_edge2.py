#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MR 均值回归引擎：特征归因 + 失败早砍（时间止损）实验，寻找能把毛 edge 拉高到盖过成本的子集。"""
import os, sys, copy
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lab_v4 as L

MAXHOLD = 48; COOL = 3

def sim(d, sigs, cost, rr, k=2.0, tstop=None, be_r=None):
    o = d.open.to_numpy(float); h = d.high.to_numpy(float); l = d.low.to_numpy(float)
    c = d.close.to_numpy(float); n = len(d)
    rows = []; busy = -1
    for s in sigs:
        i = s["i"]
        if i < busy:
            continue
        side = 1 if s["side"] == "LONG" else -1
        entry = o[i + 1]
        au = d.atr_15.iloc[i]
        if not np.isfinite(au) or au <= 0:
            continue
        stop = float(np.clip(s["anchor"] + 0.4 * au, 1.0 * au, k * au))
        slp = stop / entry; tpp = rr * stop / entry
        if slp <= 0:
            continue
        risk = entry * slp
        stop_px = entry * (1 - side * slp); tp_px = entry * (1 + side * tpp)
        be_px = entry * (1 + side * 2 * cost); armed = False
        px = None; rsn = None; ej = None; maxg = 0.0
        for j in range(i + 1, min(i + 1 + MAXHOLD, n)):
            hi = h[j]; lo = l[j]
            if (side == 1 and lo <= stop_px) or (side == -1 and hi >= stop_px):
                px, rsn, ej = stop_px, ("BE" if armed else "SL"), j; break
            if (side == 1 and hi >= tp_px) or (side == -1 and lo <= tp_px):
                px, rsn, ej = tp_px, "TP", j; break
            g = side * ((hi if side == 1 else lo) - entry) / risk
            maxg = max(maxg, g)
            if be_r is not None and not armed and g >= be_r:
                armed = True; stop_px = be_px
            if tstop is not None and (j - i) >= tstop:
                px, rsn, ej = c[j], "TIME", j; break
        if px is None:
            ej = min(i + MAXHOLD, n - 1); px = c[ej]; rsn = "END"
        gross = side * (px - entry) / risk; net = gross - cost / slp
        r = dict(gross=gross, net=net, rsn=rsn, sl_pct=slp, bars=ej - i, ct=d.ct.iloc[i + 1])
        for tag in ("sweep", "rsi", "z", "hour", "volr", "er15", "er1h", "side"):
            r[tag] = s.get(tag)
        rows.append(r)
        busy = ej + 1 + COOL
    return pd.DataFrame(rows)


def agg(g):
    if not len(g):
        return pd.Series({"n": 0})
    pf = g.net[g.net > 0].sum() / max(-g.net[g.net < 0].sum(), 1e-9)
    return pd.Series({"n": len(g), "win": round(100 * (g.net > 0).mean(), 1),
                      "grossR": round(g.gross.mean(), 3), "netR": round(g.net.mean(), 3),
                      "PF": round(pf, 2)})


def period(ts):
    if L.INS0 <= ts <= L.INS1: return "INS"
    if L.OOS0 <= ts <= L.OOS1: return "OOS"
    return "?"


def load():
    frames = []
    p = copy.deepcopy(L.DEFAULT); p["mr_sweep"] = False
    for sym in L.SYMS:
        df = pd.read_csv(os.path.join(L.DATA, f"{sym}_5m.csv"))
        d = L.build_features(df)
        d, sigs = L.raw_signals(d, p)
        sigs = [s for s in sigs if s["engine"] == "MR"]
        frames.append((sym, d, sigs))
    return frames


def report(frames, cost, rr, k, tstop, be_r, label):
    t = pd.concat([sim(d, sigs, cost, rr, k, tstop, be_r) for _, d, sigs in frames], ignore_index=True)
    t["period"] = t.ct.apply(period)
    print(f"\n########## {label}: cost={cost*100:.2f}% rr={rr} k={k} tstop={tstop} BE@{be_r} ##########")
    for per in ("INS", "OOS"):
        print(per, dict(agg(t[t.period == per])))
    return t


if __name__ == "__main__":
    frames = load()
    # 1) 时间止损（失败早砍）扫描，maker 成本 0.08%，rr=1.0 与 1.5
    for rr in (1.0, 1.3, 1.5):
        for tstop in (None, 12, 8, 6):
            report(frames, 0.0008, rr, 2.0, tstop, None, f"maker rr{rr}")
    # 2) 用一个中等配置做特征归因（rr=1.3, tstop=8, maker）
    t = report(frames, 0.0008, 1.3, 2.0, 8, None, "ATTR")
    t["sweep_f"] = t.sweep.map({True: "sweep", False: "nosweep"})
    t["rsi_f"] = pd.cut(t.rsi, [0, 5, 12, 20, 100], labels=["<5", "5-12", "12-20", ">20"])
    t["z_f"] = pd.cut(t.z, [-9, 0.5, 1.0, 1.5, 9], labels=["z<.5", ".5-1", "1-1.5", ">1.5"])
    t["vol_f"] = pd.cut(t.volr, [0, 0.9, 1.3, 2.0, 99], labels=["<0.9", ".9-1.3", "1.3-2", ">2"])
    t["er_f"] = pd.cut(t.er15, [0, 0.1, 0.18, 0.25, 1], labels=["<.10", ".10-.18", ".18-.25", ">.25"])
    t["sess"] = t.hour.map(lambda h: "London(7-10)" if 7 <= h < 10 else ("NY(12-16)" if 12 <= h < 16 else ("Asia(0-6)" if h < 7 else "other")))
    for col in ("sweep_f", "rsi_f", "z_f", "vol_f", "er_f", "sess", "side"):
        print(f"\n--- by {col} (INS / OOS) ---")
        print(t.groupby([col, "period"], observed=True).apply(agg, include_groups=False).to_string())
