#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三引擎(v4 / v4_trend / v3)在真实6币数据上的契约审计：
- 决策本身不抛异常
- 非FLAT信号：复现 alpha_engine 下单前 factors 格式化(原中文float崩溃点) 不崩
- 下单关键数值 tp/sl/base_tp/base_sl/confidence/horizon 正有限
- 全部决策严格 JSON 序列化(allow_nan=False)，抓 NaN/Inf(原status500 bug)
"""
import os, sys, json, math, importlib.util, traceback
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, ROOT)
import fast_backtest as fb

def load(name, fn):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, fn)); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
A = load("afm", "alpha_fast_mode.py")
import alpha_engine as E   # 用生产里真实的 _fmt_factor

SYMS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","LINKUSDT"]
ENGINES = {"v4": (True, False), "v4_trend": (True, True), "v3": (False, False)}
STEP = 20

def strict_json(o):
    # 先把 numpy 标量转原生，再用禁止 NaN 的严格序列化
    def conv(x):
        if isinstance(x, dict): return {k: conv(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)): return [conv(v) for v in x]
        if isinstance(x, (np.floating,)): return float(x)
        if isinstance(x, (np.integer,)): return int(x)
        if isinstance(x, float):
            if not math.isfinite(x): raise ValueError(f"non-finite float {x}")
            return x
        return x
    return json.dumps(conv(o), ensure_ascii=False, allow_nan=False)

from fastapi.encoders import jsonable_encoder

def _is_text(v):
    try:
        float(v); return False
    except (TypeError, ValueError):
        return True

grand = {}
for ename,(ens,sleeve) in ENGINES.items():
    A.FAST_V4_ENSEMBLE = ens; A.FAST_V4_TREND_SLEEVE = sleeve
    eng = fb.Engine(A)
    n_dec=0; n_long=0; n_short=0; engines_seen=set()
    crashes=[]; numbad=[]; jsonbad=[]; factor_text=0
    for s in SYMS:
        df, rs = fb.load_symbol(s)
        for i in range(400, len(df)-2, STEP):
            try:
                res, _ = eng.decide(s, df, rs, i)
            except Exception as ex:
                crashes.append((s, i, f"{type(ex).__name__}: {ex}")); continue
            n_dec += 1
            sig = res.get("signal")
            if sig == "LONG": n_long += 1
            elif sig == "SHORT": n_short += 1
            fs = res.get("fast_strategy") or {}
            if fs.get("v4_engine"): engines_seen.add(fs.get("v4_engine"))
            # 严格 JSON（FLAT 与非FLAT 都查，覆盖 /alpha/status、/alpha/predict）
            try:
                strict_json(jsonable_encoder(res))
            except Exception as ex:
                jsonbad.append((s, i, sig, str(ex)[:90]))
            if sig in ("LONG", "SHORT"):
                # 复现生产 ENTER 行 1912 的 factors 格式化（原崩溃点）
                try:
                    epc = res.get("entry_price_confirmation") or {}
                    fac = epc.get("factors") or {}
                    line = "；".join(E._fmt_factor(k, v) for k, v in fac.items())
                    if any(_is_text(v) for v in fac.values()):
                        factor_text += 1
                except Exception as ex:
                    crashes.append((s, i, f"factors格式化崩溃: {type(ex).__name__}: {ex}"))
                # 下单关键数值
                for k in ["tp","sl","base_tp","base_sl","confidence","horizon"]:
                    try:
                        v = float(res.get(k)); assert math.isfinite(v) and v > 0
                    except Exception:
                        numbad.append((s, i, sig, k, res.get(k)))
    grand[ename] = dict(dec=n_dec, long=n_long, short=n_short, engines=sorted(engines_seen))
    print(f"\n===== 引擎 {ename} (ENSEMBLE={ens}, SLEEVE={sleeve}) =====")
    print(f"  决策 {n_dec} 笔: LONG={n_long} SHORT={n_short}; v4_engine标签={sorted(engines_seen)}; 含文字因子的信号={factor_text}")
    print(("  ❌ 决策/格式化崩溃: "+str(crashes[:5])) if crashes else "  ✅ 决策与 factors 格式化零崩溃（原bug①已覆盖v3/v4）")
    print(("  ❌ 下单关键数值异常: "+str(numbad[:5])) if numbad else "  ✅ 下单关键数值 tp/sl/confidence/horizon 均正有限")
    print(("  ❌ JSON NaN/序列化失败: "+str(jsonbad[:5])) if jsonbad else "  ✅ 全部决策严格 JSON 序列化通过（原bug②已覆盖v3/v4）")

print("\n汇总:")
for k,v in grand.items(): print(" ", k, v)
# 复位
A.FAST_V4_ENSEMBLE=True; A.FAST_V4_TREND_SLEEVE=False
print("\n审计结束。")
