"""用 V7 录的实盘数据回测（爆仓、盘口失衡这些没有公开历史的打法，只能这样测）。
V7 每分钟一条，没有逐价位的足迹，所以只测用得上的打法：爆仓反转、盘口失衡突破、持仓骤降/背离、资金费率极端、
Delta 背离、吸收、大单之外的形态。止损止盈按每分钟最高最低价检查，同一分钟两个都碰到算先止损。
用法: python of_backtest_v7.py <V7文件夹或recorder_data> [天数] [K线分钟数] [币,币]"""
from __future__ import annotations

import math
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import of_v7data as V  # noqa: E402
from of_core import Bar, Detector, SIGNAL_NAMES, auto_row_size  # noqa: E402

FEE, SLIP = 0.0005, 0.0002
KINDS = ["liq_cascade", "book_imbalance", "oi_flush", "oi_divergence", "funding_extreme", "divergence", "absorption"]


def run_inst(rows, tf_min):
    tf = tf_min * 60_000
    vb = V.bars(rows, tf)
    ts = sorted(vb)
    if len(ts) < 40:
        return []
    row = auto_row_size([vb[t]["h"] - vb[t]["l"] for t in ts[:50]], 1e-8)
    det = Detector(row, enabled=KINDS)
    mins = [(r["ts"], r["open"], r["high"], r["low"], r["close"]) for r in rows if not math.isnan(r["close"])]
    mi = {m[0]: i for i, m in enumerate(mins)}
    trades, busy_until = [], {}
    for t in ts:
        v = vb[t]
        b = Bar(t=t, o=v["o"], h=v["h"], l=v["l"], c=v["c"], closed=True)
        b.rows = {int(math.floor(v["c"] / row)): [v["sell"], v["buy"]]}
        b.liq_long, b.liq_short = v["liq_long"], v["liq_short"]
        b.oi, b.funding, b.obi = v["oi"], v["funding"], v["obi"]
        for s in det.on_bar(b):
            start = t + tf
            if start not in mi or busy_until.get(s.kind, 0) > start:
                continue
            i = mi[start]
            entry = mins[i][1] * (1 + s.side * SLIP)
            if (s.side == 1 and not (s.stop < entry < s.target)) or (s.side == -1 and not (s.target < entry < s.stop)):
                continue
            px, j = None, i
            while j < len(mins) and mins[j][0] < start + det.p["max_hold"] * tf:
                _, o, h, l, c = mins[j]
                if s.side == 1 and l <= s.stop:
                    px = min(o, s.stop) * (1 - SLIP); break
                if s.side == -1 and h >= s.stop:
                    px = max(o, s.stop) * (1 + SLIP); break
                if (s.side == 1 and h >= s.target) or (s.side == -1 and l <= s.target):
                    px = s.target; break
                j += 1
            j = min(j, len(mins) - 1)
            if px is None:
                px = mins[j][4]
            busy_until[s.kind] = mins[j][0] + 1
            trades.append((s.kind, s.side * (px / entry - 1) - 2 * FEE))
    return trades


if __name__ == "__main__":
    d = V.find_dir(sys.argv[1] if len(sys.argv) > 1 else None)
    if d is None:
        sys.exit("没找到 V7 的 recorder_data")
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    tf_min = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    data = V.load(d, sys.argv[4].split(",") if len(sys.argv) > 4 else None, days=days)
    span = max((r[-1]["ts"] - r[0]["ts"] for r in data.values() if r), default=0) / 3_600_000
    print(f"V7 数据：{d}  {len(data)} 个币，约 {span:.1f} 小时，{tf_min} 分钟K线")
    allr = defaultdict(list)
    for inst, rows in data.items():
        for k, r in run_inst(rows, tf_min):
            allr[k].append(r)
    for k in KINDS:
        r = np.array(allr[k])
        if not len(r):
            print(f"  {SIGNAL_NAMES[k]:<8} 0笔")
            continue
        loss = -r[r <= 0].sum()
        pf = r[r > 0].sum() / loss if loss > 0 else float("inf")
        print(f"  {SIGNAL_NAMES[k]:<8} {len(r):4d}笔 胜率{(r > 0).mean()*100:3.0f}% PF {pf:.2f} 每笔{r.mean()*100:+.3f}%")
    if span < 24 * 14:
        print("提醒：数据不到两周，笔数太少，结果只能参考，不能当结论。")
