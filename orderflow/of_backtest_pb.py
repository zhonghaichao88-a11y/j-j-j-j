"""实战版打法回测（of_playbook.py），用 1 分钟足迹K线，一分钟一分钟往前推，不偷看未来：
  - 信号出现后挂限价单，之后 3 分钟内价格碰到才算成交（挂单手续费 0.02%），没碰到就撤
  - 成交那一分钟如果也碰到止损，算止损（往坏处算）
  - 到 1R 先平一半（挂单），剩下的止损移到保本；止盈挂单，止损是市价（吃单 0.05% + 滑点 0.02%）
  - 最多拿 120 分钟，到时间市价平
  - 每个币同一时间只拿一单；当天的单数、亏损次数在平仓那一刻才计入（不提前知道结果）
用法: python of_backtest_pb.py <npz目录> BTC,ETH,SOL
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from of_core import Bar  # noqa: E402
from of_playbook import Playbook, PB_NAMES  # noqa: E402
import of_backtest as B  # noqa: E402

MAKER, TAKER, SLIP = 0.0002, 0.0005, 0.0002
FILL_MIN, MAX_HOLD = 3, 120


def minute_bars(d, tf=1):
    """tf 分钟一根的足迹K线（默认 1 分钟）"""
    tick = d["tick"]
    bars = {}
    for m, o, h, l, c in zip(d["om"], d["o"], d["h"], d["l"], d["c"]):
        k = int(m) // tf
        b = bars.get(k)
        if b is None:
            bars[k] = Bar(t=k * tf * 60000, o=float(o), h=float(h), l=float(l), c=float(c), closed=True)
        else:
            b.h = max(b.h, float(h)); b.l = min(b.l, float(l)); b.c = float(c)
    for m, b_, bid, ask in zip(d["m"], d["b"], d["bid"], d["ask"]):
        bar = bars.get(int(m) // tf)
        if bar is not None:
            cell = bar.rows.setdefault(int(b_), [0.0, 0.0])
            cell[0] += float(bid); cell[1] += float(ask)
    return [bars[k] for k in sorted(bars)], tick


def run(d, params=None, kinds=None, tf=1):
    """tf>1 时用 tf 分钟K线确认信号、也按 tf 分钟推进（止损止盈按这根K线的高低判断，更保守）"""
    bars, tick = minute_bars(d, tf)
    pb = Playbook(tick, params, kinds)
    pending, pos, trades = None, None, []
    for b in bars:
        m = b.t // 60000
        # 1 先处理已有持仓
        if pos is not None:
            s = pos
            hit_stop = (b.l <= s["stop"]) if s["side"] == 1 else (b.h >= s["stop"])
            if hit_stop:
                px = (min(b.o, s["stop"]) if s["side"] == 1 else max(b.o, s["stop"])) * (1 - s["side"] * SLIP)
                s["pnl"] += s["left"] * (s["side"] * (px / s["entry"] - 1) - TAKER)
                s["left"] = 0; s["why"] = "止损" if not s["half"] else "保本/止损"
            else:
                one_r = s["entry"] + s["side"] * s["risk"]
                if not s["half"] and ((b.h >= one_r) if s["side"] == 1 else (b.l <= one_r)):
                    s["pnl"] += 0.5 * (s["side"] * (one_r / s["entry"] - 1) - MAKER)
                    s["left"] = 0.5; s["half"] = True; s["stop"] = s["entry"]
                if (b.h >= s["target"]) if s["side"] == 1 else (b.l <= s["target"]):
                    s["pnl"] += s["left"] * (s["side"] * (s["target"] / s["entry"] - 1) - MAKER)
                    s["left"] = 0; s["why"] = "止盈"
                elif m - s["m0"] >= MAX_HOLD:
                    px = b.c * (1 - s["side"] * SLIP)
                    s["pnl"] += s["left"] * (s["side"] * (px / s["entry"] - 1) - TAKER)
                    s["left"] = 0; s["why"] = "到时间"
            if s["left"] == 0:
                trades.append(s)
                pb.record_result(s["pnl"] > 0)
                pos = None
        # 2 挂着的限价单：这一分钟碰到就成交
        if pending is not None and pos is None:
            sg = pending
            touched = (b.l <= sg.entry) if sg.side == 1 else (b.h >= sg.entry)
            if touched:
                pos = {"kind": sg.kind, "side": sg.side, "entry": sg.entry, "stop": sg.stop, "target": sg.target,
                       "risk": abs(sg.entry - sg.stop), "m0": m, "pnl": -MAKER, "left": 1.0, "half": False,
                       "why": "", "level": sg.level, "t": b.t}
                pb.trades_today += 1
                pending = None
                # 成交这一分钟就碰到止损：算止损
                if (b.l <= pos["stop"]) if pos["side"] == 1 else (b.h >= pos["stop"]):
                    px = pos["stop"] * (1 - pos["side"] * SLIP)
                    pos["pnl"] += pos["side"] * (px / pos["entry"] - 1) - TAKER
                    pos["left"] = 0; pos["why"] = "止损"
                    trades.append(pos); pb.record_result(False); pos = None
            elif m - sg.t // 60000 > max(FILL_MIN, tf):
                pending = None
        # 3 这根K线收盘，看有没有新信号
        sigs = pb.on_bar(b)
        if sigs and pos is None and pending is None:
            pending = sigs[0]
    return trades


def report(sym, trades, t_mid):
    print(f"\n=== {sym}")
    by = defaultdict(list)
    for t in trades:
        by[(t["kind"], "前半" if t["t"] < t_mid else "后半")].append(t)
        by[(t["kind"], "全部")].append(t)
    out = {}
    for k, name in PB_NAMES.items():
        parts = []
        for h in ("前半", "后半", "全部"):
            r = np.array([t["pnl"] for t in by[(k, h)]])
            if not len(r):
                parts.append(f"{h} 0笔"); continue
            loss = -r[r <= 0].sum()
            pf = r[r > 0].sum() / loss if loss > 0 else float("inf")
            parts.append(f"{h} {len(r):4d}笔 胜{(r > 0).mean()*100:3.0f}% PF{pf:.2f} 每笔{r.mean()*100:+.3f}% 合计{r.sum()*100:+.1f}%")
            if h == "全部":
                out[k] = r
        print(f"{name:<8} | " + " | ".join(parts), flush=True)
    return out


if __name__ == "__main__":
    allr = defaultdict(list)
    for sym in sys.argv[2].split(","):
        d = B.load(sys.argv[1], sym)
        tr = run(d)
        mid = int((d["om"][0] + d["om"][-1]) // 2) * 60000
        for k, r in report(sym, tr, mid).items():
            allr[k] += list(r)
    print("\n=== 三个币合计")
    for k, r in allr.items():
        r = np.array(r); loss = -r[r <= 0].sum()
        print(f"{PB_NAMES[k]:<8} {len(r)}笔 胜{(r>0).mean()*100:.0f}% PF {r[r>0].sum()/loss if loss>0 else float('inf'):.2f} 每笔{r.mean()*100:+.3f}%")
