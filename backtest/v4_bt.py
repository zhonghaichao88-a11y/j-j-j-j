#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FAST v4（生产 _build_decision）真实数据回测：6币全历史，INS/OOS，多成本，分批开关对照。"""
import os, sys
import numpy as np, pandas as pd
BT = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(BT); sys.path.insert(0, BT)
import fast_backtest as fb

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "LINKUSDT"]
mod = fb.load_module(os.path.join(ROOT, "alpha_fast_mode.py"), "fastmod_v4")
INS0, INS1 = fb.ms("2026-03-01"), fb.ms("2026-06-30 23:59")
OOS0, OOS1 = fb.ms("2026-07-01"), fb.ms("2026-09-15 23:59")

frames = []
for s in SYMS:
    df, rs = fb.load_symbol(s)
    t1 = fb.run_symbol(mod, s, df, rs)
    for x in t1:
        x["mode"] = "v4_noPartial"
    t2 = fb.run_symbol_partial(mod, s, df, rs)
    for x in t2:
        x["mode"] = "v4_partial"
    frames += t1 + t2
    print(s, "noPart", len(t1), "partial", len(t2), flush=True)

t = pd.DataFrame(frames)
t["ms"] = pd.to_datetime(t["entry_time"]).astype("int64") // 10**6
t["period"] = np.where((t.ms >= INS0) & (t.ms <= INS1), "INS",
                       np.where((t.ms >= OOS0) & (t.ms <= OOS1), "OOS", "?"))
t["engine"] = np.where(t.path.astype(str).str.contains("区间"), "MR",
                       np.where(t.engine.astype(str) == "RANGE_REVERSION", "MR", "TR"))
t["part_closed"] = t["part_closed"].fillna(0.0)
t.to_csv(os.path.join(BT, "v4_trades.csv"), index=False)


def net_for(g, cost):
    # 基础往返成本按全额；分批已平仓部分额外计一次单边(taker)费
    return g["gross_r"] - cost / g["sl_pct"] - g.get("part_closed", 0.0) * (cost / 2.0) / g["sl_pct"]


def stat(g, cost):
    net = net_for(g, cost)
    w = net > 0
    pf = net[net > 0].sum() / max(-net[net < 0].sum(), 1e-9)
    return pd.Series({"n": len(g), "win%": round(100 * w.mean(), 1), "avgR": round(net.mean(), 3),
                      "totR": round(net.sum(), 0), "PF": round(pf, 2),
                      "medSL%": round(100 * g.sl_pct.median(), 2), "bars": round(g.bars.mean(), 1)})


for mode in ("v4_noPartial", "v4_partial"):
    g0 = t[t["mode"] == mode]
    print(f"\n================ {mode} ================")
    for cost in (0.002, 0.0012, 0.0008, 0.0006):
        print(f"\n--- 往返成本 {cost*100:.2f}% ---")
        for per in ("INS", "OOS"):
            print(per, dict(stat(g0[g0.period == per], cost)))
        print("ALL", dict(stat(g0, cost)), " per-day(6币)", round(len(g0) / (199 * 6), 2))

print("\n\n################ 成本0.08% 下分引擎/分币/平仓方式（分批画像） ################")
g0 = t[t["mode"] == "v4_partial"].copy(); g0["net"] = net_for(g0, 0.0008)
print("\n分周期×引擎:")
print(g0.groupby(["period", "engine"]).apply(
    lambda x: pd.Series({"n": len(x), "win%": round(100 * (x.net > 0).mean(), 1),
                        "avgR": round(x.net.mean(), 3), "PF": round(x.net[x.net > 0].sum() / max(-x.net[x.net < 0].sum(), 1e-9), 2)}),
    include_groups=False).to_string())
print("\n分币:")
print(g0.groupby("symbol").apply(
    lambda x: pd.Series({"n": len(x), "win%": round(100 * (x.net > 0).mean(), 1),
                        "avgR": round(x.net.mean(), 3), "PF": round(x.net[x.net > 0].sum() / max(-x.net[x.net < 0].sum(), 1e-9), 2)}),
    include_groups=False).to_string())
print("\n平仓方式:")
print(g0.groupby("exit_reason").net.agg(["count", "mean"]).round(3).to_string())
print("\n引擎×周期 笔数:")
print(g0.groupby(["period", "engine"]).size().to_string())
