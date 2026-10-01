"""用程序自己录下来的实时数据（data_rec/）回测：包括真实爆仓、盘口失衡、大单墙这些没有历史数据的打法。
录的是每根K线收盘时的全部字段，所以和实盘看到的一模一样。
止损止盈只能按K线的最高最低价判断（同一根两个都碰到算先止损），比分钟级更保守。
用法: python of_backtest_rec.py BTC-USDT-SWAP [打法,打法]"""
import glob
import gzip
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from of_core import Bar, Detector, SIGNAL_NAMES  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FEE, SLIP = 0.0005, 0.0002


def load(inst):
    bars = []
    for f in sorted(glob.glob(os.path.join(HERE, "data_rec", "*", f"{inst}_bars.jsonl.gz"))):
        for line in gzip.open(f, "rt", encoding="utf-8"):
            d = json.loads(line)
            b = Bar(t=d["t"], o=d["o"], h=d["h"], l=d["l"], c=d["c"], closed=True)
            b.rows = {int(r): [x, y] for r, x, y in d["rows"]}
            for k in ("liq_long", "liq_short", "big_buy", "big_sell", "oi", "funding", "ls", "obi", "wall_bid", "wall_ask"):
                v = d.get(k)
                if v is not None:
                    setattr(b, k, float(v))
            b.row = d["row"]
            bars.append(b)
    bars.sort(key=lambda b: b.t)
    return bars


def run(inst, kinds=None):
    bars = load(inst)
    if len(bars) < 50:
        print(f"{inst} 只录了 {len(bars)} 根K线，太少，再录一段时间")
        return
    det = Detector(bars[0].row, enabled=kinds)
    trades, open_until = [], {}
    for i, bar in enumerate(bars[:-1]):
        for s in det.on_bar(bar):
            if open_until.get(s.kind, -1) >= i:
                continue
            nb = bars[i + 1]
            entry = nb.o * (1 + s.side * SLIP)
            if (s.side == 1 and not (s.stop < entry < s.target)) or (s.side == -1 and not (s.target < entry < s.stop)):
                continue
            j, px = i + 1, None
            while j < len(bars) and j <= i + det.p["max_hold"]:
                b = bars[j]
                if s.side == 1 and b.l <= s.stop:
                    px = min(b.o, s.stop) * (1 - SLIP); break
                if s.side == -1 and b.h >= s.stop:
                    px = max(b.o, s.stop) * (1 + SLIP); break
                if (s.side == 1 and b.h >= s.target) or (s.side == -1 and b.l <= s.target):
                    px = s.target; break
                j += 1
            j = min(j, len(bars) - 1)
            if px is None:
                px = bars[j].c
            open_until[s.kind] = j
            trades.append((s.kind, s.side * (px / entry - 1) - 2 * FEE))
    print(f"{inst} 录到 {len(bars)} 根K线")
    for k in SIGNAL_NAMES:
        r = np.array([x for kk, x in trades if kk == k])
        if len(r):
            loss = -r[r <= 0].sum()
            pf = r[r > 0].sum() / loss if loss > 0 else float("inf")
            print(f"  {SIGNAL_NAMES[k]:<8} {len(r):4d}笔 胜率{(r > 0).mean()*100:3.0f}% PF {pf:.2f} 每笔{r.mean()*100:+.3f}%")


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2].split(",") if len(sys.argv) > 2 else None)
