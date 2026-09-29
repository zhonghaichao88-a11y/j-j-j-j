#!/usr/bin/env python3
"""FAST v3 生产函数 真实数据回测：6币全历史，拆 INS/OOS，多成本敏感性。"""
import os,sys
import numpy as np,pandas as pd
BT=os.path.dirname(os.path.abspath(__file__));ROOT=os.path.dirname(BT);sys.path.insert(0,BT)
import fast_backtest as fb

SYMS=["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","LINKUSDT"]
mod=fb.load_module(os.path.join(ROOT,"alpha_fast_mode.py"),"fastmod_v3")
INS0=fb.ms("2026-03-01");INS1=fb.ms("2026-06-30 23:59")
OOS0=fb.ms("2026-07-01");OOS1=fb.ms("2026-09-15 23:59")

allt=[]
for s in SYMS:
    df,rs=fb.load_symbol(s)
    tr=fb.run_symbol(mod,s,df,rs)
    print(s,"trades",len(tr),flush=True)
    allt.extend(tr)
t=pd.DataFrame(allt)
t["ms"]=pd.to_datetime(t["entry_time"]).astype("int64")//10**6
t["period"]=np.where((t.ms>=INS0)&(t.ms<=INS1),"INS",np.where((t.ms>=OOS0)&(t.ms<=OOS1),"OOS","?"))
t.to_csv(os.path.join(BT,"v3_trades.csv"),index=False)

def stat(g,cost):
    net=g["gross_r"]-cost/g["sl_pct"]
    w=(net>0);pf=net[net>0].sum()/max(-net[net<0].sum(),1e-9)
    return pd.Series({"n":len(g),"win%":round(100*w.mean(),1),"avgR":round(net.mean(),3),
                      "totR":round(net.sum(),0),"PF":round(pf,2),
                      "avgBars":round(g["bars"].mean(),1)})

print("\n================ v3 汇总（净R，扣往返成本） ================")
for cost in (0.002,0.0012,0.0008):
    print(f"\n--- 往返成本 {cost*100:.2f}% ---")
    for per in ("INS","OOS"):
        print(per, dict(stat(t[t.period==per],cost)))
    print("ALL", dict(stat(t,cost)))
print("\n分币种（成本0.12%）:")
print(t.groupby(["period","symbol"]).apply(lambda g:stat(g,0.0012),include_groups=False).to_string())
print("\n分路径（成本0.12%）:")
print(t.groupby(["period","path"]).apply(lambda g:stat(g,0.0012),include_groups=False).to_string())
print("\n分强度（成本0.12%）:")
print(t.groupby(["period","tier"]).apply(lambda g:stat(g,0.0012),include_groups=False).to_string())
print("\n平仓原因计数:")
print(t.groupby(["period","exit_reason"]).size().to_string())
print("\n计划RR / 实际sl分布:")
print(t.groupby("period")[["plan_rr","sl_pct","tp_pct"]].median().round(4).to_string())
