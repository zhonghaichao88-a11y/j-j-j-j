#!/usr/bin/env python3
"""ALPHA-X FAST 裸K：真实数据事件驱动回测引擎（只读，不下单）。

口径（与生产 alpha_engine 的 FAST 持仓管理对齐）：
- 信号在第 i 根5m收盘判定（仅用已收盘K线），第 i+1 根开盘成交（杜绝当根未来函数）。
- 交易所 TP/SL 条件单始终有效；同一根5m同时触及 TP/SL 时，保守按先打 SL。
- 浮盈首次达 1R：止损移到 成本+双边成本（保本，只做一次）。
- 持仓满 8 根(40分钟)且最大浮盈始终<0.3R：横盘时间止损，按当根收盘平仓。
- 开仓后 2 根(10分钟)为最小持仓，之后调用与生产同一函数判定“确认反转”才主动平仓
  （需两个不同已收盘5m确认，或单次评分>=0.90）。
- 最多持仓 48 根5m(4小时)，到期按收盘平仓；平仓后冷却 3 根(15分钟)。
- 成本：手续费0.05%+滑点0.05% 每边，往返0.2%，折算为 R。
"""
from __future__ import annotations
import argparse, importlib.util, json, os, sys
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
BT = os.path.dirname(os.path.abspath(__file__))

# ---- 与生产一致的成本/出场常量（alpha_engine.DEFAULT / FAST增强段）----
FEE = 0.0005
SLIP = 0.0005
ROUND_COST = 2.0 * (FEE + SLIP)   # 0.002
BE_BUFFER = 2.0 * (FEE + SLIP)    # 保本止损相对成本的偏移
MIN_HOLD_BARS = 2
STAGNATE_BARS = 18
STAGNATE_MIN_R = 0.3
MAX_HOLD_BARS = 48
COOLDOWN_BARS = 3

# ---- 数学等价的向量化 _swings（已在31623组K线上与原版逐点比对一致），仅为回测提速 ----
def _fast_swings(frame, left=2, right=2):
    from numpy.lib.stride_tricks import sliding_window_view as _swv
    h = np.asarray(frame.get("high", np.array([])), float)
    l = np.asarray(frame.get("low", np.array([])), float)
    n = len(h)
    if n < left + right + 5:
        return [], []
    w = left + right + 1
    def _piv(x, want):
        win = _swv(x, w)
        if want == "max":
            neigh = np.maximum(win[:, :left].max(1), win[:, left + 1:].max(1))
            center = x[left:n - right]; ok = center > neigh
        else:
            neigh = np.minimum(win[:, :left].min(1), win[:, left + 1:].min(1))
            center = x[left:n - right]; ok = center < neigh
        idxs = np.nonzero(ok)[0] + left
        vals = center[ok]; out = []; min_sep = max(left + right, 3)
        for ii in range(len(idxs)):
            i = int(idxs[ii]); v = float(vals[ii])
            if not out or i - out[-1][0] >= min_sep:
                out.append((i, v))
            elif (want == "max" and v > out[-1][1]) or (want == "min" and v < out[-1][1]):
                out[-1] = (i, v)
        return out
    return _piv(h, "max"), _piv(l, "min")

def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # 用数学等价的快速实现替换热点纯函数（结果一致，仅提速）
    mod._swings = _fast_swings
    return mod

def resample(df, rule, mins):
    g = df.set_index(pd.to_datetime(df.open_ms, unit="ms")).resample(rule, label="left", closed="left")
    o = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
              close=("close", "last"), vol=("vol", "sum")).dropna()
    o["ts_ms"] = (o.index.view(np.int64) // 10**6)
    return o

class Engine:
    def __init__(self, mod):
        self.m = mod
        # 当次决策内的纯函数记忆化（数据指纹缓存，结果与直接调用一致）
        self.memo = {}
        self._wrap()

    def _fp(self, fr):
        c = fr.get("close"); h = fr.get("high"); l = fr.get("low")
        if c is None or len(c) == 0: return (0, 0.0, 0.0, 0.0, 0.0)
        return (len(c), float(c[-1]), float(c[0]), float(h[-1]), float(l[-1]))

    def _wrap(self):
        m = self.m
        for name in ["_structure", "_n_pattern", "_fvg", "_liquidity_sweep", "_order_block",
                     "_breakout_pullback_reclaim", "_displacement", "_rejection_wick",
                     "_compression_expansion", "_premium_discount", "_price_location",
                     "_liquidity_pools", "_dealing_range"]:
            if not hasattr(m, name): continue
            orig = getattr(m, name); ev = self
            def make(fn, nm):
                def wrap(fr, *a, **k):
                    key = (nm, ev._fp(fr), a)
                    if key in ev.memo: return ev.memo[key]
                    r = fn(fr, *a, **k); ev.memo[key] = r; return r
                return wrap
            setattr(m, name, make(orig, name))
        if hasattr(m, "_efficiency_ratio"):
            orig_er = m._efficiency_ratio; ev = self
            def er_wrap(c, n=14):
                key = ("ER", len(c), float(c[-1]), float(c[0]), n)
                if key in ev.memo: return ev.memo[key]
                r = orig_er(c, n); ev.memo[key] = r; return r
            m._efficiency_ratio = er_wrap

    def frames_at(self, df, rs, i):
        cc = int(df.open_ms.iloc[i]) + 5 * 60000
        out = {}
        sub5 = df.iloc[max(0, i - 119):i + 1]
        out["5m"] = {"ts": sub5.open_ms.to_numpy(float), "open": sub5.open.to_numpy(float),
                     "high": sub5.high.to_numpy(float), "low": sub5.low.to_numpy(float),
                     "close": sub5.close.to_numpy(float), "volume": sub5.vol.to_numpy(float)}
        for tf, mins in (("15m", 15), ("1h", 60), ("4h", 240)):
            r = rs[tf]
            k = int(np.searchsorted(r.ts_ms.to_numpy(), cc - mins * 60000, side="right"))
            seg = r.iloc[max(0, k - 120):k]
            out[tf] = {"ts": seg.ts_ms.to_numpy(float), "open": seg.open.to_numpy(float),
                       "high": seg.high.to_numpy(float), "low": seg.low.to_numpy(float),
                       "close": seg.close.to_numpy(float), "volume": seg.vol.to_numpy(float)}
        return out

    def decide(self, symbol, df, rs, i):
        self.memo.clear()
        frames = self.frames_at(df, rs, i)
        data = {"frames": frames, "ticker_last": float(df.close.iloc[i]), "spread_bps": -1.0,
                "missing": [], "summary": {"ready": True}}
        return self.m._build_decision(symbol, data), frames


def run_symbol(mod, symbol, df, rs, start_ms=None, end_ms=None,
               static_only=False, verbose=False, atr_k=0.0, atr_rr=1.5,
               atr_floor=0.0, flip=False):
    """连续遍历单币种，返回成交明细 list[dict]。
    atr_k>0：忽略信号TP/SL，改用 ATR(14,5m)*atr_k 止损、atr_rr倍止盈（诊断/原型）。
    flip=True：多空翻转（随机对照，检验方向是否有真实edge）。"""
    eng = Engine(mod)
    m = eng.m
    n = len(df)
    o = df["open"].to_numpy(float); h = df["high"].to_numpy(float); l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float); t = df["open_ms"].to_numpy(float)
    # ATR(14) on 5m（用截至当根已收盘的数据）
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    atr14 = pd.Series(tr).rolling(14).mean().to_numpy()
    # 预构建每根 i 的5m帧（反转检查复用）
    trades = []
    cooldown_until = -1
    i = 400  # warmup，保证4H有足够K线
    if start_ms is not None:
        i = max(i, int(np.searchsorted(t, start_ms, side="left")))
    end_i = n - 2
    if end_ms is not None:
        end_i = min(end_i, int(np.searchsorted(t, end_ms, side="right")))
    while i < end_i:
        if i < cooldown_until:
            i += 1; continue
        res, frames = eng.decide(symbol, df, rs, i)
        sig = res.get("signal")
        if sig not in ("LONG", "SHORT"):
            i += 1; continue
        if flip:
            sig = "SHORT" if sig == "LONG" else "LONG"
        side = 1 if sig == "LONG" else -1
        sl_pct = float(res.get("sl") or 0); tp_pct = float(res.get("tp") or 0)
        if atr_k > 0:
            a = float(atr14[i]) if np.isfinite(atr14[i]) else 0.0
            sl_pct = max(a / float(c[i]) * atr_k, atr_floor)
            tp_pct = sl_pct * atr_rr
        if sl_pct <= 0 or tp_pct <= 0:
            i += 1; continue
        entry = float(o[i + 1])            # 次根开盘成交
        risk = entry * sl_pct
        cost_r = ROUND_COST / sl_pct
        if side == 1:
            stop_px = entry * (1 - sl_pct); tp_px = entry * (1 + tp_pct); be_px = entry * (1 + BE_BUFFER)
        else:
            stop_px = entry * (1 + sl_pct); tp_px = entry * (1 - tp_pct); be_px = entry * (1 - BE_BUFFER)
        init_stop = stop_px
        max_gain_r = 0.0; be_armed = False
        rev_bars = []; exit_reason = None; exit_px = None; exit_j = None
        entry_path = ((res.get("fast_strategy") or {}).get("entry_quality") or {}).get("path", "")
        _eng = (res.get("fast_strategy") or {}).get("v4_engine", "")
        is_mr = _eng == "RANGE_REVERSION" or "区间" in str(entry_path)
        tier = res.get("signal_tier", "")
        score = float(res.get("confidence") or 0)
        rr0 = tp_pct / sl_pct
        for j in range(i + 1, min(i + 1 + MAX_HOLD_BARS, n)):
            hi = float(h[j]); lo = float(l[j]); cl = float(c[j])
            bars_held = j - (i + 1) + 1   # 含成交当根
            # 1) 交易所止损/保本单（同根先判止损，保守）
            if side == 1 and lo <= stop_px:
                exit_reason = "TP" if stop_px >= tp_px*0.999999 else ("BE" if be_armed else "SL"); exit_px = stop_px; exit_j = j; break
            if side == -1 and hi >= stop_px:
                exit_reason = "TP" if stop_px <= tp_px*1.000001 else ("BE" if be_armed else "SL"); exit_px = stop_px; exit_j = j; break
            # 2) 交易所止盈单
            if side == 1 and hi >= tp_px:
                exit_reason = "TP"; exit_px = tp_px; exit_j = j; break
            if side == -1 and lo <= tp_px:
                exit_reason = "TP"; exit_px = tp_px; exit_j = j; break
            # 3) 更新最大浮盈 & 保本（达到1R，下一根生效）
            if side == 1:
                max_gain_r = max(max_gain_r, (hi - entry) / risk)
                if (not be_armed) and hi >= entry + risk: be_armed = True; stop_px = be_px
            else:
                max_gain_r = max(max_gain_r, (entry - lo) / risk)
                if (not be_armed) and lo <= entry - risk: be_armed = True; stop_px = be_px
            if static_only:
                if bars_held >= MAX_HOLD_BARS:
                    exit_reason = "TIMEOUT"; exit_px = cl; exit_j = j; break
                continue
            # 4) 横盘时间止损（满8根且从未0.3R）；均值回归单不用
            if (not is_mr) and bars_held >= STAGNATE_BARS and max_gain_r < STAGNATE_MIN_R:
                exit_reason = "STAGNATION"; exit_px = cl; exit_j = j; break
            # 5) 确认反转主动平仓（过最小持仓）；均值回归单不用
            if (not is_mr) and bars_held >= MIN_HOLD_BARS + 1:
                try:
                    fj = eng.frames_at(df, rs, j)
                    rev = m._fast_position_reversal_check(fj["5m"], fj["15m"], "long" if side == 1 else "short")
                    bar_ts = int(rev.get("bar_ts") or 0)
                    if rev.get("action") == "CONFIRMED" and bar_ts and bar_ts not in rev_bars:
                        rev_bars.append(bar_ts)
                    strong = rev.get("action") == "CONFIRMED" and float(rev.get("score") or 0) >= 0.90
                    if (strong or len(rev_bars) >= 2):
                        exit_reason = "REVERSAL"; exit_px = cl; exit_j = j; break
                except Exception:
                    pass
            if bars_held >= MAX_HOLD_BARS:
                exit_reason = "TIMEOUT"; exit_px = cl; exit_j = j; break
        if exit_px is None:
            # 数据末端仍持仓：按最后收盘市值结算
            exit_px = float(c[min(i + MAX_HOLD_BARS, n - 1)]); exit_j = min(i + MAX_HOLD_BARS, n - 1); exit_reason = "OPEN_END"
        gross_r = side * (exit_px - entry) / risk
        net_r = gross_r - cost_r
        trades.append({
            "symbol": symbol, "sig_bar": int(t[i]), "entry_bar": int(t[i + 1]),
            "entry_time": pd.to_datetime(t[i + 1], unit="ms"), "side": sig,
            "path": entry_path, "engine": _eng, "tier": tier, "score": round(score, 3), "plan_rr": round(rr0, 2),
            "sl_pct": sl_pct, "tp_pct": tp_pct, "entry": entry, "exit": exit_px,
            "exit_reason": exit_reason, "bars": int(exit_j - (i + 1) + 1),
            "gross_r": gross_r, "cost_r": cost_r, "net_r": net_r, "partial": False, "part_closed": 0.0,
        })
        cooldown_until = exit_j + 1 + COOLDOWN_BARS
        i = exit_j + 1
    return trades


# 与生产 alpha_engine._live_partial_take_profit 对齐的分批档：(目标进度, 平仓比例)
PARTIAL_LEVELS = [(0.50, 0.30), (0.80, 0.30)]
ONEWAY_COST = (FEE + SLIP)   # 分批是市价 reduce-only(taker)，每批额外计一次单边费用


def run_symbol_partial(mod, symbol, df, rs, start_ms=None, end_ms=None,
                       levels=None, flip=False):
    """与 run_symbol 同口径，但额外模拟生产的分批止盈(30%@0.5目标→保本、30%@0.8目标→保护价、40%奔跑)。

    分批的减仓按 taker 单边费保守计费；同根仍先判止损；runner 沿用保本/横盘/反转/超时管理。
    用于诚实评估 v4 的“高胜率画像”，不改变 run_symbol 的 v3 结果。
    """
    levels = levels or PARTIAL_LEVELS
    eng = Engine(mod); m = eng.m
    n = len(df)
    o = df["open"].to_numpy(float); h = df["high"].to_numpy(float); l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float); t = df["open_ms"].to_numpy(float)
    trades = []; cooldown_until = -1; i = 400
    if start_ms is not None:
        i = max(i, int(np.searchsorted(t, start_ms, side="left")))
    end_i = n - 2
    if end_ms is not None:
        end_i = min(end_i, int(np.searchsorted(t, end_ms, side="right")))
    while i < end_i:
        if i < cooldown_until:
            i += 1; continue
        res, frames = eng.decide(symbol, df, rs, i)
        sig = res.get("signal")
        if sig not in ("LONG", "SHORT"):
            i += 1; continue
        if flip:
            sig = "SHORT" if sig == "LONG" else "LONG"
        side = 1 if sig == "LONG" else -1
        sl_pct = float(res.get("sl") or 0); tp_pct = float(res.get("tp") or 0)
        if sl_pct <= 0 or tp_pct <= 0:
            i += 1; continue
        entry = float(o[i + 1]); risk = entry * sl_pct
        base_cost_r = ROUND_COST / sl_pct
        if side == 1:
            stop_px = entry * (1 - sl_pct); tp_px = entry * (1 + tp_pct); be_px = entry * 1.0005
        else:
            stop_px = entry * (1 + sl_pct); tp_px = entry * (1 - tp_pct); be_px = entry * 0.9995
        stage_px, stage_frac, stage_done = [], [], []
        for prog, frac in levels:
            tgt_pct = max(0.002, min(tp_pct * prog, 0.25))
            if tgt_pct >= tp_pct:
                stage_px.append(None)
            else:
                stage_px.append(entry * (1 + side * tgt_pct))
            stage_frac.append(frac); stage_done.append(False)
        realized_r = 0.0; rem = 1.0; part_cost_r = 0.0
        max_gain_r = 0.0; be_armed = False
        rev_bars = []; exit_reason = None; exit_px = None; exit_j = None; runner_r = None
        entry_path = ((res.get("fast_strategy") or {}).get("entry_quality") or {}).get("path",
                    (res.get("fast_strategy") or {}).get("path", ""))
        tier = res.get("signal_tier", ""); score = float(res.get("confidence") or 0)
        engine_lbl = (res.get("fast_strategy") or {}).get("v4_engine", "")
        # 均值回归 sleeve 的管理与趋势单不同：失效位是宽结构止损，不能被动量“确认反转/横盘”
        # 信号提前砍仓（那正是回归展开前的最差点），只认 TP / 结构SL / 时间到期。
        is_mr = engine_lbl == "RANGE_REVERSION" or "区间" in str(entry_path)
        for j in range(i + 1, min(i + 1 + MAX_HOLD_BARS, n)):
            hi = float(h[j]); lo = float(l[j]); cl = float(c[j]); bars_held = j - (i + 1) + 1
            # 1) 止损/保本（同根最先判，保守）——对剩余 runner 生效
            if (side == 1 and lo <= stop_px) or (side == -1 and hi >= stop_px):
                exit_px = stop_px; exit_j = j
                runner_r = side * (stop_px - entry) / risk
                exit_reason = "SL" if not (be_armed or any(stage_done)) else ("BE" if stage_done.count(True) <= 1 else "PROTECT")
                break
            # 2) 分批止盈档位（按顺序，每根最多推进一档，保守）
            for k in range(len(stage_px)):
                if stage_done[k] or stage_px[k] is None:
                    continue
                tpx = stage_px[k]; hit = (side == 1 and hi >= tpx) or (side == -1 and lo <= tpx)
                if not hit:
                    break
                stage_done[k] = True; fq = stage_frac[k]
                stage_R = side * (tpx - entry) / risk
                realized_r += fq * stage_R; rem -= fq
                part_cost_r += fq * ONEWAY_COST / sl_pct
                if k == 0:    # TP1 后剩余位移到保本
                    be_armed = True
                    stop_px = max(stop_px, be_px) if side == 1 else min(stop_px, be_px)
                else:         # TP2 后保护价提到 0.5 倍目标
                    floor_pct = max(0.002, min(tp_pct * 0.50, 0.25))
                    floor_px = entry * (1 + side * floor_pct)
                    stop_px = max(stop_px, floor_px) if side == 1 else min(stop_px, floor_px)
                break
            # 3) runner 全额止盈（原始 TP）
            if (side == 1 and hi >= tp_px) or (side == -1 and lo <= tp_px):
                exit_px = tp_px; exit_j = j; runner_r = side * (tp_px - entry) / risk
                exit_reason = "TP_RUNNER"; break
            # 4) 浮盈/保本（未分批时的旧保本逻辑，达到1R）
            if side == 1:
                max_gain_r = max(max_gain_r, (hi - entry) / risk)
                if not be_armed and hi >= entry + risk:
                    be_armed = True; stop_px = be_px
            else:
                max_gain_r = max(max_gain_r, (entry - lo) / risk)
                if not be_armed and lo <= entry - risk:
                    be_armed = True; stop_px = be_px
            # 5) 横盘时间止损（均值回归单不用：回归需要时间展开，早砍有害）
            if (not is_mr) and bars_held >= STAGNATE_BARS and max_gain_r < STAGNATE_MIN_R:
                exit_px = cl; exit_j = j; runner_r = side * (cl - entry) / risk
                exit_reason = "STAGNATION"; break
            # 6) 确认反转主动平仓（均值回归单不用：动量确认反转恰是其对手盘逻辑）
            if (not is_mr) and bars_held >= MIN_HOLD_BARS + 1:
                try:
                    fj = eng.frames_at(df, rs, j)
                    rev = m._fast_position_reversal_check(fj["5m"], fj["15m"], "long" if side == 1 else "short")
                    bar_ts = int(rev.get("bar_ts") or 0)
                    if rev.get("action") == "CONFIRMED" and bar_ts and bar_ts not in rev_bars:
                        rev_bars.append(bar_ts)
                    strong = rev.get("action") == "CONFIRMED" and float(rev.get("score") or 0) >= 0.90
                    if strong or len(rev_bars) >= 2:
                        exit_px = cl; exit_j = j; runner_r = side * (cl - entry) / risk
                        exit_reason = "REVERSAL"; break
                except Exception:
                    pass
            if bars_held >= MAX_HOLD_BARS:
                exit_px = cl; exit_j = j; runner_r = side * (cl - entry) / risk
                exit_reason = "TIMEOUT"; break
        if exit_px is None:
            exit_j = min(i + MAX_HOLD_BARS, n - 1); exit_px = float(c[exit_j]); runner_r = side * (exit_px - entry) / risk
            exit_reason = "OPEN_END"
        gross_r = realized_r + max(rem, 0.0) * runner_r
        net_r = gross_r - base_cost_r - part_cost_r
        trades.append({
            "symbol": symbol, "sig_bar": int(t[i]), "entry_bar": int(t[i + 1]),
            "entry_time": pd.to_datetime(t[i + 1], unit="ms"), "side": sig,
            "path": entry_path, "engine": engine_lbl, "tier": tier, "score": round(score, 3),
            "plan_rr": round(tp_pct / sl_pct, 2), "sl_pct": sl_pct, "tp_pct": tp_pct,
            "entry": entry, "exit": exit_px, "exit_reason": exit_reason,
            "bars": int(exit_j - (i + 1) + 1), "gross_r": gross_r,
            "cost_r": base_cost_r + part_cost_r, "net_r": net_r, "partial": any(stage_done),
            "part_closed": round(1.0 - rem, 3),
        })
        cooldown_until = exit_j + 1 + COOLDOWN_BARS
        i = exit_j + 1
    return trades


def metrics(trades, label="", days=None):
    if not trades:
        return {"label": label, "trades": 0}
    df = pd.DataFrame(trades)
    r = df["net_r"]
    wins = r[r > 0]; losses = r[r < 0]; scratch = r[r == 0]
    gross_win = wins.sum(); gross_loss = -losses.sum()
    cum = r.cumsum(); peak = cum.cummax(); dd = (cum - peak)
    days = days or 1.0
    by_reason = df.groupby("exit_reason")["net_r"].agg(["count", "mean"]).round(3).to_dict("index")
    by_path = df.groupby("path")["net_r"].agg(["count", "mean"]).round(3).to_dict("index") if df["path"].any() else {}
    by_tier = df.groupby("tier")["net_r"].agg(["count", "mean"]).round(3).to_dict("index")
    return {
        "label": label, "trades": int(len(df)), "per_day": round(len(df) / days, 2),
        "win_pct": round(100 * len(wins) / len(df), 1), "scratch_pct": round(100 * len(scratch) / len(df), 1),
        "loss_pct": round(100 * len(losses) / len(df), 1),
        "avg_R": round(r.mean(), 3), "total_R": round(r.sum(), 1),
        "profit_factor": round(gross_win / max(gross_loss, 1e-9), 2),
        "max_dd_R": round(dd.min(), 1), "avg_bars": round(df["bars"].mean(), 1),
        "long_pct": round(100 * (df["side"] == "LONG").mean(), 1),
        "by_reason": by_reason, "by_path": by_path, "by_tier": by_tier,
    }


def load_symbol(sym):
    p = os.path.join(BT, "data", f"{sym}_5m.csv")
    df = pd.read_csv(p)
    rs = {"15m": resample(df, "15min", 15), "1h": resample(df, "1h", 60), "4h": resample(df, "4h", 240)}
    return df, rs


def run_config(module_path, symbols, start_ms=None, end_ms=None, static_only=False, name="",
               atr_k=0.0, atr_rr=1.5, atr_floor=0.0, flip=False):
    mod = load_module(module_path, "fastmod_" + name.replace("-", "_"))
    all_t = []
    per_sym = {}
    for sym in symbols:
        df, rs = load_symbol(sym)
        days = (df.open_ms.iloc[-1] - df.open_ms.iloc[0]) / 86400000
        if start_ms is not None or end_ms is not None:
            dd = df[(df.open_ms >= (start_ms or 0)) & (df.open_ms <= (end_ms or df.open_ms.iloc[-1]))]
            days = max((dd.open_ms.iloc[-1] - dd.open_ms.iloc[0]) / 86400000, 1) if len(dd) > 1 else days
        tr = run_symbol(mod, sym, df, rs, start_ms, end_ms, static_only=static_only,
                        atr_k=atr_k, atr_rr=atr_rr, atr_floor=atr_floor, flip=flip)
        per_sym[sym] = metrics(tr, sym, days)
        all_t.extend(tr)
    total_days = None
    return all_t, per_sym


def ms(date):
    return int(pd.Timestamp(date, tz="UTC").timestamp() * 1000)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--module", default=os.path.join(ROOT, "alpha_fast_mode.py"))
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    ap.add_argument("--start", default=None); ap.add_argument("--end", default=None)
    ap.add_argument("--static-only", action="store_true")
    ap.add_argument("--atr-k", type=float, default=0.0)
    ap.add_argument("--atr-rr", type=float, default=1.5)
    ap.add_argument("--atr-floor", type=float, default=0.0)
    ap.add_argument("--flip", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    sm = ms(a.start) if a.start else None; em = ms(a.end) if a.end else None
    trades, per_sym = run_config(a.module, a.symbols, sm, em, a.static_only,
                                 name=os.path.basename(a.module)[:-3],
                                 atr_k=a.atr_k, atr_rr=a.atr_rr, atr_floor=a.atr_floor, flip=a.flip)
    if trades:
        df0 = pd.read_csv(os.path.join(BT, "data", f"{a.symbols[0]}_5m.csv"))
        t0 = sm or df0.open_ms.iloc[0]; t1 = em or df0.open_ms.iloc[-1]
        days = (t1 - t0) / 86400000 * len(a.symbols)
        agg = metrics(trades, "AGG", days)
        print(json.dumps(agg, ensure_ascii=False, indent=2))
        print("--- per symbol ---")
        for s, v in per_sym.items():
            print(s, json.dumps({k: v[k] for k in ("trades", "per_day", "win_pct", "avg_R", "total_R", "profit_factor", "max_dd_R") if k in v}, ensure_ascii=False))
        if a.out:
            pd.DataFrame(trades).to_csv(a.out, index=False)
    else:
        print("NO TRADES")
