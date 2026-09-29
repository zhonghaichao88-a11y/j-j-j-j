#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""权威复核：用【生产 alpha_fast_mode._build_decision】+ fast_backtest 事件驱动口径，
在小币 data_small/ 上跑 v4（分批画像），给出真实胜率/PF；研究台结论以此为准。"""
import os, sys
import numpy as np, pandas as pd
BT = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(BT); sys.path.insert(0, BT)
import fast_backtest as fb

SMALL = os.path.join(BT, "data_small")
BASKET = ["WIFUSDT", "SUIUSDT", "SEIUSDT", "APTUSDT", "INJUSDT", "OPUSDT",
          "WLDUSDT", "FETUSDT", "PEPEUSDT", "AAVEUSDT", "ENSUSDT", "ORDIUSDT"]
INS0, INS1 = fb.ms("2026-03-01"), fb.ms("2026-06-30 23:59")
OOS0, OOS1 = fb.ms("2026-07-01"), fb.ms("2026-09-15 23:59")


def load_small(sym):
    df = pd.read_csv(os.path.join(SMALL, f"{sym}_5m.csv"))
    rs = {"15m": fb.resample(df, "15min", 15), "1h": fb.resample(df, "1h", 60), "4h": fb.resample(df, "4h", 240)}
    return df, rs


def main():
    mod = fb.load_module(os.path.join(ROOT, "alpha_fast_mode.py"), "fastmod_v4_small")
    frames = []
    for s in BASKET:
        df, rs = load_small(s)
        t = fb.run_symbol_partial(mod, s, df, rs)
        frames += t
        print(s, "trades", len(t), flush=True)
    t = pd.DataFrame(frames)
    t["ms"] = pd.to_datetime(t["entry_time"]).astype("int64") // 10**6
    t["period"] = np.where((t.ms >= INS0) & (t.ms <= INS1), "INS",
                           np.where((t.ms >= OOS0) & (t.ms <= OOS1), "OOS", "?"))
    t.to_csv(os.path.join(BT, "v4_small_trades.csv"), index=False)

    def net_for(g, cost):
        return g["gross_r"] - cost / g["sl_pct"] - g["part_closed"].fillna(0) * (cost / 2.0) / g["sl_pct"]

    def stat(g, cost):
        net = net_for(g, cost); w = net > 0
        pf = net[net > 0].sum() / max(-net[net < 0].sum(), 1e-9)
        aw = net[net > 0].mean() if (net > 0).any() else 0; al = -net[net < 0].mean() if (net < 0).any() else 0
        return pd.Series({"n": len(g), "win%": round(100 * w.mean(), 1), "avgR": round(net.mean(), 3),
                          "totR": round(net.sum(), 0), "PF": round(pf, 2), "payoff": round(aw / max(al, 1e-9), 2),
                          "medSL%": round(100 * g.sl_pct.median(), 2)})
    print("\n===== 生产 v4（分批）在 12 小币上的权威结果 =====")
    for cost in (0.0012, 0.0008, 0.0006, 0.0004):
        print(f"\n--- 往返成本 {cost*100:.2f}% ---")
        for per in ("INS", "OOS"):
            print(per, dict(stat(t[t.period == per], cost)))
        print("ALL", dict(stat(t, cost)))
    print("\n分币(0.06%):")
    g = t.copy(); g["net"] = net_for(g, 0.0006)
    print(g.groupby("symbol").apply(
        lambda x: pd.Series({"n": len(x), "win%": round(100 * (x.net > 0).mean(), 1),
                             "PF": round(x.net[x.net > 0].sum() / max(-x.net[x.net < 0].sum(), 1e-9), 2),
                             "avgR": round(x.net.mean(), 3)}), include_groups=False).to_string())
    print("\n平仓方式(0.06%):", g.groupby("exit_reason").net.agg(["count", "mean"]).round(3).to_dict())


if __name__ == "__main__":
    main()
