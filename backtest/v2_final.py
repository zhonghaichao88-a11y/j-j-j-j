import os,sys
import numpy as np,pandas as pd
BT=os.path.dirname(os.path.abspath(__file__));ROOT=os.path.dirname(BT);sys.path.insert(0,BT)
import fast_backtest as fb
SYMS=["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","LINKUSDT"]
mod=fb.load_module(os.path.join(BT,"ref_alpha_fast_mode_v2.py"),"fastmod_v2ref")
allt=[]
for s in SYMS:
    df,rs=fb.load_symbol(s); tr=fb.run_symbol(mod,s,df,rs)
    print(s,len(tr),flush=True); allt.extend(tr)
t=pd.DataFrame(allt)
t["ms"]=pd.to_datetime(t["entry_time"]).astype("int64")//10**6
INS0=fb.ms("2026-03-01");INS1=fb.ms("2026-06-30 23:59");OOS0=fb.ms("2026-07-01");OOS1=fb.ms("2026-09-15 23:59")
t["period"]=np.where((t.ms>=INS0)&(t.ms<=INS1),"INS",np.where((t.ms>=OOS0)&(t.ms<=OOS1),"OOS","?"))
t.to_csv(os.path.join(BT,"v2_trades.csv"),index=False)
def stat(g,cost):
    net=g["gross_r"]-cost/g["sl_pct"];w=net>0
    return dict(n=len(g),win=round(100*w.mean(),1),avgR=round(net.mean(),3),totR=round(net.sum(),0),PF=round(net[net>0].sum()/max(-net[net<0].sum(),1e-9),2))
for cost in (0.002,0.0012,0.0008):
    print("cost",cost,"INS",stat(t[t.period=="INS"],cost),"OOS",stat(t[t.period=="OOS"],cost),"ALL",stat(t,cost))
print("median sl",t.groupby("period").sl_pct.median().round(4).to_dict())
print(t.groupby(["period","exit_reason"]).size())
