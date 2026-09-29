import os,importlib.util
import pandas as pd, fast_backtest as fb
ROOT=os.path.dirname(os.path.abspath("."))
def loadprod(ver):
    spec=importlib.util.spec_from_file_location(f"fm_{ver}",os.path.join(ROOT,"alpha_fast_mode.py"))
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    m.set_active_version(ver)
    flags={"v5":(True,False),"v4":(True,False),"v4_trend":(True,True),"v3":(False,False)}[ver]
    m.FAST_V4_ENSEMBLE,m.FAST_V4_TREND_SLEEVE=flags
    return m
df=pd.read_csv("data_small/WIFUSDT_5m.csv").tail(6000).reset_index(drop=True)
rs={"15m":fb.resample(df,"15min",15),"1h":fb.resample(df,"1h",60),"4h":fb.resample(df,"4h",240)}
m=loadprod("v5"); eng=fb.Engine(m)
shown=0
for i in range(260,len(df)):
    pred,_=eng.decide("WIF",df,rs,i)
    if pred.get("signal") in ("LONG","SHORT"):
        fs=pred["fast_strategy"]
        print("v5 SIGNAL",pred["signal"],fs.get("v5_engine"),fs.get("version"),
              "sl=%.3f%% tp=%.3f%% rr=%.2f"%(100*fs["adaptive_sl_pct"],100*fs["adaptive_tp_pct"],fs["rr"]),
              "| owner",pred["fast_ownership"]["entry_owner"],"| label",pred["strategy_label"])
        print("   path:",fs["path"],"| evidence:",fs["evidence"],"| maker:",fs["maker_preferred"])
        shown+=1
        if shown>=3: break
print("shown",shown)
# 趋势 sleeve 默认关闭时，趋势 regime 应 FLAT；打开后应能出 TREND_PULLBACK
m2=loadprod("v5"); m2.FAST_V5_TREND_SLEEVE=True
e2=fb.Engine(m2); cnt={"TREND_PULLBACK":0}
for i in range(260,len(df)):
    p,_=e2.decide("WIF",df,rs,i)
    lab=(p.get("fast_strategy") or {}).get("v5_engine","")
    if lab=="TREND_PULLBACK": cnt["TREND_PULLBACK"]+=1
print("v5 trend-sleeve ON -> TREND_PULLBACK signals:",cnt["TREND_PULLBACK"])
