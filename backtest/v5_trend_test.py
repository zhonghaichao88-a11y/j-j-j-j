#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立检验：小币在 15m/1h 上是否存在可捕获的趋势 edge（标准唐奇安突破+EMA50方向+ATR吊灯，无调参=防过拟合）。
信号收盘判定、次根开盘成交；同根先判止损；成本按往返折算 R。"""
import os, sys
import numpy as np, pandas as pd
import lab_v5_small as V

OUT = {}


def resample(df, rule):
    idx = pd.to_datetime(df.open_ms, unit="ms", utc=True)
    g = df.set_index(idx).resample(rule, label="left", closed="left")
    r = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
              close=("close", "last"), vol=("vol", "sum")).dropna().reset_index()
    r["open_ms"] = (pd.to_datetime(r.iloc[:, 0]).astype("int64") // 10**6)
    return r


def atr(h, l, c, n=14):
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    return pd.Series(tr).rolling(n).mean().to_numpy()


def backtest(df, rule, cost_round, don=20, atr_k=2.0, trail_k=3.0, max_hold=96,
             use_ema=True, long_only=False, mins_per=15):
    r = resample(df, rule)
    o = r.open.to_numpy(float); h = r.high.to_numpy(float); l = r.low.to_numpy(float); c = r.close.to_numpy(float)
    t = r.open_ms.to_numpy(float); n = len(r)
    a = atr(h, l, c)
    e50 = pd.Series(c).ewm(span=50, adjust=False).mean().to_numpy()
    dhi = pd.Series(h).rolling(don).max().to_numpy()
    dlo = pd.Series(l).rolling(don).min().to_numpy()
    rows = []; busy = -1
    for i in range(don + 5, n - 1):
        if i < busy:
            continue
        au = a[i]
        if not np.isfinite(au) or au <= 0:
            continue
        up = c[i] > dhi[i - 1]
        dn = (c[i] < dlo[i - 1]) if not long_only else False
        if use_ema:
            up = up and c[i] > e50[i]
            dn = dn and c[i] < e50[i]
        side = 1 if up else (-1 if dn else 0)
        if side == 0:
            continue
        entry = float(o[i + 1])
        sl_pct = atr_k * au / c[i]
        risk = entry * sl_pct
        cost_r = cost_round / sl_pct
        stop = entry * (1 - side * sl_pct); ext = entry
        exit_px = None; reason = None; ej = None
        for j in range(i + 1, min(i + 1 + max_hold, n)):
            hi, lo, cl = float(h[j]), float(l[j]), float(c[j]); bars = j - (i + 1) + 1
            if side == 1 and lo <= stop:
                exit_px, reason, ej = stop, "TRAIL/SL", j; break
            if side == -1 and hi >= stop:
                exit_px, reason, ej = stop, "TRAIL/SL", j; break
            if side == 1:
                ext = max(ext, hi); stop = max(stop, ext - trail_k * a[j]) if np.isfinite(a[j]) else stop
            else:
                ext = min(ext, lo); stop = min(stop, ext + trail_k * a[j]) if np.isfinite(a[j]) else stop
            if bars >= max_hold:
                exit_px, reason, ej = cl, "TIME", j; break
        if exit_px is None:
            ej = min(i + max_hold, n - 1); exit_px = float(c[ej]); reason = "END"
        gross = side * (exit_px - entry) / risk
        rows.append(dict(ct=pd.to_datetime(t[i + 1], unit="ms", utc=True), side=side,
                         sl_pct=sl_pct, gross_r=gross, net_r=gross - cost_r, reason=reason))
        busy = ej + 1
    return pd.DataFrame(rows)


def agg(t, label):
    if not len(t):
        return dict(label=label, n=0)
    r = t.net_r; w = r > 0
    aw = r[r > 0].mean() if w.any() else 0; al = -r[r < 0].mean() if (~w).any() else 0
    return dict(label=label, n=len(t), win=round(100 * w.mean(), 1),
                avgR=round(r.mean(), 3), PF=round(r[r > 0].sum() / max(-r[r < 0].sum(), 1e-9), 2),
                payoff=round(aw / max(al, 1e-9), 2), totR=round(r.sum(), 0))


def period_of(ts):
    if V.INS0 <= ts <= V.INS1: return "INS"
    if V.OOS0 <= ts <= V.OOS1: return "OOS"
    return "?"


def run(universe, rule, cost, **kw):
    frames = []
    for sym in universe:
        df = pd.read_csv(os.path.join(V.DATA, f"{sym}_5m.csv"))
        t = backtest(df, rule, cost, **kw)
        if len(t):
            t["symbol"] = sym; frames.append(t)
    t = pd.concat(frames, ignore_index=True)
    t["period"] = t.ct.apply(period_of)
    out = {}
    for per in ("INS", "OOS"):
        g = t[t.period == per]; out[per] = agg(g, per)
    out["ALL"] = agg(t, "ALL")
    return out, t


if __name__ == "__main__":
    uni = sys.argv[1].split(",") if len(sys.argv) > 1 else V.UNIVERSE[:16]
    for rule, mh in (("15min", 64), ("1h", 48)):
        for cost in (0.0012, 0.0006):
            o, _ = run(uni, rule, cost, max_hold=mh)
            print(f"唐奇安趋势 {rule:4s} cost={cost*100:.2f}% 多空:",
                  "INS", o["INS"], "OOS", o["OOS"], "ALL", o["ALL"], flush=True)
        o, _ = run(uni, rule, 0.0006, max_hold=mh, long_only=True)
        print(f"唐奇安趋势 {rule:4s} cost=0.06% 仅做多:", "INS", o["INS"], "OOS", o["OOS"], "ALL", o["ALL"], flush=True)
