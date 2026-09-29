#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v5 契约审计（主流+小币）：决策不崩、factors 格式化不崩、下单数值正有限、严格 JSON 可序列化。
与 dual_engine_audit.py 同口径，额外覆盖 v5 默认(区间质量闸门) 与 v5 趋势sleeve开 两种状态。"""
import os, sys, json, math, importlib.util
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, ROOT)
import fast_backtest as fb


def load(name, fn):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, fn))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


A = load("afm_v5", "alpha_fast_mode.py")
import alpha_engine as E
from fastapi.encoders import jsonable_encoder

MAJ = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "LINKUSDT"]
SML = ["WIFUSDT", "SUIUSDT", "APTUSDT", "OPUSDT", "FETUSDT", "PEPEUSDT"]
STEP = 20


def load_sym(s):
    sub = "data" if s in MAJ else "data_small"
    df = pd.read_csv(os.path.join(HERE, sub, f"{s}_5m.csv"))
    return df, {"15m": fb.resample(df, "15min", 15), "1h": fb.resample(df, "1h", 60),
                "4h": fb.resample(df, "4h", 240)}


def strict_json(o):
    def conv(x):
        if isinstance(x, dict): return {k: conv(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)): return [conv(v) for v in x]
        if isinstance(x, np.floating): return float(x)
        if isinstance(x, np.integer): return int(x)
        if isinstance(x, float):
            if not math.isfinite(x): raise ValueError(f"non-finite {x}")
            return x
        return x
    return json.dumps(conv(o), ensure_ascii=False, allow_nan=False)


def _is_text(v):
    try:
        float(v); return False
    except (TypeError, ValueError):
        return True


def run_case(case, trend_on):
    A.set_active_version("v5")
    A.FAST_V4_ENSEMBLE, A.FAST_V4_TREND_SLEEVE = True, False
    A.FAST_V5_TREND_SLEEVE = bool(trend_on)
    eng = fb.Engine(A)
    n_dec = n_long = n_short = factor_text = 0; engines = set()
    crashes = []; numbad = []; jsonbad = []
    for s in MAJ + SML:
        df, rs = load_sym(s)
        for i in range(400, len(df) - 2, STEP):
            try:
                res, _ = eng.decide(s, df, rs, i)
            except Exception as ex:
                crashes.append((s, i, f"{type(ex).__name__}: {ex}")); continue
            n_dec += 1; sig = res.get("signal")
            if sig == "LONG": n_long += 1
            elif sig == "SHORT": n_short += 1
            fs = res.get("fast_strategy") or {}
            if fs.get("v5_engine"): engines.add(fs["v5_engine"])
            try:
                strict_json(jsonable_encoder(res))
            except Exception as ex:
                jsonbad.append((s, i, sig, str(ex)[:90]))
            if sig in ("LONG", "SHORT"):
                try:
                    fac = (res.get("entry_price_confirmation") or {}).get("factors") or {}
                    "；".join(E._fmt_factor(k, v) for k, v in fac.items())
                    if any(_is_text(v) for v in fac.values()): factor_text += 1
                except Exception as ex:
                    crashes.append((s, i, f"factors格式化崩溃: {ex}"))
                for k in ["tp", "sl", "base_tp", "base_sl", "confidence", "horizon"]:
                    try:
                        v = float(res.get(k)); assert math.isfinite(v) and v > 0
                    except Exception:
                        numbad.append((s, i, sig, k, res.get(k)))
    print(f"\n===== v5 / {case} =====")
    print(f"  决策 {n_dec} 笔: LONG={n_long} SHORT={n_short}; 引擎标签={sorted(engines)}; 文字因子信号={factor_text}")
    print(("  ❌ 崩溃: " + str(crashes[:5])) if crashes else "  ✅ 决策/factors 格式化零崩溃")
    print(("  ❌ 数值异常: " + str(numbad[:5])) if numbad else "  ✅ tp/sl/confidence/horizon 正有限")
    print(("  ❌ JSON 失败: " + str(jsonbad[:5])) if jsonbad else "  ✅ 全部决策严格 JSON 序列化通过")
    return not (crashes or numbad or jsonbad)


ok1 = run_case("默认(区间质量闸门，趋势sleeve关)", False)
ok2 = run_case("趋势sleeve开", True)
A.set_active_version("v4"); A.FAST_V5_TREND_SLEEVE = False
print("\n审计结果：", "全部通过 ✅" if (ok1 and ok2) else "存在问题 ❌")
sys.exit(0 if (ok1 and ok2) else 1)
