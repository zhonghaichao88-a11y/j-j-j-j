#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v5 生产级权威回测：直接驱动生产 alpha_fast_mode._build_decision（事件驱动、含真实持仓管理/分批）。
同一混合篮（主流币+小币）上对比 v4 与 v5，INS/OOS 分段、多成本档，输出真实胜率/PF/盈亏比。
用法: python3 v5_bt.py [v5]   （默认依次跑 v4、v5）"""
import os, sys, importlib.util
import numpy as np, pandas as pd
BT = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(BT); sys.path.insert(0, BT)
import fast_backtest as fb

MAJ = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "LINKUSDT"]
SML = ["WIFUSDT", "SUIUSDT", "SEIUSDT", "APTUSDT", "INJUSDT", "OPUSDT",
       "WLDUSDT", "FETUSDT", "PEPEUSDT", "AAVEUSDT", "ENSUSDT", "ORDIUSDT"]
INS0, INS1 = fb.ms("2026-03-01"), fb.ms("2026-06-30 23:59")
OOS0, OOS1 = fb.ms("2026-07-01"), fb.ms("2026-09-15 23:59")


def load(sym):
    sub = "data" if sym in MAJ else "data_small"
    df = pd.read_csv(os.path.join(BT, sub, f"{sym}_5m.csv"))
    return df, {"15m": fb.resample(df, "15min", 15), "1h": fb.resample(df, "1h", 60),
                "4h": fb.resample(df, "4h", 240)}


def load_prod(version):
    path = os.path.join(ROOT, "alpha_fast_mode.py")
    spec = importlib.util.spec_from_file_location(f"fastmod_{version}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.set_active_version(version)
    assert mod.get_active_version() == version
    # 显式同步开关，等价于 alpha_engine._apply_fast_engine
    flags = {"v5": (True, False), "v4": (True, False), "v4_trend": (True, True), "v3": (False, False)}
    mod.FAST_V4_ENSEMBLE, mod.FAST_V4_TREND_SLEEVE = flags[version]
    return mod


def run_version(version, basket):
    mod = load_prod(version)
    rows = []
    for s in basket:
        df, rs = load(s)
        t = fb.run_symbol_partial(mod, s, df, rs)
        rows += t
        print(f"[{version}] {s} trades={len(t)}", flush=True)
    t = pd.DataFrame(rows)
    t["ms"] = pd.to_datetime(t["entry_time"]).astype("int64") // 10**6
    t["period"] = np.where((t.ms >= INS0) & (t.ms <= INS1), "INS",
                           np.where((t.ms >= OOS0) & (t.ms <= OOS1), "OOS", "?"))
    t["version"] = version
    t.to_csv(os.path.join(BT, f"v5bt_{version}_trades.csv"), index=False)
    return t


def stat(g, cost):
    net = g.gross_r - cost / g.sl_pct - g.part_closed.fillna(0) * (cost / 2.0) / g.sl_pct
    w = net > 0
    pf = net[net > 0].sum() / max(-net[net < 0].sum(), 1e-9)
    aw = net[net > 0].mean() if w.any() else 0.0; al = -net[~w].mean() if (~w).any() else 0.0
    return pd.Series({"n": len(g), "win%": round(100 * w.mean(), 1), "avgR": round(net.mean(), 3),
                      "totR": round(net.sum(), 0), "PF": round(pf, 2), "payoff": round(aw / max(al, 1e-9), 2),
                      "medSL%": round(100 * g.sl_pct.median(), 2)})


def main():
    versions = [a for a in sys.argv[1:] if a.startswith("v")] or ["v4", "v5"]
    basket = MAJ + SML
    all_t = []
    for ver in versions:
        all_t.append(run_version(ver, basket))
    t = pd.concat(all_t, ignore_index=True)
    print("\n================ 生产事件驱动回测：混合篮(5主流+6小币) ================")
    for cost in (0.0012, 0.0008, 0.0006, 0.0004):
        print(f"\n########## 往返成本 {cost*100:.2f}% ##########")
        for ver in versions:
            tv = t[t.version == ver]
            for per in ("INS", "OOS"):
                print(ver, per, dict(stat(tv[tv.period == per], cost)))
            print(ver, "ALL", dict(stat(tv, cost)))
    print("\n===== 0.06% maker 下分版本×币种 =====")
    g = t.copy(); g["net"] = g.gross_r - 0.0006 / g.sl_pct - g.part_closed.fillna(0) * 0.0003 / g.sl_pct
    print(g.groupby(["version", "symbol"]).apply(
        lambda x: pd.Series({"n": len(x), "win%": round(100 * (x.net > 0).mean(), 1),
                             "PF": round(x.net[x.net > 0].sum() / max(-x.net[x.net < 0].sum(), 1e-9), 2),
                             "avgR": round(x.net.mean(), 3)}), include_groups=False).to_string())
    print("\n===== 0.06% 下分版本×引擎×平仓方式 =====")
    print(g.groupby(["version", "engine", "exit_reason"]).net.agg(["count", "mean"]).round(3).to_string())


if __name__ == "__main__":
    main()
