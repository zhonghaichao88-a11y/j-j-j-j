#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""豆包的方案三回测（用户转来的原代码）。只改了一处会报错的地方：want_open((sym, -1)) → want_open.append((sym, -1))。
主程序改为按命令行参数跑一个口径，并输出交易明细。"""
import os, json, glob, sys
import numpy as np
import pandas as pd
import talib

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

EXCLUDE = {
    "CRCLBUSDT", "GOOGLBUSDT", "MSTRBUSDT", "MUBUSDT", "NVDABUSDT",
    "SNDKBUSDT", "SNXXBUSDT", "SOXLBUSDT", "SPCXBUSDT", "TSLABUSDT",
    "PAXGUSDT", "XAUTUSDT", "RLUSDUSDT", "XUSDUSDT",
}

FEE = 0.001
TP_GROSS = 0.012
TP_NET = 0.010
SL = 0.25
SL_NET = -(SL + 2 * FEE)
POSITION_PCT = 0.10
MAX_POS = 10
ADX_N = 14
MOM_N = 14
DI_N = 14
ADX_THR = 25
DI_THR = 25

def load_all():
    top = [s for s, _ in json.load(open(os.path.join(HERE, "top100.json")))]
    frames = {}
    for f in sorted(glob.glob(os.path.join(DATA, "*_1h.csv"))):
        sym = os.path.basename(f).replace("_1h.csv", "")
        if sym in EXCLUDE:
            continue
        d = pd.read_csv(f)
        for c in ["open", "high", "low", "close"]:
            d[c] = d[c].astype(float)
        d["t"] = pd.to_datetime(d["open_ms"], unit="ms", utc=True)
        d = d.set_index("t").sort_index()
        frames[sym] = d
    order = [s for s in top if s in frames]
    order += [s for s in frames if s not in order]
    return frames, order

def btc_daily_regime(btc: pd.DataFrame):
    daily = btc["close"].resample("1D").last().dropna()
    ema50 = talib.EMA(daily.to_numpy(), 50)
    regime = pd.Series(np.where(daily.to_numpy() > ema50, 1, -1), index=daily.index)
    return regime

def compute_signals(d: pd.DataFrame, di_n=DI_N):
    h, l, c = d["high"].to_numpy(), d["low"].to_numpy(), d["close"].to_numpy()
    adx = talib.ADX(h, l, c, ADX_N)
    pdi = talib.PLUS_DI(h, l, c, di_n)
    mdi = talib.MINUS_DI(h, l, c, di_n)
    mom = talib.MOM(c, MOM_N)
    long_sig = (adx > ADX_THR) & (mom > 0) & (pdi > DI_THR) & (pdi > mdi)
    short_sig = (adx > ADX_THR) & (mom < 0) & (mdi > DI_THR) & (mdi > pdi)
    return pd.DataFrame({"long": long_sig, "short": short_sig}, index=d.index)

def run_backtest(max_pos=MAX_POS, di_n=DI_N, label=""):
    frames, order = load_all()
    all_t = pd.DatetimeIndex(sorted(set().union(*[set(d.index) for d in frames.values()])))
    reindexed = {}
    signals = {}
    for s, d in frames.items():
        reindexed[s] = d.reindex(all_t)
        signals[s] = compute_signals(d, di_n).reindex(all_t).fillna(False)

    btc = frames["BTCUSDT"]
    regime = btc_daily_regime(btc)
    reg_aligned = pd.Series(index=all_t, dtype=float)
    dvals = regime.index
    for t in all_t:
        prev_day = (t - pd.Timedelta(days=1)).normalize()
        pos = dvals.searchsorted(prev_day, side="right") - 1
        if pos >= 0:
            reg_aligned[t] = regime.iloc[pos]
    reg_aligned = reg_aligned.ffill()

    cash = 10000.0
    positions = {}
    pending = []
    trades = []
    equity_curve = []

    for i, t in enumerate(all_t):
        for (sym, action, side) in pending:
            d = reindexed[sym]
            op = d["open"].iloc[i]
            if np.isnan(op):
                continue
            if action == "CLOSE" and sym in positions:
                p = positions.pop(sym)
                r = _exit_return(p, op, "REV")
                cash += p["notional"] * r
                trades.append(_trade(sym, p, t, op, "REV", r))
            elif action == "OPEN" and sym not in positions and len(positions) < max_pos:
                notional = _equity(cash, positions, reindexed, i) * POSITION_PCT
                positions[sym] = {"side": side, "entry": op, "notional": notional,
                                  "entry_t": t, "tp_px": op * (1 + TP_GROSS) if side == 1
                                  else op * (1 - TP_GROSS),
                                  "sl_px": op * (1 - SL) if side == 1 else op * (1 + SL)}
        pending = []

        for sym in list(positions.keys()):
            d = reindexed[sym]
            hi, lo = d["high"].iloc[i], d["low"].iloc[i]
            if np.isnan(hi):
                continue
            p = positions[sym]
            hit_sl = (lo <= p["sl_px"]) if p["side"] == 1 else (hi >= p["sl_px"])
            hit_tp = (hi >= p["tp_px"]) if p["side"] == 1 else (lo <= p["tp_px"])
            if hit_sl:
                positions.pop(sym)
                cash += p["notional"] * SL_NET
                trades.append(_trade(sym, p, t, p["sl_px"], "SL", SL_NET))
            elif hit_tp:
                positions.pop(sym)
                cash += p["notional"] * TP_NET
                trades.append(_trade(sym, p, t, p["tp_px"], "TP", TP_NET))

        want_open = []
        for sym in order:
            sig = signals[sym]
            if i >= len(sig):
                continue
            ls, ss = sig["long"].iloc[i], sig["short"].iloc[i]
            if sym in positions:
                p = positions[sym]
                if p["side"] == 1 and ss:
                    pending.append((sym, "CLOSE", -1))
                elif p["side"] == -1 and ls:
                    pending.append((sym, "CLOSE", 1))
                continue
            r = reg_aligned.iloc[i]
            if r == 1 and ls:
                want_open.append((sym, 1))
            elif r == -1 and ss:
                want_open.append((sym, -1))      # 原代码这里写成 want_open((sym, -1))，会报错
        for sym, side in want_open:
            if len(positions) + sum(1 for a in pending if a[1] == "OPEN") < max_pos:
                pending.append((sym, "OPEN", side))

        eq = _equity(cash, positions, reindexed, i)
        equity_curve.append((t, eq))

    last_i = len(all_t) - 1
    for sym in list(positions.keys()):
        p = positions.pop(sym)
        d = reindexed[sym]
        px = d["close"].iloc[last_i]
        r = _exit_return(p, px, "END")
        cash += p["notional"] * r
        trades.append(_trade(sym, p, all_t[last_i], px, "END", r))

    return pd.DataFrame(trades), pd.DataFrame(equity_curve, columns=["t", "equity"])

def _equity(cash, positions, reindexed, i):
    e = cash
    for sym, p in positions.items():
        cp = reindexed[sym]["close"].iloc[i]
        if np.isnan(cp):
            cp = p["entry"]
        rr = (cp / p["entry"] - 1) if p["side"] == 1 else (p["entry"] / cp - 1)
        e += p["notional"] * rr
    return e

def _exit_return(p, exit_px, kind):
    gross = (exit_px / p["entry"] - 1) if p["side"] == 1 else (p["entry"] / exit_px - 1)
    return gross - 2 * FEE

def _trade(sym, p, t, exit_px, kind, r):
    return {"symbol": sym, "side": p["side"], "entry_t": p["entry_t"],
            "exit_t": t, "entry": p["entry"], "exit": exit_px,
            "notional": p["notional"], "type": kind, "ret": r}

def stats(trades, eq, label):
    n = len(trades)
    wins = trades[trades["ret"] > 0]
    losses = trades[trades["ret"] <= 0]
    gross_win = (wins["ret"] * wins["notional"]).sum()
    gross_loss = -(losses["ret"] * losses["notional"]).sum()
    pf = gross_win / gross_loss if gross_loss > 0 else np.nan
    e = eq["equity"].to_numpy()
    dd = ((np.maximum.accumulate(e) - e) / np.maximum.accumulate(e)).max()
    print(f"\n===== {label} =====")
    print(f"交易笔数 {n}  胜率 {len(wins)/n*100:.2f}%  PF {pf:.3f}  每笔平均 {trades['ret'].mean()*100:+.3f}%")
    print(f"出场类型 {trades['type'].value_counts().to_dict()}  空单 {(trades['side'] == -1).sum()}")
    print(f"REV 平均 {trades[trades.type == 'REV'].ret.mean()*100:+.2f}%")
    print(f"账户总收益 {(e[-1]/e[0]-1)*100:.1f}%  最大回撤 {dd*100:.1f}%", flush=True)

if __name__ == "__main__":
    di = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    tr, eq = run_backtest(max_pos=10, di_n=di)
    stats(tr, eq, f"豆包原代码：最多10仓，DI周期{di}")
    tr.to_csv(os.path.join(HERE, f"trades_di{di}.csv"), index=False)
