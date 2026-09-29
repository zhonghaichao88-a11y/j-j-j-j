#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立检验：小币 15m「耗竭回归」(更高周期、更宽止损、更少更稳)。
1H/4H 区间 regime 闸门 + 15m 布林极值收回 + RSI2 + (扫损/极端RSI/放量高潮)，回归会话VWAP。
收盘判定、次根开盘成交、同根先判止损、分批(0.5/0.8档)+保本、4小时到期；MR 不做横盘/反转砍仓。"""
import os, sys
import numpy as np, pandas as pd
import lab_v5_small as V

PARTIAL = [(0.50, 0.30), (0.80, 0.30)]


def base15(df):
    idx = pd.to_datetime(df.open_ms, unit="ms", utc=True)
    g = df.set_index(idx).resample("15min", label="left", closed="left")
    r = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
              close=("close", "last"), vol=("vol", "sum")).dropna()
    r["open_ms"] = (r.index.view("int64") // 10**6)
    return r.reset_index(drop=True)


def htf(r, rule, mins, tag):
    idx = pd.to_datetime(r.open_ms, unit="ms", utc=True)
    g = r.set_index(idx).resample(rule, label="left", closed="left")
    x = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
              close=("close", "last"), vol=("vol", "sum")).dropna()
    x["ct"] = x.index + pd.Timedelta(minutes=mins)
    c = x.close.to_numpy(float); h = x.high.to_numpy(float); l = x.low.to_numpy(float)
    e21 = V.L.ema(c, 21); e50 = V.L.ema(c, 50); at = V.L.atr(h, l, c, 14)
    x[f"egap_{tag}"] = (e21 - e50) / np.maximum(at, 1e-12)
    x[f"er_{tag}"] = V.L.kaufman_er(c, 14)
    x[f"sl_{tag}"] = pd.Series(e21).pct_change(5).to_numpy()
    rr = r.copy()
    rr["ct"] = pd.to_datetime(rr.open_ms, unit="ms", utc=True) + pd.Timedelta(minutes=15)
    m = pd.merge_asof(rr.sort_values("ct"), x[["ct", f"egap_{tag}", f"er_{tag}", f"sl_{tag}"]].sort_values("ct"),
                      on="ct", direction="backward")
    return m[[f"egap_{tag}", f"er_{tag}", f"sl_{tag}"]].to_numpy(float).T


def backtest(df5, p, cost):
    r = base15(df5)
    o = r.open.to_numpy(float); h = r.high.to_numpy(float); l = r.low.to_numpy(float)
    c = r.close.to_numpy(float); v = r.vol.to_numpy(float); t = r.open_ms.to_numpy(float)
    n = len(r)
    mid = pd.Series(c).rolling(20).mean().to_numpy(); sd = pd.Series(c).rolling(20).std(ddof=0).to_numpy()
    up = mid + 2 * sd; dn = mid - 2 * sd
    rsi = V.L.rsi_wilder(c, 2); at = V.L.atr(h, l, c, 14)
    typ = (h + l + c) / 3
    vwap = (pd.Series(typ * v).rolling(32).sum() / pd.Series(v).rolling(32).sum()).to_numpy()
    medv = pd.Series(v).rolling(21).median().to_numpy()
    ll = pd.Series(l).rolling(10).min().to_numpy(); hh = pd.Series(h).rolling(10).max().to_numpy()
    eg1, er1, sl1 = htf(r, "1h", 60, "1")
    eg4, er4, sl4 = htf(r, "4h", 240, "4")
    rows = []; busy = -1
    for i in range(260, n - 1):
        if i < busy or not np.isfinite(at[i]) or at[i] <= 0:
            continue
        # 区间 regime：1H/4H 都不趋势
        rng = (er1[i] < p["chop1"] and abs(eg1[i]) < p["gap1"] and abs(eg4[i]) < p["gap4"])
        if not rng:
            continue
        px = c[i]; au = at[i]; rngbar = max(h[i] - l[i], 1e-12)
        sweep_lo = np.min(l[i - 2:i + 1]) < ll[i - 1]; sweep_hi = np.max(h[i - 2:i + 1]) > hh[i - 1]
        climax = medv[i] > 0 and v[i] >= p["climax"] * medv[i]
        side = 0
        if (l[i] <= dn[i] * (1 + p["tol"]) and c[i] > dn[i] and (c[i] - l[i]) / rngbar >= 0.5 and c[i] >= o[i]
                and rsi[i] <= p["rsi_o"] and (sweep_lo or rsi[i] <= p["rsi_x"] or climax)):
            side = 1
        if (h[i] >= up[i] * (1 - p["tol"]) and c[i] < up[i] and (h[i] - c[i]) / rngbar >= 0.5 and c[i] <= o[i]
                and rsi[i] >= 100 - p["rsi_o"] and (sweep_hi or rsi[i] >= 100 - p["rsi_x"] or climax)):
            side = -1
        if side == 0:
            continue
        anchor = max(side * (px - (np.min(l[i - 6:i + 1]) if side == 1 else np.max(h[i - 6:i + 1]))), 0)
        stop = float(np.clip(anchor + 0.5 * au, p["klo"] * au, p["khi"] * au))
        sl_pct = stop / px
        if sl_pct < p["floor"]:
            continue
        vw_d = max(side * (vwap[i] - px), 0) if np.isfinite(vwap[i]) else 0
        tp_dist = vw_d if vw_d >= p["rr"] * stop else p["rr"] * stop
        tp_pct = tp_dist / px
        entry = float(o[i + 1]);
        if not np.isfinite(entry) or entry <= 0:
            continue
        risk = entry * sl_pct; cost_r = cost / sl_pct
        if side == 1:
            stop_px = entry * (1 - sl_pct); tp_px = entry * (1 + tp_pct); be_px = entry * 1.0005
        else:
            stop_px = entry * (1 + sl_pct); tp_px = entry * (1 - tp_pct); be_px = entry * 0.9995
        levels = []
        for prog, frac in PARTIAL:
            tgt = max(0.002, min(tp_pct * prog, 0.25))
            levels.append([entry * (1 + side * tgt) if tgt < tp_pct else None, frac, False])
        realized = 0.0; rem = 1.0; part_cost = 0.0; be = False
        exit_px = None; reason = None; ej = None; runner = None
        for j in range(i + 1, min(i + 1 + p["maxhold"], n)):
            hi, lo, cl = float(h[j]), float(l[j]), float(c[j])
            if (side == 1 and lo <= stop_px) or (side == -1 and hi >= stop_px):
                exit_px, ej, runner = stop_px, j, side * (stop_px - entry) / risk
                nd = sum(x[2] for x in levels); reason = "SL" if not (be or nd) else ("BE" if nd <= 1 else "PROTECT"); break
            for k, (lvl, frac, done) in enumerate(levels):
                if done or lvl is None:
                    continue
                if (side == 1 and hi >= lvl) or (side == -1 and lo <= lvl):
                    levels[k][2] = True; realized += frac * side * (lvl - entry) / risk; rem -= frac
                    part_cost += frac * (cost / 2) / sl_pct
                    if k == 0:
                        be = True; stop_px = max(stop_px, be_px) if side == 1 else min(stop_px, be_px)
                    else:
                        fp = max(0.002, min(tp_pct * 0.5, 0.25)); fpx = entry * (1 + side * fp)
                        stop_px = max(stop_px, fpx) if side == 1 else min(stop_px, fpx)
                break
            if (side == 1 and hi >= tp_px) or (side == -1 and lo <= tp_px):
                exit_px, ej, runner = tp_px, j, side * (tp_px - entry) / risk; reason = "TP_RUNNER"; break
            if side == 1:
                if not be and hi >= entry + risk: be = True; stop_px = be_px
            else:
                if not be and lo <= entry - risk: be = True; stop_px = be_px
            if j - i >= p["maxhold"]:
                exit_px, ej, runner = cl, j, side * (cl - entry) / risk; reason = "TIME"; break
        if exit_px is None:
            ej = min(i + p["maxhold"], n - 1); exit_px = float(c[ej]); runner = side * (exit_px - entry) / risk; reason = "END"
        gross = realized + max(rem, 0) * runner
        rows.append(dict(ct=pd.to_datetime(t[i + 1], unit="ms", utc=True), side=side, sl_pct=sl_pct,
                         gross_r=gross, net_r=gross - cost_r - part_cost, reason=reason))
        busy = ej + 1 + p.get("cooldown", 1)
    return pd.DataFrame(rows)


def agg(t, lab):
    if not len(t):
        return dict(label=lab, n=0)
    r = t.net_r; w = r > 0
    aw = r[r > 0].mean() if w.any() else 0; al = -r[r < 0].mean() if (~w).any() else 0
    return dict(label=lab, n=len(t), win=round(100 * w.mean(), 1), avgR=round(r.mean(), 3),
                PF=round(r[r > 0].sum() / max(-r[r < 0].sum(), 1e-9), 2),
                payoff=round(aw / max(al, 1e-9), 2), totR=round(r.sum(), 0))


def run(universe, p, cost):
    fr = []
    for sym in universe:
        df = pd.read_csv(os.path.join(V.DATA, f"{sym}_5m.csv"))
        t = backtest(df, p, cost)
        if len(t):
            t["symbol"] = sym; fr.append(t)
    t = pd.concat(fr, ignore_index=True)
    t["period"] = t.ct.apply(lambda x: "INS" if V.INS0 <= x <= V.INS1 else ("OOS" if V.OOS0 <= x <= V.OOS1 else "?"))
    o = {g: agg(t[t.period == g], g) for g in ("INS", "OOS")}; o["ALL"] = agg(t, "ALL")
    return o, t


P = dict(chop1=0.30, gap1=0.6, gap4=1.0, tol=0.0005, rsi_o=20.0, rsi_x=10.0, climax=1.8,
         klo=2.0, khi=3.5, rr=1.3, floor=0.006, maxhold=16, cooldown=1)

if __name__ == "__main__":
    uni = V.UNIVERSE
    for rr, klo, khi, clx in [(1.1, 2.0, 3.5, 1.8), (1.3, 2.0, 3.5, 1.8), (1.6, 2.0, 3.5, 1.8),
                              (1.3, 2.5, 4.5, 1.8), (1.3, 2.0, 3.5, 1.4)]:
        p = dict(P); p.update(rr=rr, klo=klo, khi=khi, climax=clx)
        line = f"rr={rr} k=[{klo},{khi}] clx={clx}: "
        for cost in (0.0006, 0.0004):
            o, _ = run(uni, p, cost)
            line += f"[{cost*100:.2f}%] INS{o['INS']} OOS{o['OOS']}  "
        print(line, flush=True)
