#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ALPHA-X FAST v4 研究台（只读研究，不下单）。

目的：在真实 5m K线上，快速、无未来函数地筛选“ regime 切换 + 双引擎 + 出场工程 + 成本档 ”组合。
成交/风控口径与生产回测器 backtest/fast_backtest.py 对齐（保守）：
  - 信号在第 i 根 5m【收盘】判定（所有指标只用 <=i 的已收盘K），第 i+1 根【开盘】成交；
  - 同一根K同时触及止损/止盈，保守按【先打止损】；
  - 支持分批止盈(tp1 锁利)→止损移保本→剩余仓位 runner(固定tp2 或吊灯移动止损)；
  - 横盘时间止损、最大持仓、平仓冷却、往返成本(可按 maker/taker 设档)。
研究台只用于【筛想法】；最终入选逻辑必须移植进生产 alpha_fast_mode.py，
再用 fast_backtest.py 直接 import 生产 _build_decision 复核，以生产口径为准。

用法：
  python3 lab_v4.py                 # 跑默认 v4 组合，INS/OOS 全报告
  python3 lab_v4.py --sweep         # 粗网格稳健性扫描
"""
from __future__ import annotations
import argparse, itertools, os, sys
import numpy as np
import pandas as pd

BT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BT, "data")
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "LINKUSDT"]
INS0, INS1 = pd.Timestamp("2026-03-01", tz="UTC"), pd.Timestamp("2026-06-30 23:59", tz="UTC")
OOS0, OOS1 = pd.Timestamp("2026-07-01", tz="UTC"), pd.Timestamp("2026-09-15 23:59", tz="UTC")


# ------------------------------ 指标（全部向量化，只用当根及以前数据） ------------------------------
def ema(x, n):
    return pd.Series(x).ewm(span=n, adjust=False, min_periods=n).mean().to_numpy()


def rsi_wilder(c, n):
    d = np.diff(c, prepend=c[0])
    up = np.maximum(d, 0.0); dn = np.maximum(-d, 0.0)
    au = pd.Series(up).ewm(alpha=1 / n, adjust=False, min_periods=n).mean().to_numpy()
    ad = pd.Series(dn).ewm(alpha=1 / n, adjust=False, min_periods=n).mean().to_numpy()
    rs = au / np.maximum(ad, 1e-12)
    return 100 - 100 / (1 + rs)


def atr(h, l, c, n=14):
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    return pd.Series(tr).rolling(n, min_periods=n).mean().to_numpy()


def kaufman_er(c, n):
    change = np.abs(c - np.roll(c, n))
    change[:n] = np.nan
    path = pd.Series(np.abs(np.diff(c, prepend=c[0]))).rolling(n).sum().to_numpy()
    er = change / np.maximum(path, 1e-12)
    return np.nan_to_num(er, nan=0.0)


def rolling_vwap(h, l, c, v, k):
    typ = (h + l + c) / 3.0
    pv = typ * v
    spv = pd.Series(pv).rolling(k).sum().to_numpy()
    sv = pd.Series(v).rolling(k).sum().to_numpy()
    return np.where(sv > 1e-12, spv / np.maximum(sv, 1e-12), c)


def resample_closed(df, rule, mins):
    """重采样，并把时间戳对齐到该根K的【收盘时刻】，便于 merge_asof 杜绝未来函数。"""
    idx = pd.to_datetime(df.open_ms, unit="ms", utc=True)
    g = df.set_index(idx).resample(rule, label="left", closed="left")
    o = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
              close=("close", "last"), vol=("vol", "sum")).dropna()
    o["close_time"] = o.index + pd.Timedelta(minutes=mins)
    return o.reset_index(drop=True)


def build_features(df):
    """在 5m 主表上补齐全部特征；15m/1h 特征按收盘时刻 merge_asof（只用已收盘高周期K）。"""
    d = df.copy().reset_index(drop=True)
    o = d.open.to_numpy(float); h = d.high.to_numpy(float); l = d.low.to_numpy(float)
    c = d.close.to_numpy(float); v = d.vol.to_numpy(float)
    d["ct"] = pd.to_datetime(d.open_ms, unit="ms", utc=True) + pd.Timedelta(minutes=5)

    # --- 5m 原生特征 ---
    d["ema21"] = ema(c, 21); d["ema50"] = ema(c, 50); d["ema200"] = ema(c, 200)
    d["atr"] = atr(h, l, c, 14)
    d["vwap"] = rolling_vwap(h, l, c, v, 96)          # ~8h 滚动会话 VWAP
    mid = pd.Series(c).rolling(20).mean()
    sd = pd.Series(c).rolling(20).std(ddof=0)
    d["bb_mid"] = mid.to_numpy(); d["bb_sd"] = sd.to_numpy()
    d["bb_up"] = (mid + 2.0 * sd).to_numpy(); d["bb_dn"] = (mid - 2.0 * sd).to_numpy()
    d["rsi2"] = rsi_wilder(c, 2); d["rsi14"] = rsi_wilder(c, 14)
    d["er"] = kaufman_er(c, 14)
    d["don_hi20"] = pd.Series(h).rolling(20).max().to_numpy()
    d["don_lo20"] = pd.Series(l).rolling(20).min().to_numpy()
    d["hh10"] = pd.Series(h).rolling(10).max().to_numpy()
    d["ll10"] = pd.Series(l).rolling(10).min().to_numpy()
    d["hh3"] = pd.Series(h).rolling(3).max().to_numpy()
    d["ll3"] = pd.Series(l).rolling(3).min().to_numpy()
    d["medvol21"] = pd.Series(v).rolling(21).median().to_numpy()

    # --- 15m / 1h 特征，按收盘时刻向后对齐（无未来） ---
    for tf, mins, tag in [("15min", 15, "15"), ("1h", 60, "1h")]:
        r = resample_closed(d, tf, mins)
        rh = r.high.to_numpy(float); rl = r.low.to_numpy(float); rc = r.close.to_numpy(float)
        r[f"e21_{tag}"] = ema(rc, 21); r[f"e50_{tag}"] = ema(rc, 50)
        r[f"atr_{tag}"] = atr(rh, rl, rc, 14)
        r[f"er_{tag}"] = kaufman_er(rc, 14)
        r[f"eslope_{tag}"] = pd.Series(r[f"e21_{tag}"]).pct_change(5).to_numpy()
        r[f"egap_{tag}"] = (r[f"e21_{tag}"] - r[f"e50_{tag}"]) / np.maximum(r[f"atr_{tag}"], 1e-12)
        r[f"don_hi_{tag}"] = pd.Series(rh).rolling(20).max().to_numpy()
        r[f"don_lo_{tag}"] = pd.Series(rl).rolling(20).min().to_numpy()
        r[f"rng_{tag}"] = (r[f"don_hi_{tag}"] - r[f"don_lo_{tag}"]) / np.maximum(r[f"atr_{tag}"], 1e-12)
        keep = ["close_time", f"e21_{tag}", f"e50_{tag}", f"atr_{tag}", f"er_{tag}",
                f"eslope_{tag}", f"egap_{tag}", f"don_hi_{tag}", f"don_lo_{tag}", f"rng_{tag}"]
        d = pd.merge_asof(d.sort_values("ct"), r[keep].sort_values("close_time"),
                          left_on="ct", right_on="close_time", direction="backward")
        d = d.drop(columns=["close_time"]).reset_index(drop=True)
    return d


def regime_row(x, p):
    """双闸门：返回 1=多头趋势 / -1=空头趋势 / 0=区间 / 9=中性不做。只用当根已知量。"""
    er15 = x[f"er_15"]; egap15 = x[f"egap_15"]; eslope15 = x[f"eslope_15"]
    er1h = x[f"er_1h"]; egap1h = x[f"egap_1h"]
    px = x.close
    if not np.isfinite(er15) or not np.isfinite(egap1h):
        return 9
    tup = (egap15 > p["trend_gap"] and eslope15 > 0 and er15 >= p["trend_er"]
           and px > x.e21_15 and egap1h > -p["htf_gap"])
    tdn = (egap15 < -p["trend_gap"] and eslope15 < 0 and er15 >= p["trend_er"]
           and px < x.e21_15 and egap1h < p["htf_gap"])
    if tup:
        return 1
    if tdn:
        return -1
    range_ok = (er15 < p["chop_er"] and er1h < p["chop_er_1h"]
                and abs(egap1h) < p["htf_range_gap"])
    return 0 if range_ok else 9


# ------------------------------ 信号引擎 ------------------------------ #
def raw_signals(d, p):
    """只产出原始信号点（方向/引擎/失效锚点距离），不含止损止盈，便于 edge 网格复用。"""
    n = len(d)
    reg = np.full(n, 9, dtype=int)
    for i in range(n):
        reg[i] = regime_row(d.iloc[i], p)
    d["regime"] = reg

    c = d.close.to_numpy(); h = d.high.to_numpy(); l = d.low.to_numpy(); o = d.open.to_numpy()
    v = d.vol.to_numpy(); medv = d.medvol21.to_numpy(); bbsd = d.bb_sd.to_numpy()
    a5 = d.atr.to_numpy()
    hours = d.ct.dt.hour.to_numpy()
    vwap = d.vwap.to_numpy(); bbdn = d.bb_dn.to_numpy(); bbup = d.bb_up.to_numpy()
    bbm = d.bb_mid.to_numpy(); rsi2 = d.rsi2.to_numpy(); ll10 = d.ll10.to_numpy(); hh10 = d.hh10.to_numpy()
    e21_15 = d.e21_15.to_numpy()
    sigs = []
    for i in range(250, n - 2):
        r = reg[i]
        px = c[i]
        # ---- 引擎 M：区间极值均值回归（高胜率引擎，天然 maker 挂单思维） ----
        if r == 0 and p["w_meanrev"]:
            sweep_lo = bool(np.min(l[i - 2:i + 1]) < ll10[i - 1]) if np.isfinite(ll10[i - 1]) else False
            reclaim_up = (l[i] <= bbdn[i] * (1 + p["band_touch"]) and c[i] > bbdn[i] and c[i] >= o[i])
            if reclaim_up and rsi2[i] <= p["rsi_ovs"] and (sweep_lo if p["mr_sweep"] else True):
                anchor = max(px - np.min(l[i - 6:i + 1]), 0.0)
                z = (vwap[i] - px) / max(bbsd[i], a5[i] * 0.5, 1e-12) if np.isfinite(vwap[i]) else 0.0
                sigs.append(dict(i=i, side="LONG", engine="MR", regime=r, anchor=anchor,
                                 vwap_t=max(vwap[i] - px, 0.0) if np.isfinite(vwap[i]) else 0.0,
                                 sweep=sweep_lo, rsi=rsi2[i], z=z, hour=int(hours[i]),
                                 volr=v[i] / max(medv[i], 1e-12), er15=float(d.er_15.iloc[i]),
                                 er1h=float(d.er_1h.iloc[i])))
            sweep_hi = bool(np.max(h[i - 2:i + 1]) > hh10[i - 1]) if np.isfinite(hh10[i - 1]) else False
            reclaim_dn = (h[i] >= bbup[i] * (1 - p["band_touch"]) and c[i] < bbup[i] and c[i] <= o[i])
            if reclaim_dn and rsi2[i] >= p["rsi_obv"] and (sweep_hi if p["mr_sweep"] else True):
                anchor = max(np.max(h[i - 6:i + 1]) - px, 0.0)
                z = (px - vwap[i]) / max(bbsd[i], a5[i] * 0.5, 1e-12) if np.isfinite(vwap[i]) else 0.0
                sigs.append(dict(i=i, side="SHORT", engine="MR", regime=r, anchor=anchor,
                                 vwap_t=max(px - vwap[i], 0.0) if np.isfinite(vwap[i]) else 0.0,
                                 sweep=sweep_hi, rsi=100 - rsi2[i], z=z, hour=int(hours[i]),
                                 volr=v[i] / max(medv[i], 1e-12), er15=float(d.er_15.iloc[i]),
                                 er1h=float(d.er_1h.iloc[i])))
        # ---- 引擎 T：趋势回踩/扫损延续 ----
        if r in (1, -1) and p["w_trend"]:
            up = r == 1
            if up:
                touch = np.min(l[i - 5:i + 1]) <= max(e21_15[i], vwap[i]) * 1.001 if np.isfinite(e21_15[i]) else False
                sweep = (np.min(l[i - 2:i + 1]) < ll10[i - 1] and c[i] > ll10[i - 1]
                         and (c[i] - l[i]) / max(h[i] - l[i], 1e-12) >= 0.55) if np.isfinite(ll10[i - 1]) else False
                if c[i] > vwap[i] and c[i] > o[i] and (touch or sweep):
                    anchor = max(px - np.min(l[i - 6:i + 1]), 0.0)
                    sigs.append(dict(i=i, side="LONG", engine="TR", regime=r, anchor=anchor, vwap_t=0.0))
            else:
                touch = np.max(h[i - 5:i + 1]) >= min(e21_15[i], vwap[i]) * 0.999 if np.isfinite(e21_15[i]) else False
                sweep = (np.max(h[i - 2:i + 1]) > hh10[i - 1] and c[i] < hh10[i - 1]
                         and (h[i] - c[i]) / max(h[i] - l[i], 1e-12) >= 0.55) if np.isfinite(hh10[i - 1]) else False
                if c[i] < vwap[i] and c[i] < o[i] and (touch or sweep):
                    anchor = max(np.max(h[i - 6:i + 1]) - px, 0.0)
                    sigs.append(dict(i=i, side="SHORT", engine="TR", regime=r, anchor=anchor, vwap_t=0.0))
    return d, sigs


def attach_levels(d, sigs, p):
    """统一用 15m ATR 做止损波动单位（与生产 v3 一致，压低成本/R），RR 由参数决定。"""
    c = d.close.to_numpy(); atr15 = d.atr_15.to_numpy()
    for s in sigs:
        i = s["i"]; px = c[i]; au = atr15[i]
        if not np.isfinite(au) or au <= 0:
            s["sl"] = 0.0; s["tp"] = 0.0; continue
        if s["engine"] == "MR":
            k_lo, k_hi, rr = p["mr_sl_k"], p["mr_sl_cap"], p["mr_rr"]
        else:
            k_lo, k_hi, rr = p["tr_sl_k"], p["tr_sl_cap"], p["tr_rr"]
        stop = float(np.clip(s["anchor"] + 0.4 * au, k_lo * au, k_hi * au))
        # MR 优先把 VWAP 当目标（若满足最低RR），否则按固定RR兜底
        if s["engine"] == "MR" and p.get("mr_target_vwap", True) and s["vwap_t"] >= rr * stop:
            tp = s["vwap_t"]
        else:
            tp = rr * stop
        s["sl"] = stop / px; s["tp"] = tp / px
    return sigs


def gen_signals(d, p):
    d, sigs = raw_signals(d, p)
    sigs = attach_levels(d, sigs, p)
    return d, sigs


# ------------------------------ 成交模拟（保守，支持分批/保本/吊灯） ------------------------------ #
def simulate(d, sigs, p, cost_round):
    o = d.open.to_numpy(float); h = d.high.to_numpy(float); l = d.low.to_numpy(float)
    c = d.close.to_numpy(float); a = d.atr.to_numpy(float)
    t = d.open_ms.to_numpy(float); n = len(d)
    max_hold = p["max_hold"]; cooldown = p["cooldown"]
    part = p["part_frac"]; r1 = p["tp1_r"]; use_part = p["use_partial"]; trail_k = p["trail_k"]
    stag_bars = p["stag_bars"]; stag_r = p["stag_r"]
    trades = []
    busy_until = -1
    for s in sigs:
        i = s["i"]
        if i < busy_until:
            continue
        side = 1 if s["side"] == "LONG" else -1
        entry = float(o[i + 1])
        if not np.isfinite(entry) or entry <= 0:
            continue
        sl_pct = float(s["sl"]); tp_pct = float(s["tp"])
        if sl_pct <= 0 or tp_pct <= 0:
            continue
        risk = entry * sl_pct
        cost_r = cost_round / sl_pct
        if side == 1:
            stop0 = entry * (1 - sl_pct); tp_full = entry * (1 + tp_pct)
        else:
            stop0 = entry * (1 + sl_pct); tp_full = entry * (1 - tp_pct)
        # 分批参数
        if use_part:
            if side == 1:
                tp1_px = entry + r1 * risk
            else:
                tp1_px = entry - r1 * risk
            q1 = part
        else:
            tp1_px = None; q1 = 0.0
        stop = stop0; be = False; partial_done = False; max_gain = 0.0
        exit_px = None; reason = None; ej = None
        runner_r = None
        peak = entry
        for j in range(i + 1, min(i + 1 + max_hold, n)):
            hi = float(h[j]); lo = float(l[j]); cl = float(c[j])
            bars = j - (i + 1) + 1
            # 1) 止损（同根最先判，保守）。分批后 stop 可能=保本
            hit_sl = (side == 1 and lo <= stop) or (side == -1 and hi >= stop)
            if hit_sl:
                if not partial_done:
                    exit_px, reason, ej = stop, "SL", j; runner_r = -1.0
                else:
                    exit_px, reason, ej = stop, ("BE" if be else "SL"), j
                    # 剩余仓位在保本/吊灯位出场
                    runner_r = side * (stop - entry) / risk
                break
            # 2) 第一批止盈
            if (not partial_done) and tp1_px is not None:
                hit1 = (side == 1 and hi >= tp1_px) or (side == -1 and lo <= tp1_px)
                if hit1:
                    partial_done = True; be = True
                    stop = entry * (1 + side * 2 * cost_round)   # 移到保本(含往返成本)
                    peak = tp1_px
            # 3) 全额止盈（未分批时即唯一止盈；分批后为 runner 终单）
            hit_tp = (side == 1 and hi >= tp_full) or (side == -1 and lo <= tp_full)
            if hit_tp:
                exit_px, reason, ej = tp_full, ("TP" if not partial_done else "TP2"), j
                runner_r = side * (tp_full - entry) / risk
                break
            # 4) 浮盈/峰值更新 + 吊灯移动（仅在分批锁利后启动）
            if side == 1:
                max_gain = max(max_gain, (hi - entry) / risk); peak = max(peak, hi)
                if (not be) and hi >= entry + risk:
                    be = True; stop = entry * (1 + 2 * cost_round)
                if partial_done and trail_k > 0 and np.isfinite(a[j]):
                    stop = max(stop, peak - trail_k * a[j])
            else:
                max_gain = max(max_gain, (entry - lo) / risk); peak = min(peak, lo)
                if (not be) and lo <= entry - risk:
                    be = True; stop = entry * (1 - 2 * cost_round)
                if partial_done and trail_k > 0 and np.isfinite(a[j]):
                    stop = min(stop, peak + trail_k * a[j])
            # 5) 横盘时间止损
            if bars >= stag_bars and max_gain < stag_r:
                exit_px, reason, ej = cl, "STAG", j; runner_r = side * (cl - entry) / risk; break
            if bars >= max_hold:
                exit_px, reason, ej = cl, "TIME", j; runner_r = side * (cl - entry) / risk; break
        if exit_px is None:
            j = min(i + max_hold, n - 1); exit_px = float(c[j]); ej = j; reason = "END"
            runner_r = side * (exit_px - entry) / risk
        # 组合净 R（分批 + runner），成本对全额仓位收取一次往返
        if use_part and partial_done:
            gross_r = q1 * r1 + (1 - q1) * runner_r
        else:
            gross_r = runner_r
        net_r = gross_r - cost_r
        trades.append(dict(symbol=s.get("sym", ""), ct=d.ct.iloc[i + 1], side=s["side"], engine=s["engine"],
                           regime=int(s["regime"]), sl_pct=sl_pct, tp_pct=tp_pct,
                           entry=entry, exit=exit_px, reason=reason, bars=int(ej - (i + 1) + 1),
                           gross_r=gross_r, cost_r=cost_r, net_r=net_r,
                           partial=bool(use_part and partial_done)))
        busy_until = ej + 1 + cooldown
    return pd.DataFrame(trades)


# ------------------------------ 汇总 ------------------------------ #
def metrics(t, label, days):
    if not len(t):
        return dict(label=label, n=0)
    r = t.net_r
    w = (r > 0); lo = (r < 0)
    pf = r[r > 0].sum() / max(-r[r < 0].sum(), 1e-9)
    cum = r.cumsum(); dd = (cum - cum.cummax()).min()
    return dict(label=label, n=len(t), per_day=round(len(t) / max(days, 1), 2),
                win=round(100 * w.mean(), 1), avgR=round(r.mean(), 3), totR=round(r.sum(), 1),
                PF=round(pf, 2), maxDD=round(dd, 1),
                avgBars=round(t.bars.mean(), 1))


def period_of(ts):
    if INS0 <= ts <= INS1: return "INS"
    if OOS0 <= ts <= OOS1: return "OOS"
    return "?"


def run(p, cost_round, symbols=None, verbose=True):
    symbols = symbols or SYMS
    allt = []
    for sym in symbols:
        df = pd.read_csv(os.path.join(DATA, f"{sym}_5m.csv"))
        d = build_features(df)
        d, sigs = gen_signals(d, p)
        for s in sigs:
            s["sym"] = sym
        t = simulate(d, sigs, p, cost_round)
        allt.append(t)
    t = pd.concat(allt, ignore_index=True)
    t["period"] = t.ct.apply(period_of)
    days = 199.0 * len(symbols)
    if verbose:
        print(f"\n===== cost={cost_round*100:.2f}%  partial={p['use_partial']} =====")
        for per in ("INS", "OOS"):
            g = t[t.period == per]
            print(per, metrics(g, per, days / 2))
        print("ALL", metrics(t, "ALL", days))
        print("by engine:", t.groupby("engine").apply(
            lambda g: pd.Series({"n": len(g), "win": round(100 * (g.net_r > 0).mean(), 1),
                                 "avgR": round(g.net_r.mean(), 3), "PF": round(g.net_r[g.net_r > 0].sum() / max(-g.net_r[g.net_r < 0].sum(), 1e-9), 2)}),
            include_groups=False).to_dict())
        print("by symbol:\n", t.groupby("symbol").apply(
            lambda g: pd.Series({"n": len(g), "win": round(100 * (g.net_r > 0).mean(), 1),
                                 "avgR": round(g.net_r.mean(), 3), "PF": round(g.net_r[g.net_r > 0].sum() / max(-g.net_r[g.net_r < 0].sum(), 1e-9), 2)}),
            include_groups=False).to_string())
        print("by reason:", t.groupby("reason").net_r.agg(["count", "mean"]).round(3).to_dict())
    return t


DEFAULT = dict(
    # regime（双闸门）
    trend_er=0.30, trend_gap=0.30, htf_gap=0.50,
    chop_er=0.25, chop_er_1h=0.32, htf_range_gap=1.2,
    # 均值回归引擎
    w_meanrev=True, mr_sweep=False, band_touch=0.0005, rsi_ovs=20.0, rsi_obv=80.0,
    mr_sl_k=1.1, mr_sl_cap=2.2, mr_rr=1.05,
    # 趋势引擎
    w_trend=True, tr_sl_k=1.0, tr_sl_cap=2.0, tr_rr=1.5,
    # 出场/持仓
    use_partial=True, part_frac=0.5, tp1_r=0.6, trail_k=2.2,
    stag_bars=18, stag_r=0.3, max_hold=48, cooldown=3,
)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--cost", type=float, default=0.0012)
    args = ap.parse_args()
    if not args.sweep:
        for cost in (0.002, 0.0012, 0.0008):
            run(DEFAULT, cost)
    else:
        # 粗网格：在 INS 上筛选，OOS 仅用于最终确认（这里同时打印，人工只选两段都稳的平台）
        grid = dict(
            chop_er=[0.18, 0.22, 0.26], mr_rr=[0.9, 1.1, 1.3],
            mr_sweep=[True, False], use_partial=[True, False],
            tp1_r=[0.5, 0.7], trail_k=[1.8, 2.5],
        )
        keys = list(grid)
        rows = []
        for combo in itertools.product(*[grid[k] for k in keys]):
            p = dict(DEFAULT); p.update(dict(zip(keys, combo)))
            t = run(p, args.cost, verbose=False)
            if not len(t):
                continue
            r = {}
            for per in ("INS", "OOS"):
                g = t[t.period == per]
                if len(g):
                    r[f"{per}_win"] = round(100 * (g.net_r > 0).mean(), 1)
                    r[f"{per}_pf"] = round(g.net_r[g.net_r > 0].sum() / max(-g.net_r[g.net_r < 0].sum(), 1e-9), 2)
                    r[f"{per}_n"] = len(g); r[f"{per}_avgR"] = round(g.net_r.mean(), 3)
            rows.append({**dict(zip(keys, combo)), **r})
        res = pd.DataFrame(rows)
        # 只保留两段都 PF>1 且 INS 胜率>=60% 的稳健平台
        sel = res[(res.INS_pf > 1.0) & (res.OOS_pf > 1.0) & (res.INS_win >= 60)]
        print("\n===== 两段都盈利且INS胜率>=60% 的参数平台 =====")
        print(sel.sort_values(["OOS_pf", "INS_pf"], ascending=False).head(25).to_string(index=False))
        print("\n===== PF 最高的前15 =====")
        print(res.sort_values(["OOS_pf", "INS_pf"], ascending=False).head(15).to_string(index=False))
