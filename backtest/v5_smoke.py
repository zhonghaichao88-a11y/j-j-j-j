import os,sys,importlib.util
import pandas as pd, fast_backtest as fb
ROOT=os.path.dirname(os.path.abspath("."))
def loadprod(ver):
    spec=importlib.util.spec_from_file_location(f"fm_{ver}",os.path.join(ROOT,"alpha_fast_mode.py"))
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    m.set_active_version(ver)
    flags={"v5":(True,False),"v4":(True,False),"v4_trend":(True,True),"v3":(False,False)}[ver]
    m.FAST_V4_ENSEMBLE,m.FAST_V4_TREND_SLEEVE=flags
    return m
df=pd.read_csv("data_small/WIFUSDT_5m.csv").tail(4000).reset_index(drop=True)
rs={"15m":fb.resample(df,"15min",15),"1h":fb.resample(df,"1h",60),"4h":fb.resample(df,"4h",240)}
for ver in ("v5","v4","v3"):
    m=loadprod(ver)
    t=fb.run_symbol_partial(m,"WIF",df,rs)
    eng=pd.Series([x.get("engine","") for x in t]).value_counts().to_dict() if t else {}
    print(ver,"trades",len(t),"engines",eng)
    if t:
        ex=t[0]; print("  keys ok:", all(k in ex for k in ("side","sl_pct","tp_pct","gross_r","engine","path")))
# 单独抓一个 v5 出信号的 decision，检查 schema/标签
m=loadprod("v5")
d=fb.build_market_data("WIF",df,rs)
sig=0
for i in range(260,len(df)):
    dd=fb.market_data_at(d,i)
    pred=m._build_decision("WIF",dd)
    fs=pred.get("fast_strategy",{})
    if pred.get("signal") in ("LONG","SHORT"):
        sig+=1
        if sig==1:
            print("v5 sample signal:",pred["signal"],fs.get("v5_engine"),fs.get("version"),pred.get("strategy_label"))
            print("  tp/sl/rr:",fs.get("adaptive_tp_pct"),fs.get("adaptive_sl_pct"),round(fs.get("rr",0),2),"owner:",pred["fast_ownership"]["entry_owner"])
print("v5 signals in window:",sig)
