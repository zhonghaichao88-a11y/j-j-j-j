"""订单流打法回测：用欧易官方逐笔成交压出的 1 分钟足迹数据（fetch_fp.py 生成）。

不偷看未来：
  - K线收盘才判断信号，下一根K线第一分钟开盘价进场（市价）
  - 止损止盈用之后每一分钟的最高最低价检查；同一分钟两个都碰到，按先止损算
  - 每种打法同一时间只拿一单
成本：吃单手续费 0.05% + 滑点 0.02%，每边都算。

用法: python of_backtest.py <npz目录> BTC,ETH,SOL 5,15 [输出csv]
"""
from __future__ import annotations

import glob
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from of_core import Bar, Detector, SIGNAL_NAMES, auto_row_size  # noqa: E402

FEE = float(os.environ.get("OF_FEE", "0.0005"))     # 吃单 0.05%；挂单可设 0.0002
SLIP = float(os.environ.get("OF_SLIP", "0.0002"))
FADE = os.environ.get("OF_FADE") == "1"              # 反着做：信号说多就空（检验是不是方向反了）


def load(dirpath, sym):
    files = sorted(glob.glob(f"{dirpath}/{sym}_*.npz"))
    parts = [np.load(f) for f in files]
    if not parts:
        return None
    tick = float(parts[0]["tick"])
    cat = lambda k: np.concatenate([p[k] for p in parts])
    return {"tick": tick, "m": cat("m"), "b": cat("b"), "bid": cat("bid"), "ask": cat("ask"),
            "om": cat("om"), "o": cat("o"), "h": cat("h"), "l": cat("l"), "c": cat("c")}


def build_bars(d, tf_min):
    """1 分钟足迹 → tf 分钟足迹K线。返回 (bars, row_size, 分钟OHLC索引)"""
    om = d["om"]
    bar_id = om // tf_min
    # 先用前 7 天的K线波动定格子大小，之后固定
    ub = np.unique(bar_id)
    first = ub[: 7 * 1440 // tf_min]
    rng = []
    for k in first:
        s = bar_id == k
        rng.append(d["h"][s].max() - d["l"][s].min())
    row = auto_row_size(rng, d["tick"])

    bars = {}
    # OHLC
    for k, o, h, l, c in zip(bar_id, d["o"], d["h"], d["l"], d["c"]):
        b = bars.get(k)
        if b is None:
            b = bars[k] = Bar(t=int(k * tf_min * 60000), o=float(o), h=float(h), l=float(l), c=float(c))
        else:
            b.h = max(b.h, float(h)); b.l = min(b.l, float(l)); b.c = float(c)
    # 足迹
    mb = d["m"] // tf_min
    rows = np.floor(d["b"] * d["tick"] / row + 1e-9).astype(np.int64)
    for k, r, bid, ask in zip(mb, rows, d["bid"], d["ask"]):
        cell = bars[k].rows.setdefault(int(r), [0.0, 0.0])
        cell[0] += float(bid); cell[1] += float(ask)
    out = [bars[k] for k in sorted(bars)]
    for b in out:
        b.closed = True
    return out, row


def simulate(d, bars, row, tf_min, kinds=None, params=None):
    det = Detector(row, params, enabled=kinds)
    om, mo, mh, ml, mc = d["om"], d["o"], d["h"], d["l"], d["c"]
    idx = {int(m): i for i, m in enumerate(om)}
    open_until = defaultdict(int)        # kind -> 这单结束的分钟
    trades = []
    maxhold_min = det.p["max_hold"] * tf_min
    for bi, bar in enumerate(bars):
        sigs = det.on_bar(bar)
        if not sigs:
            continue
        start_min = (bar.t // 60000) + tf_min          # 下一根K线第一分钟
        if start_min not in idx:
            continue
        i0 = idx[start_min]
        for s in sigs:
            if FADE:
                s.side, s.stop, s.target = -s.side, 2 * bar.c - s.stop, 2 * bar.c - s.target
            if open_until[s.kind] > start_min:
                continue
            entry = mo[i0] * (1 + s.side * SLIP)
            if (s.side == 1 and not (s.stop < entry < s.target)) or (s.side == -1 and not (s.target < entry < s.stop)):
                continue                                   # 开盘已经越过止损或止盈，放弃
            exit_px, why, j = None, "time", i0
            end_min = start_min + maxhold_min
            j = i0
            while j < len(om) and om[j] < end_min:
                hi, lo, op = mh[j], ml[j], mo[j]
                if s.side == 1:
                    if lo <= s.stop:
                        exit_px, why = min(op, s.stop) * (1 - SLIP), "stop"; break
                    if hi >= s.target:
                        exit_px, why = s.target, "target"; break
                else:
                    if hi >= s.stop:
                        exit_px, why = max(op, s.stop) * (1 + SLIP), "stop"; break
                    if lo <= s.target:
                        exit_px, why = s.target, "target"; break
                j += 1
            if exit_px is None:
                j = min(j, len(om)) - 1
                exit_px = mc[j] * (1 - s.side * SLIP)
            ret = s.side * (exit_px / entry - 1) - 2 * FEE
            open_until[s.kind] = int(om[j]) + 1
            trades.append({"kind": s.kind, "side": s.side, "t": int(start_min), "ret": ret, "why": why,
                           "risk": abs(entry - s.stop) / entry})
    return trades


def stats(tr):
    if not tr:
        return {"n": 0}
    r = np.array([t["ret"] for t in tr])
    w, l = r[r > 0], r[r <= 0]
    pf = w.sum() / -l.sum() if l.sum() < 0 else float("inf")
    eq = np.cumsum(r)
    dd = float((np.maximum.accumulate(eq) - eq).max())
    return {"n": len(r), "win": len(w) / len(r), "pf": pf, "avg": r.mean(), "sum": r.sum(), "dd": dd,
            "gross_avg": (r + 2 * FEE).mean()}


if __name__ == "__main__":
    dirpath, syms, tfs = sys.argv[1], sys.argv[2].split(","), [int(x) for x in sys.argv[3].split(",")]
    out_csv = sys.argv[4] if len(sys.argv) > 4 else None
    rows_out = []
    for sym in syms:
        d = load(dirpath, sym)
        if d is None:
            continue
        for tf in tfs:
            bars, row = build_bars(d, tf)
            tr = simulate(d, bars, row, tf)
            mid_t = bars[len(bars) // 2].t // 60000
            by = defaultdict(list)
            for t in tr:
                by[(t["kind"], "前半" if t["t"] < mid_t else "后半")].append(t)
                by[(t["kind"], "全部")].append(t)
            print(f"\n=== {sym} {tf}分钟  格子 {row:g}  K线 {len(bars)}  {len(bars)*tf/1440:.0f}天")
            for kind in SIGNAL_NAMES:
                line = []
                for half in ("前半", "后半", "全部"):
                    s = stats(by[(kind, half)])
                    rows_out.append({"sym": sym, "tf": tf, "kind": kind, "half": half, **s})
                    if s["n"]:
                        line.append(f"{half} {s['n']:4d}笔 胜{s['win']*100:3.0f}% PF{s['pf']:.2f} 每笔{s['avg']*100:+.3f}%(未扣费{s['gross_avg']*100:+.3f}%) 合计{s['sum']*100:+.1f}%")
                    else:
                        line.append(f"{half} 0笔")
                print(f"{SIGNAL_NAMES[kind]:<8} | " + " | ".join(line), flush=True)
    if out_csv:
        import csv
        keys = ["sym", "tf", "kind", "half", "n", "win", "pf", "avg", "gross_avg", "sum", "dd"]
        with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows_out)
