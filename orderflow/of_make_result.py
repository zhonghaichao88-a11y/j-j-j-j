"""把回测结果汇总成网页上显示的 of_backtest_result.json（三个币合在一起算）。
用法: python of_make_result.py <npz目录>"""
import json
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import of_backtest as B  # noqa: E402
from of_core import SIGNAL_NAMES  # noqa: E402


def pf(rs):
    rs = np.array(rs)
    if not len(rs):
        return 0.0
    loss = -rs[rs <= 0].sum()
    return float(rs[rs > 0].sum() / loss) if loss > 0 else 99.0


out = {}
for tf in (5, 15, 60):
    by = defaultdict(lambda: {"a": [], "b": []})
    for sym in ("BTC", "ETH", "SOL"):
        d = B.load(sys.argv[1], sym)
        if d is None:
            continue
        bars, row = B.build_bars(d, tf)
        mid = bars[len(bars) // 2].t // 60000
        for t in B.simulate(d, bars, row, tf):
            by[t["kind"]]["a" if t["t"] < mid else "b"].append(t["ret"])
    key = {5: "5m", 15: "15m", 60: "1h"}[tf]
    out[key] = {}
    for k in SIGNAL_NAMES:
        a, b = by[k]["a"], by[k]["b"]
        allr = a + b
        out[key][k] = {"n": len(allr), "win": float(np.mean(np.array(allr) > 0)) if allr else 0.0,
                       "pf": pf(allr), "pf_a": pf(a), "pf_b": pf(b),
                       "avg": float(np.mean(allr)) if allr else 0.0}
    print(key, {k: round(v["pf"], 2) for k, v in out[key].items()}, flush=True)
json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "of_backtest_result.json"), "w",
               encoding="utf-8"), ensure_ascii=False, indent=1)
