#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""高周期 edge 诊断：1h 趋势回踩延续 + 1h 区间极值回归。止损用 1h ATR（宽，成本/R 极低）。
信号在小时K收盘判定、次根开盘成交；同根先判止损（保守）。"""
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lab_v4 as L

MAXHOLD = 60     # 最多持 60 根1h（约2.5天）
COOL = 2


def htf_features(df, rule, mins):
    idx = pd.to_datetime(df.open_ms, unit="ms", utc=True)
    g = df.set_index(idx).resample(rule, label="left", closed="left")
    r = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
              close=("close", "last"), vol=("vol", "sum")).dropna()
    r["open_ms"] = (r.index.view("int64") // 10**6)
    r = r.reset_index(drop=True)
    o = r.open.to_numpy(float); h = r.high.to_numpy(float); l = r.low.to_numpy(float)
    c = r.close.to_numpy(float); v = r.vol.to_numpy(float)
    r["ema21"] = L.ema(c, 21); r["ema50"] = L.ema(c, 50); r["ema200"] = L.ema(c, 200)
    r["atr"] = L.atr(h, l, c, 14)
    r["er"] = L.kaufman_er(c, 14)
    mid = pd.Series(c).rolling(20).mean(); sd = pd.Series(c).rolling(20).std(ddof=0)
    r["mid"] = mid.to_numpy(); r["up"] = (mid + 2 * sd).to_numpy(); r["dn"] = (mid - 2 * sd).to_numpy()
    r["rsi2"] = L.rsi_wilder(c, 2)
    r["ll10"] = pd.Series(l).rolling(10).min().to_numpy()
    r["hh10"] = pd.Series(h).rolling(10).max().to_numpy()
    r["don_hi"] = pd.Series(h).rolling(20).max().to_numpy()
    r["don_lo"] = pd.Series(l).rolling(20).min().to_numpy()
    r["eslope"] = pd.Series(r["ema21"]).pct_change(5).to_numpy()
    r["egap"] = (r["ema21"] - r["ema50"]) / np.maximum(r["atr"], 1e-12)
    r["ct"] = pd.to_datetime(r.open_ms, unit="ms", utc=True) + pd.Timedelta(minutes=mins)
    return r


def sim(r, sigs, cost, k, rr, tstop=None):
    o = r.open.to_numpy(float); h = r.high.to_numpy(float); l = r.low.to_numpy(float)
    c = r.close.to_numpy(float); atr = r.atr.to_numpy(float); n = len(r)
    rows = []; busy = -1
    for s in sigs:
        i = s["i"]
        if i < busy:
            continue
        side = 1 if s["side"] == "LONG" else -1
        entry = o[i + 1]; au = atr[i]
        if not np.isfinite(au) or au <= 0:
            continue
        stopd = float(np.clip(s["anchor"] + 0.5 * au, k * au, (k + 1.0) * au))
        slp = stopd / entry; tpp = rr * stopd / entry
        sp = entry * (1 - side * slp); tp = entry * (1 + side * tpp)
        px = None; rsn = None; ej = None
        for j in range(i + 1, min(i + 1 + MAXHOLD, n)):
            if (side == 1 and l[j] <= sp) or (side == -1 and h[j] >= sp):
                px, rsn, ej = sp, "SL", j; break
            if (side == 1 and h[j] >= tp) or (side == -1 and l[j] <= tp):
                px, rsn, ej = tp, "TP", j; break
            if tstop and (j - i) >= tstop:
                px, rsn, ej = c[j], "TIME", j; break
        if px is None:
            ej = min(i + MAXHOLD, n - 1); px = c[ej]; rsn = "END"
        gross = side * (px - entry) / (entry * slp); net = gross - cost / slp
        rows.append(dict(eng=s["eng"], side=s["side"], ct=r.ct.iloc[i + 1], gross=gross,
                         net=net, rsn=rsn, sl_pct=slp, bars=ej - i))
        busy = ej + 1 + COOL
    return pd.DataFrame(rows)


def trend_signals(r):
    c = r.close.to_numpy(); h = r.high.to_numpy(); l = r.low.to_numpy()
    e21 = r.ema21.to_numpy(); e50 = r.ema50.to_numpy(); egap = r.egap.to_numpy()
    slope = r.eslope.to_numpy(); er = r.er.to_numpy(); ll = r.ll10.to_numpy(); hh = r.hh10.to_numpy()
    sigs = []
    for i in range(210, len(c) - 2):
        if not np.isfinite(egap[i]):
            continue
        # 多头趋势：EMA多头排列+向上斜率+ER达标；回踩EMA21/扫近低后强收回
        if egap[i] > 0.3 and slope[i] > 0 and er[i] >= 0.28 and c[i] > e50[i]:
            pull = np.min(l[i - 4:i + 1]) <= e21[i] * 1.002
            sweep = np.min(l[i - 2:i + 1]) < ll[i - 1] and c[i] > ll[i - 1]
            if c[i] > e21[i] and c[i] >= o_(r, i) and (pull or sweep):
                sigs.append(dict(i=i, side="LONG", eng="TR", anchor=max(c[i] - np.min(l[i - 6:i + 1]), 0)))
        if egap[i] < -0.3 and slope[i] < 0 and er[i] >= 0.28 and c[i] < e50[i]:
            pull = np.max(h[i - 4:i + 1]) >= e21[i] * 0.998
            sweep = np.max(h[i - 2:i + 1]) > hh[i - 1] and c[i] < hh[i - 1]
            if c[i] < e21[i] and c[i] <= o_(r, i) and (pull or sweep):
                sigs.append(dict(i=i, side="SHORT", eng="TR", anchor=max(np.max(h[i - 6:i + 1]) - c[i], 0)))
    return sigs


def o_(r, i):
    return r.open.to_numpy()[i]


def range_signals(r):
    c = r.close.to_numpy(); o = r.open.to_numpy(); h = r.high.to_numpy(); l = r.low.to_numpy()
    dn = r.dn.to_numpy(); up = r.up.to_numpy(); rsi = r.rsi2.to_numpy()
    egap = r.egap.to_numpy(); er = r.er.to_numpy(); ll = r.ll10.to_numpy(); hh = r.hh10.to_numpy()
    sigs = []
    for i in range(210, len(c) - 2):
        if not np.isfinite(egap[i]):
            continue
        rangey = er[i] < 0.25 and abs(egap[i]) < 0.5
        if not rangey:
            continue
        if l[i] <= dn[i] and c[i] > dn[i] and c[i] >= o[i] and rsi[i] <= 20:
            sigs.append(dict(i=i, side="LONG", eng="MR", anchor=max(c[i] - np.min(l[i - 6:i + 1]), 0)))
        if h[i] >= up[i] and c[i] < up[i] and c[i] <= o[i] and rsi[i] >= 80:
            sigs.append(dict(i=i, side="SHORT", eng="MR", anchor=max(np.max(h[i - 6:i + 1]) - c[i], 0)))
    return sigs


def agg(g):
    if not len(g):
        return pd.Series({"n": 0})
    pf = g.net[g.net > 0].sum() / max(-g.net[g.net < 0].sum(), 1e-9)
    return pd.Series({"n": len(g), "win": round(100 * (g.net > 0).mean(), 1),
                      "grossR": round(g.gross.mean(), 3), "netR": round(g.net.mean(), 3),
                      "totR": round(g.net.sum(), 0), "PF": round(pf, 2),
                      "medSL%": round(100 * g.sl_pct.median(), 2)})


def period(ts):
    if L.INS0 <= ts <= L.INS1: return "INS"
    if L.OOS0 <= ts <= L.OOS1: return "OOS"
    return "?"


def main(cost=0.0012):
    for rule, mins, tag in [("1h", 60, "1h"), ("4h", 240, "4h")]:
        frames = []
        for sym in L.SYMS:
            df = pd.read_csv(os.path.join(L.DATA, f"{sym}_5m.csv"))
            r = htf_features(df, rule, mins)
            sigs = trend_signals(r) + range_signals(r)
            for k in (1.5,):
                for rr in (1.5, 2.0, 3.0):
                    t = sim(r, sigs, cost, k, rr)
                    t["sym"] = sym; t["k"] = k; t["rr"] = rr
                    frames.append(t)
        t = pd.concat(frames, ignore_index=True); t["period"] = t.ct.apply(period)
        print(f"\n================= {tag}  cost={cost*100:.2f}% =================")
        for rr in (1.5, 2.0, 3.0):
            g0 = t[t.rr == rr]
            for eng in ("TR", "MR"):
                g = g0[g0.eng == eng]
                for per in ("INS", "OOS"):
                    print(f"rr{rr} {eng} {per}", dict(agg(g[g.period == per])))
                print("  ALL", dict(agg(g)))
        print("by symbol (rr2, TR):")
        g = t[(t.rr == 2.0) & (t.eng == "TR")]
        print(g.groupby("sym").apply(lambda x: agg(x), include_groups=False).to_string())


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 0.0012)
