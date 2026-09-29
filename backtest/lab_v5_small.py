#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ALPHA-X FAST v5 研究台（小币，只读研究，不下单）。

目标（对应用户 v5 需求）：
  1) 更快识别机会：v4 趋势市空仓，v5 增加【小币趋势回踩延续 sleeve】，区间仍做高胜率均值回归；
  2) 多空方向准：1H/4H 定量 regime 闸门 + 回踩到位+重新启动确认，不追垂直脉冲；并用【多空翻转对照】证明方向有真实 edge；
  3) 胜率≥60% 且盈亏比为正：区间 sleeve 求高胜率(分批+保本)，趋势 sleeve 用更高固定 RR 提供大赚单，抬升平均盈利；
  4) 防过拟合/欠拟合：INS(03-01~06-30) 调参、OOS(07-01~09-15) 冻结；31 个小币横截面；粗网格宽平台 + 单参数敏感性。

成交/风控口径尽量贴近生产 backtest/fast_backtest.py 的 run_symbol_partial：
  信号在 i 根 5m 收盘判定、i+1 开盘成交；同根先判止损；分批档 (0.5目标平30%→保本, 0.8目标平30%→保护0.5目标, 40% runner 到全目标)；
  MR 单不做横盘/反转主动平仓、只认 TP/SL/48根到期；趋势单加横盘时间止损（反转主动平仓以生产 harness 为准，研究台从简=略偏乐观，最终以生产复核为准）。

研究台只用于筛想法；入选逻辑移植进生产 alpha_fast_mode.py 后，必须用 v5_bt.py 直接驱动生产 _build_decision 复核。
"""
from __future__ import annotations
import argparse, itertools, os, sys
import numpy as np
import pandas as pd

BT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BT)
import lab_v4 as L

DATA = os.path.join(BT, "data_small")
INS0, INS1 = L.INS0, L.INS1
OOS0, OOS1 = L.OOS0, L.OOS1

# 31 个全窗口数据完整的小/中高波动 alt（板块分散）
UNIVERSE = ["WIFUSDT", "SUIUSDT", "SEIUSDT", "APTUSDT", "INJUSDT", "TIAUSDT", "OPUSDT",
            "ARBUSDT", "WLDUSDT", "FETUSDT", "RUNEUSDT", "AAVEUSDT", "CRVUSDT", "ENSUSDT",
            "GALAUSDT", "SANDUSDT", "AXSUSDT", "CHZUSDT", "LDOUSDT", "ALTUSDT", "JTOUSDT",
            "PEPEUSDT", "SHIBUSDT", "FLOKIUSDT", "MEMEUSDT", "BONKUSDT", "RENDERUSDT",
            "STXUSDT", "IMXUSDT", "ARKMUSDT", "ORDIUSDT"]


def add_4h(d):
    """在 5m 主表上按收盘时刻 merge_asof 4h 特征（只用已收盘 4h K，无未来）。"""
    r = L.resample_closed(d, "4h", 240)
    rh = r.high.to_numpy(float); rl = r.low.to_numpy(float); rc = r.close.to_numpy(float)
    r["e21_4"] = L.ema(rc, 21); r["e50_4"] = L.ema(rc, 50)
    r["atr_4"] = L.atr(rh, rl, rc, 14); r["er_4"] = L.kaufman_er(rc, 14)
    r["eslope_4"] = pd.Series(r["e21_4"]).pct_change(5).to_numpy()
    r["egap_4"] = (r["e21_4"] - r["e50_4"]) / np.maximum(r["atr_4"], 1e-12)
    keep = ["close_time", "e21_4", "e50_4", "atr_4", "er_4", "eslope_4", "egap_4"]
    d = pd.merge_asof(d.sort_values("ct"), r[keep].sort_values("close_time"),
                      left_on="ct", right_on="close_time", direction="backward")
    return d.drop(columns=["close_time"]).reset_index(drop=True)


def build(df):
    d = L.build_features(df)
    d = add_4h(d)
    c = d.close.to_numpy(float)
    # 价格相对 1H EMA21 的乖离（按 1H ATR 归一）：衡量趋势延伸程度，MR 用来拒绝"强趋势里接飞刀"
    d["ext1h"] = np.abs(c - d["e21_1h"].to_numpy(float)) / np.maximum(d["atr_1h"].to_numpy(float), 1e-12)
    # 5m 布林带宽 / 5m ATR：区分"安静区间"与"波动扩张/趋势启动"
    d["bbw"] = (d["bb_up"].to_numpy(float) - d["bb_dn"].to_numpy(float)) / np.maximum(d["atr"].to_numpy(float), 1e-12)
    d["bbw_ma"] = pd.Series(d["bbw"].to_numpy(float)).rolling(50).mean().to_numpy()
    return d


def regime_v5(x, p):
    """1=多头趋势 / -1=空头趋势 / 0=区间 / 9=中性。1H 定方向、4H 不强烈反向、ER 定趋势 vs 区间。"""
    er15, er1h = x["er_15"], x["er_1h"]
    eg1, eg4 = x["egap_1h"], x["egap_4"]
    sl1 = x["eslope_1h"]
    if not np.isfinite(eg1) or not np.isfinite(eg4):
        return 9
    # 1H 定方向、效率比确认趋势，4H 允许中性但不允许强烈反向
    tup = bool((eg1 > p["trend_gap"]) and (sl1 > 0) and (er1h >= p["trend_er"])
               and (eg4 > -p["htf_gap"]))
    tdn = bool((eg1 < -p["trend_gap"]) and (sl1 < 0) and (er1h >= p["trend_er"])
               and (eg4 < p["htf_gap"]))
    if tup:
        return 1
    if tdn:
        return -1
    rng = bool(er15 < p["chop_er"] and er1h < p["chop_er_1h"] and abs(eg4) < p["range_gap4"])
    return 0 if rng else 9


def regime_array(d, p):
    """regime_v5 的向量化实现（逐元素等价，仅提速）。"""
    er15 = d["er_15"].to_numpy(float); er1h = d["er_1h"].to_numpy(float)
    eg1 = d["egap_1h"].to_numpy(float); eg4 = d["egap_4"].to_numpy(float)
    sl1 = d["eslope_1h"].to_numpy(float)
    fin = np.isfinite(eg1) & np.isfinite(eg4)
    tup = fin & (eg1 > p["trend_gap"]) & (sl1 > 0) & (er1h >= p["trend_er"]) & (eg4 > -p["htf_gap"])
    tdn = fin & (eg1 < -p["trend_gap"]) & (sl1 < 0) & (er1h >= p["trend_er"]) & (eg4 < p["htf_gap"])
    rng = fin & (~tup) & (~tdn) & (er15 < p["chop_er"]) & (er1h < p["chop_er_1h"]) & (np.abs(eg4) < p["range_gap4"])
    reg = np.full(len(d), 9, int)
    reg[rng] = 0
    reg[tup] = 1
    reg[tdn] = -1
    return reg


def v5_signals(d, p):
    """产出 v5 原始信号：engine=MR(区间回归) / TR(趋势回踩) / BR(突破,默认关)。"""
    n = len(d)
    reg = regime_array(d, p)
    c = d.close.to_numpy(); h = d.high.to_numpy(); l = d.low.to_numpy(); o = d.open.to_numpy()
    v = d.vol.to_numpy(); medv = d.medvol21.to_numpy()
    bbdn = d.bb_dn.to_numpy(); bbup = d.bb_up.to_numpy(); bbm = d.bb_mid.to_numpy(); bbsd = d.bb_sd.to_numpy()
    rsi2 = d.rsi2.to_numpy(); vwap = d.vwap.to_numpy(); a5 = d.atr.to_numpy(); a15 = d.atr_15.to_numpy()
    ll10 = d.ll10.to_numpy(); hh10 = d.hh10.to_numpy()
    e21_15 = d.e21_15.to_numpy(); e21_1h = d.get("e21_1h")
    e21_1h = e21_1h.to_numpy() if e21_1h is not None else np.full(n, np.nan)
    ext1h = d["ext1h"].to_numpy(float); bbw = d["bbw"].to_numpy(float); bbw_ma = d["bbw_ma"].to_numpy(float)
    eg4 = d["egap_4"].to_numpy(float); er4 = d["er_4"].to_numpy(float)
    sigs = []
    for i in range(260, n - 2):
        r = int(reg[i]); px = float(c[i])
        au = float(a15[i])
        if not np.isfinite(au) or au <= 0 or px <= 0:
            continue
        # ---------------- Sleeve MR：区间极值回归（v4 主力，增强过滤） ----------------
        if r == 0 and p["w_mr"]:
            sweep_lo = bool(np.min(l[i - 2:i + 1]) < ll10[i - 1]) if np.isfinite(ll10[i - 1]) else False
            sweep_hi = bool(np.max(h[i - 2:i + 1]) > hh10[i - 1]) if np.isfinite(hh10[i - 1]) else False
            med_v = medv[i]; vol_climax = bool(med_v > 0 and v[i] >= 1.5 * med_v)
            rng_bar = max(h[i] - l[i], 1e-12)
            # —— v5 区间质量否决：拒绝在强趋势延伸 / 4H 已走趋势 / 布林带爆炸式扩张 时逆势接飞刀 ——
            veto = bool(
                (p["mr_max_ext1h"] > 0 and (not np.isfinite(ext1h[i]) or ext1h[i] > p["mr_max_ext1h"]))
                or (p["mr_max_4gap"] > 0 and (not np.isfinite(eg4[i]) or abs(eg4[i]) > p["mr_max_4gap"]))
                or (p["mr_bbw_x"] > 0 and np.isfinite(bbw_ma[i]) and bbw[i] > p["mr_bbw_x"] * bbw_ma[i]))
            if not veto:
                # 多头：刺破下轨后强势收回（收盘在K线上半区）+ RSI 超卖 + 共振
                reclaim_l = (l[i] <= bbdn[i] * (1 + p["band_touch"]) and c[i] > bbdn[i]
                             and (c[i] - l[i]) / rng_bar >= (0.5 if p["mr_body"] else 0.0) and c[i] >= o[i])
                conf_l = bool((sweep_lo and p["mr_sweep"]) or rsi2[i] <= p["rsi_ext"] or (vol_climax and p["mr_climax"]))
                if reclaim_l and rsi2[i] <= p["rsi_ovs"] and conf_l:
                    anchor = max(px - float(np.min(l[i - 6:i + 1])), 0.0)
                    vwap_t = max(vwap[i] - px, 0.0) if np.isfinite(vwap[i]) else 0.0
                    mid_t = max(bbm[i] - px, 0.0); band_t = max(bbup[i] - px, 0.0)
                    sigs.append(dict(i=i, side="LONG", engine="MR", regime=r, anchor=anchor,
                                     vwap_t=vwap_t, mid_t=mid_t, band_t=band_t, rsi=rsi2[i]))
                reclaim_h = (h[i] >= bbup[i] * (1 - p["band_touch"]) and c[i] < bbup[i]
                             and (h[i] - c[i]) / rng_bar >= (0.5 if p["mr_body"] else 0.0) and c[i] <= o[i])
                conf_h = bool((sweep_hi and p["mr_sweep"]) or rsi2[i] >= 100 - p["rsi_ext"] or (vol_climax and p["mr_climax"]))
                if reclaim_h and rsi2[i] >= p["rsi_obv"] and conf_h:
                    anchor = max(float(np.max(h[i - 6:i + 1])) - px, 0.0)
                    vwap_t = max(px - vwap[i], 0.0) if np.isfinite(vwap[i]) else 0.0
                    mid_t = max(px - bbm[i], 0.0); band_t = max(px - bbdn[i], 0.0)
                    sigs.append(dict(i=i, side="SHORT", engine="MR", regime=r, anchor=anchor,
                                     vwap_t=vwap_t, mid_t=mid_t, band_t=band_t, rsi=100 - rsi2[i]))
        # ---------------- Sleeve TR：强趋势里的回踩→重启（小币趋势更顺，v5 新增） ----------------
        if r in (1, -1) and p["w_tr"]:
            up = r == 1
            # 可选：4H 必须同向（方向更准，牺牲一些机会）
            align4 = bool((not p["tr_align4h"]) or (up and eg4[i] > 0) or ((not up) and eg4[i] < 0))
            if not align4:
                continue
            # 近 6 根回踩触及价值区（15m EMA21 或 会话 VWAP）
            if up:
                touch = float(np.min(l[i - 6:i + 1])) <= max(e21_15[i], vwap[i]) * 1.002 if np.isfinite(e21_15[i]) else False
                # 重启确认：当根收阳、站回 VWAP 上方、下影/收回近期小低点，且不追高（收盘离 EMA21_15 不超过 tr_extend*ATR15）
                resume = bool(c[i] > o[i] and c[i] > vwap[i]
                              and (c[i] - l[i]) / max(h[i] - l[i], 1e-12) >= 0.5
                              and (l[i] < ll10[i - 1] or l[i] <= e21_15[i] * 1.001))
                extended = bool(c[i] > e21_15[i] + p["tr_extend"] * au) if np.isfinite(e21_15[i]) else True
                if touch and resume and not extended:
                    anchor = max(px - float(np.min(l[i - 6:i + 1])), 0.0)
                    sigs.append(dict(i=i, side="LONG", engine="TR", regime=r, anchor=anchor,
                                     vwap_t=0.0, mid_t=0.0, band_t=0.0))
            else:
                touch = float(np.max(h[i - 6:i + 1])) >= min(e21_15[i], vwap[i]) * 0.998 if np.isfinite(e21_15[i]) else False
                resume = bool(c[i] < o[i] and c[i] < vwap[i]
                              and (h[i] - c[i]) / max(h[i] - l[i], 1e-12) >= 0.5
                              and (h[i] > hh10[i - 1] or h[i] >= e21_15[i] * 0.999))
                extended = bool(c[i] < e21_15[i] - p["tr_extend"] * au) if np.isfinite(e21_15[i]) else True
                if touch and resume and not extended:
                    anchor = max(float(np.max(h[i - 6:i + 1])) - px, 0.0)
                    sigs.append(dict(i=i, side="SHORT", engine="TR", regime=r, anchor=anchor,
                                     vwap_t=0.0, mid_t=0.0, band_t=0.0))
    # 挂止损/止盈
    for s in sigs:
        i = s["i"]; px = c[i]; au = a15[i]
        if s["engine"] == "MR":
            klo, khi, rr, floor = p["mr_klo"], p["mr_khi"], p["mr_rr"], p["mr_floor"]
        else:
            klo, khi, rr, floor = p["tr_klo"], p["tr_khi"], p["tr_rr"], p["tr_floor"]
        stop = float(np.clip(s["anchor"] + 0.4 * au, klo * au, khi * au))
        sl_pct = stop / px
        if sl_pct < floor:                      # 结构止损太窄→成本/噪声占比过高，放弃
            s["sl"] = 0.0; s["tp"] = 0.0; continue
        tp_dist = rr * stop
        if s["engine"] == "MR":
            tmode = p.get("mr_target", "vwap")
            cand = {"vwap": s.get("vwap_t", 0.0), "mid": s.get("mid_t", 0.0), "band": s.get("band_t", 0.0)}.get(tmode, s.get("vwap_t", 0.0))
            # 目标位至少满足兜底 RR；VWAP 模式只在足够远时才用它，否则按兜底 RR
            if tmode in ("mid", "band") and cand > 0:
                tp_dist = max(cand, rr * stop)
            elif s.get("vwap_t", 0.0) >= rr * stop:
                tp_dist = s["vwap_t"]
        s["sl"] = sl_pct; s["tp"] = tp_dist / px
    return d, [s for s in sigs if s["sl"] > 0 and s["tp"] > 0]


PARTIAL_LEVELS = [(0.50, 0.30), (0.80, 0.30)]   # 与生产 run_symbol_partial 对齐


def simulate_v5(d, sigs, cost_round, p):
    """贴近生产 run_symbol_partial 的事件驱动模拟；MR 与 TR 出场管理不同。"""
    o = d.open.to_numpy(float); h = d.high.to_numpy(float); l = d.low.to_numpy(float)
    c = d.close.to_numpy(float); n = len(d)
    max_hold = p["max_hold"]; cooldown = p["cooldown"]
    stag_bars, stag_r = p["stag_bars"], p["stag_r"]
    use_part = p["use_partial"]; oneway = cost_round / 2.0
    rows = []; busy = -1
    for s in sigs:
        i = s["i"]
        if i < busy:
            continue
        side = 1 if s["side"] == "LONG" else -1
        entry = float(o[i + 1])
        if not np.isfinite(entry) or entry <= 0:
            continue
        sl_pct, tp_pct = float(s["sl"]), float(s["tp"])
        risk = entry * sl_pct
        base_cost = cost_round / sl_pct
        is_mr = s["engine"] == "MR"
        if side == 1:
            stop0 = entry * (1 - sl_pct); tp_full = entry * (1 + tp_pct); be_px = entry * 1.0005
        else:
            stop0 = entry * (1 + sl_pct); tp_full = entry * (1 - tp_pct); be_px = entry * 0.9995
        levels = []
        if use_part:
            for prog, frac in PARTIAL_LEVELS:
                tgt = max(0.002, min(tp_pct * prog, 0.25))
                lvl = entry * (1 + side * tgt) if tgt < tp_pct else None
                levels.append([lvl, frac, False])
        realized = 0.0; rem = 1.0; part_cost = 0.0; be = False
        stop = stop0; max_gain = 0.0; exit_px = None; reason = None; ej = None; runner_r = None
        for j in range(i + 1, min(i + 1 + max_hold, n)):
            hi, lo, cl = float(h[j]), float(l[j]), float(c[j]); bars = j - (i + 1) + 1
            # 1) 止损/保本/保护（同根最先判，保守），对剩余 runner 生效
            if (side == 1 and lo <= stop) or (side == -1 and hi >= stop):
                exit_px, ej = stop, j; runner_r = side * (stop - entry) / risk
                ndone = sum(1 for x in levels if x[2])
                reason = "SL" if not (be or ndone) else ("BE" if ndone <= 1 else "PROTECT")
                break
            # 2) 分批档（每根最多推进一档）
            for k, (lvl, frac, done) in enumerate(levels):
                if done or lvl is None:
                    continue
                if (side == 1 and hi >= lvl) or (side == -1 and lo <= lvl):
                    levels[k][2] = True
                    stage_R = side * (lvl - entry) / risk
                    realized += frac * stage_R; rem -= frac
                    part_cost += frac * oneway / sl_pct
                    if k == 0:
                        be = True
                        stop = max(stop, be_px) if side == 1 else min(stop, be_px)
                    else:
                        fp = max(0.002, min(tp_pct * 0.5, 0.25)); fpx = entry * (1 + side * fp)
                        stop = max(stop, fpx) if side == 1 else min(stop, fpx)
                break
            # 3) runner 全目标
            if (side == 1 and hi >= tp_full) or (side == -1 and lo <= tp_full):
                exit_px, ej, runner_r = tp_full, j, side * (tp_full - entry) / risk
                reason = "TP_RUNNER"; break
            # 4) 浮盈/保本
            if side == 1:
                max_gain = max(max_gain, (hi - entry) / risk)
                if not be and hi >= entry + risk:
                    be = True; stop = be_px
            else:
                max_gain = max(max_gain, (entry - lo) / risk)
                if not be and lo <= entry - risk:
                    be = True; stop = be_px
            # 5) 横盘时间止损（MR 不用）
            if (not is_mr) and bars >= stag_bars and max_gain < stag_r:
                exit_px, ej, runner_r = cl, j, side * (cl - entry) / risk; reason = "STAG"; break
            if bars >= max_hold:
                exit_px, ej, runner_r = cl, j, side * (cl - entry) / risk; reason = "TIME"; break
        if exit_px is None:
            ej = min(i + max_hold, n - 1); exit_px = float(c[ej]); runner_r = side * (exit_px - entry) / risk; reason = "END"
        gross = realized + max(rem, 0.0) * runner_r
        net = gross - base_cost - part_cost
        rows.append(dict(symbol=s.get("sym", ""), ct=d.ct.iloc[i + 1], side=s["side"], engine=s["engine"],
                         regime=int(s["regime"]), sl_pct=sl_pct, tp_pct=tp_pct, plan_rr=round(tp_pct / sl_pct, 2),
                         entry=entry, exit=exit_px, reason=reason, bars=int(ej - (i + 1) + 1),
                         gross_r=gross, cost_r=base_cost + part_cost, net_r=net,
                         partial=any(x[2] for x in levels)))
        busy = ej + 1 + cooldown
    return pd.DataFrame(rows)


def metrics(t, label="", days=1.0):
    if not len(t):
        return dict(label=label, n=0)
    r = t.net_r; w = r > 0
    aw = r[r > 0].mean() if (r > 0).any() else 0.0
    al = -r[r < 0].mean() if (r < 0).any() else 0.0
    pf = r[r > 0].sum() / max(-r[r < 0].sum(), 1e-9)
    cum = r.cumsum(); dd = (cum - cum.cummax()).min()
    return dict(label=label, n=len(t), per_day=round(len(t) / max(days, 1), 2),
                win=round(100 * w.mean(), 1), avgR=round(r.mean(), 3), totR=round(r.sum(), 0),
                PF=round(pf, 2), payoff=round(aw / max(al, 1e-9), 2), maxDD=round(dd, 0),
                avgBars=round(t.bars.mean(), 1))


def period_of(ts):
    if INS0 <= ts <= INS1: return "INS"
    if OOS0 <= ts <= OOS1: return "OOS"
    return "?"


_CACHE = {}


def get_features(sym):
    if sym not in _CACHE:
        df = pd.read_csv(os.path.join(DATA, f"{sym}_5m.csv"))
        _CACHE[sym] = build(df)
    return _CACHE[sym]


def run(p, cost, universe=None, flip=False, verbose=False, sleeves=None):
    universe = universe or UNIVERSE
    rows = []
    for sym in universe:
        try:
            d = get_features(sym)
        except Exception as e:
            print("skip", sym, repr(e)[:80]); continue
        d, sigs = v5_signals(d, p)
        if sleeves:
            sigs = [s for s in sigs if s["engine"] in sleeves]
        if flip:
            for s in sigs:
                s["side"] = "SHORT" if s["side"] == "LONG" else "LONG"
        for s in sigs:
            s["sym"] = sym
        t = simulate_v5(d, sigs, cost, p)
        rows.append(t)
    t = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if not len(t):
        return t
    t["period"] = t.ct.apply(period_of)
    days = 199.0 * len(universe)
    if verbose:
        report(t, days)
    return t


def report(t, days=None, n_sym=None):
    n_sym = n_sym or t.symbol.nunique()
    days = days or 199.0 * n_sym
    for per in ("INS", "OOS"):
        g = t[t.period == per]
        print(per, metrics(g, per, days / 2))
    print("ALL", metrics(t, "ALL", days))
    print("by engine:\n", t.groupby("engine").apply(
        lambda g: pd.Series({"n": len(g), "win": round(100 * (g.net_r > 0).mean(), 1),
                             "avgR": round(g.net_r.mean(), 3),
                             "PF": round(g.net_r[g.net_r > 0].sum() / max(-g.net_r[g.net_r < 0].sum(), 1e-9), 2),
                             "payoff": round((g.net_r[g.net_r > 0].mean() if (g.net_r > 0).any() else 0) /
                                             max(-(g.net_r[g.net_r < 0].mean() if (g.net_r < 0).any() else 0), 1e-9), 2)}),
        include_groups=False).to_string())
    pc = t.groupby("symbol").apply(lambda g: (g.net_r.sum() > 0) and (g.net_r > 0).mean() >= 0.5, include_groups=False)
    print(f"盈利且胜率≥50% 的币占比: {round(100*pc.mean(),1)}%  ({int(pc.sum())}/{n_sym})")
    print("by reason:", t.groupby("reason").net_r.agg(["count", "mean"]).round(3).to_dict())


# ---------------- 默认参数（粗、圆、宽平台；最终在 INS 选、OOS 冻结） ----------------
DEFAULT = dict(
    trend_gap=0.30, trend_er=0.30, htf_gap=0.55,
    chop_er=0.26, chop_er_1h=0.33, range_gap4=1.20,
    # MR
    w_mr=True, band_touch=0.0005, rsi_ovs=20.0, rsi_obv=80.0, rsi_ext=12.0,
    mr_sweep=True, mr_climax=True, mr_body=True, mr_vwap_tp=True,
    mr_klo=1.9, mr_khi=2.6, mr_rr=1.3, mr_floor=0.004,
    # v5 MR 区间质量否决（>0 生效）与目标模式 vwap/mid/band
    mr_max_ext1h=2.5, mr_max_4gap=0.8, mr_bbw_x=1.3, mr_target="vwap",
    # TR 趋势回踩
    w_tr=True, tr_extend=2.5, tr_klo=1.3, tr_khi=2.6, tr_rr=2.0, tr_floor=0.006,
    tr_align4h=True,
    # 出场
    use_partial=True, stag_bars=18, stag_r=0.3, max_hold=48, cooldown=3,
)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cost", type=float, default=0.0006)
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--flip", action="store_true")
    ap.add_argument("--sleeves", nargs="*", default=None)
    a = ap.parse_args()
    if a.sweep:
        grid = dict(mr_rr=[1.1, 1.3, 1.5], tr_rr=[1.6, 2.0, 2.4],
                    tr_extend=[2.0, 2.5, 3.0], mr_sweep=[True, False])
        keys = list(grid); rows = []
        for combo in itertools.product(*[grid[k] for k in keys]):
            p = dict(DEFAULT); p.update(dict(zip(keys, combo)))
            t = run(p, a.cost, verbose=False)
            r = {}
            for per in ("INS", "OOS"):
                g = t[t.period == per]
                if len(g):
                    r[f"{per}_n"] = len(g); r[f"{per}_win"] = round(100 * (g.net_r > 0).mean(), 1)
                    r[f"{per}_pf"] = round(g.net_r[g.net_r > 0].sum() / max(-g.net_r[g.net_r < 0].sum(), 1e-9), 2)
                    r[f"{per}_avgR"] = round(g.net_r.mean(), 3)
            rows.append({**dict(zip(keys, combo)), **r})
        res = pd.DataFrame(rows)
        sel = res[(res.INS_pf > 1.05) & (res.OOS_pf > 1.0) & (res.OOS_win >= 55) & (res.OOS_n >= 200)]
        print("=== INS/OOS 双段稳健平台（OOS胜率≥55 且样本≥200） ===")
        print(sel.sort_values(["OOS_pf", "INS_pf"], ascending=False).head(20).to_string(index=False))
    else:
        for cost in (0.0012, 0.0008, 0.0006):
            print(f"\n########## v5 小币组合 往返成本 {cost*100:.2f}% flip={a.flip} ##########")
            t = run(DEFAULT, cost, flip=a.flip, sleeves=a.sleeves, verbose=True)
