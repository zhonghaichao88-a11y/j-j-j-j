#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v5 候选：1h/4h 高周期 regime 闸门 + 15m 双引擎（趋势回踩 / 区间轨道回归）+ 工程化出场。
决策在15m收盘、次根15m开盘成交；止损以15m ATR为主并参考1h ATR下限压低成本/R。"""
import os, sys, copy
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lab_v4 as L

SYMS = L.SYMS


def base15(df):
    idx = pd.to_datetime(df.open_ms, unit="ms", utc=True)
    g = df.set_index(idx).resample("15min", label="left", closed="left")
    r = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
              close=("close", "last"), vol=("vol", "sum")).dropna()
    r["open_ms"] = (r.index.view("int64") // 10**6); r = r.reset_index(drop=True)
    o = r.open.to_numpy(float); h = r.high.to_numpy(float); l = r.low.to_numpy(float)
    c = r.close.to_numpy(float); v = r.vol.to_numpy(float)
    r["ct"] = pd.to_datetime(r.open_ms, unit="ms", utc=True) + pd.Timedelta(minutes=15)
    r["ema21"] = L.ema(c, 21); r["ema50"] = L.ema(c, 50)
    r["atr"] = L.atr(h, l, c, 14)
    r["vwap"] = L.rolling_vwap(h, l, c, v, 48)
    mid = pd.Series(c).rolling(20).mean(); sd = pd.Series(c).rolling(20).std(ddof=0)
    r["mid"] = mid.to_numpy(); r["sd"] = sd.to_numpy()
    r["up"] = (mid + 2 * sd).to_numpy(); r["dn"] = (mid - 2 * sd).to_numpy()
    r["rsi2"] = L.rsi_wilder(c, 2); r["er"] = L.kaufman_er(c, 14)
    r["ll10"] = pd.Series(l).rolling(10).min().to_numpy()
    r["hh10"] = pd.Series(h).rolling(10).max().to_numpy()
    r["eslope"] = pd.Series(r["ema21"]).pct_change(5).to_numpy()
    r["egap"] = (r["ema21"] - r["ema50"]) / np.maximum(r["atr"], 1e-12)
    # 附加 1h / 4h
    for rule, mins, tag in [("1h", 60, "1h"), ("4h", 240, "4h")]:
        gg = df.set_index(idx).resample(rule, label="left", closed="left")
        x = gg.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                   close=("close", "last"), vol=("vol", "sum")).dropna()
        x["close_time"] = x.index + pd.Timedelta(minutes=mins)
        xc = x.close.to_numpy(float); xh = x.high.to_numpy(float); xl = x.low.to_numpy(float)
        x["e21"] = L.ema(xc, 21); x["e50"] = L.ema(xc, 50)
        x["atr"] = L.atr(xh, xl, xc, 14); x["er"] = L.kaufman_er(xc, 14)
        x["eslope"] = pd.Series(x["e21"]).pct_change(5).to_numpy()
        x["egap"] = (x["e21"] - x["e50"]) / np.maximum(x["atr"], 1e-12)
        x = x.rename(columns={"egap": f"egap_{tag}", "er": f"er_{tag}", "eslope": f"eslope_{tag}"})
        keep = ["close_time", f"egap_{tag}", f"er_{tag}", f"eslope_{tag}"]
        r = pd.merge_asof(r.sort_values("ct"), x[keep].sort_values("close_time"),
                          left_on="ct", right_on="close_time", direction="backward")
        r = r.drop(columns=["close_time"]).reset_index(drop=True)
    return r


def signals(r, p):
    c = r.close.to_numpy(); o = r.open.to_numpy(); h = r.high.to_numpy(); l = r.low.to_numpy()
    e21 = r.ema21.to_numpy(); vwap = r.vwap.to_numpy()
    dn = r.dn.to_numpy(); up = r.up.to_numpy(); rsi = r.rsi2.to_numpy()
    ll = r.ll10.to_numpy(); hh = r.hh10.to_numpy(); atr = r.atr.to_numpy()
    eg1 = r.egap_1h.to_numpy(); er1 = r.er_1h.to_numpy(); sl1 = r.eslope_1h.to_numpy()
    eg4 = r.egap_4h.to_numpy(); er4 = r.er_4h.to_numpy()
    sigs = []
    for i in range(250, len(c) - 2):
        if not np.isfinite(atr[i]) or not np.isfinite(eg1[i]) or not np.isfinite(eg4[i]):
            continue
        tup = eg1[i] > p["g1"] and sl1[i] > 0 and er1[i] >= p["er1"] and eg4[i] > -p["g4"]
        tdn = eg1[i] < -p["g1"] and sl1[i] < 0 and er1[i] >= p["er1"] and eg4[i] < p["g4"]
        rng = (er1[i] < p["rng_er"] and abs(eg1[i]) < p["rng_g1"] and abs(eg4[i]) < p["rng_g4"])
        px = c[i]
        if tup:
            pull = np.min(l[i - 5:i + 1]) <= max(e21[i], vwap[i]) * 1.001 if np.isfinite(e21[i]) else False
            swp = (np.min(l[i - 2:i + 1]) < ll[i - 1] and c[i] > ll[i - 1]) if np.isfinite(ll[i - 1]) else False
            if c[i] > vwap[i] and c[i] > o[i] and (pull or swp):
                anchor = max(px - np.min(l[i - 6:i + 1]), 0)
                sigs.append(dict(i=i, side="LONG", engine="TR", anchor=anchor, regime=1))
        elif tdn:
            pull = np.max(h[i - 5:i + 1]) >= min(e21[i], vwap[i]) * 0.999 if np.isfinite(e21[i]) else False
            swp = (np.max(h[i - 2:i + 1]) > hh[i - 1] and c[i] < hh[i - 1]) if np.isfinite(hh[i - 1]) else False
            if c[i] < vwap[i] and c[i] < o[i] and (pull or swp):
                anchor = max(np.max(h[i - 6:i + 1]) - px, 0)
                sigs.append(dict(i=i, side="SHORT", engine="TR", anchor=anchor, regime=-1))
        elif rng and p["w_mr"]:
            if l[i] <= dn[i] and c[i] > dn[i] and c[i] >= o[i] and rsi[i] <= p["rsi"]:
                anchor = max(px - np.min(l[i - 6:i + 1]), 0)
                sigs.append(dict(i=i, side="LONG", engine="MR", anchor=anchor, regime=0))
            if h[i] >= up[i] and c[i] < up[i] and c[i] <= o[i] and rsi[i] >= 100 - p["rsi"]:
                anchor = max(np.max(h[i - 6:i + 1]) - px, 0)
                sigs.append(dict(i=i, side="SHORT", engine="MR", anchor=anchor, regime=0))
    # 止损/止盈
    for s in sigs:
        i = s["i"]; au = atr[i]; px = c[i]
        if s["engine"] == "MR":
            klo, khi, rr = p["mr_k"], p["mr_kcap"], p["mr_rr"]
        else:
            klo, khi, rr = p["tr_k"], p["tr_kcap"], p["tr_rr"]
        stop = float(np.clip(s["anchor"] + 0.4 * au, klo * au, khi * au))
        s["sl"] = stop / px; s["tp"] = rr * stop / px
    return sigs


P = dict(g1=0.30, er1=0.28, g4=0.5, rng_er=0.25, rng_g1=0.5, rng_g4=0.8,
         w_mr=True, rsi=20.0,
         mr_k=1.3, mr_kcap=2.4, mr_rr=1.3,
         tr_k=1.3, tr_kcap=2.4, tr_rr=1.8,
         use_partial=True, part_frac=0.5, tp1_r=0.7, trail_k=2.5,
         stag_bars=16, stag_r=0.3, max_hold=32, cooldown=2)


def agg(g):
    if not len(g):
        return pd.Series({"n": 0})
    pf = g.net_r[g.net_r > 0].sum() / max(-g.net_r[g.net_r < 0].sum(), 1e-9)
    return pd.Series({"n": len(g), "win": round(100 * (g.net_r > 0).mean(), 1),
                      "avgR": round(g.net_r.mean(), 3), "totR": round(g.net_r.sum(), 0),
                      "PF": round(pf, 2)})


def period(ts):
    if L.INS0 <= ts <= L.INS1: return "INS"
    if L.OOS0 <= ts <= L.OOS1: return "OOS"
    return "?"


def run(p, cost):
    frames = []
    for sym in SYMS:
        df = pd.read_csv(os.path.join(L.DATA, f"{sym}_5m.csv"))
        r = base15(df)
        sigs = signals(r, p)
        for s in sigs:
            s["sym"] = sym
        t = L.simulate(r, sigs, p, cost)
        frames.append(t)
    t = pd.concat(frames, ignore_index=True); t["period"] = t.ct.apply(period)
    return t


if __name__ == "__main__":
    for cost in (0.0012, 0.0008, 0.0006):
        t = run(P, cost)
        print(f"\n########## v5 15m ensemble cost={cost*100:.2f}% ##########")
        for per in ("INS", "OOS"):
            print(per, dict(agg(t[t.period == per])))
        print("ALL", dict(agg(t)), "per-day", round(len(t) / (199 * 6), 2))
        print(t.groupby("engine").apply(
            lambda g: pd.Series({"n": len(g), "win": round(100 * (g.net_r > 0).mean(), 1),
                                 "avgR": round(g.net_r.mean(), 3),
                                 "PF": round(g.net_r[g.net_r > 0].sum() / max(-g.net_r[g.net_r < 0].sum(), 1e-9), 2)}),
            include_groups=False).to_string())
        print(t.groupby(["period", "engine"]).apply(agg, include_groups=False).to_string())
        print("reasons", t.groupby("reason").net_r.agg(["count", "mean"]).round(3).to_dict())
