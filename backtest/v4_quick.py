import os,sys; import numpy as np,pandas as pd
BT=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(BT); sys.path.insert(0,BT)
import fast_backtest as fb
SYMS=["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","LINKUSDT"]
mod=fb.load_module(os.path.join(ROOT,"alpha_fast_mode.py"),"fm")
INS0,INS1=fb.ms("2026-03-01"),fb.ms("2026-06-30 23:59"); OOS0,OOS1=fb.ms("2026-07-01"),fb.ms("2026-09-15 23:59")
fr=[]
for s in SYMS:
    df,rs=fb.load_symbol(s); t=fb.run_symbol_partial(mod,s,df,rs)
    fr+=t; print(s,len(t),flush=True)
t=pd.DataFrame(fr)
t["ms"]=pd.to_datetime(t.entry_time).astype("int64")//10**6
t["period"]=np.where((t.ms>=INS0)&(t.ms<=INS1),"INS",np.where((t.ms>=OOS0)&(t.ms<=OOS1),"OOS","?"))
t["engine"]=np.where((t.engine=="RANGE_REVERSION")|t.path.astype(str).str.contains("区间"),"MR","TR")
t["pc"]=t.part_closed.fillna(0.0)
def net(g,c): return g.gross_r - c/g.sl_pct - g.pc*(c/2)/g.sl_pct
def st(g,c):
    x=net(g,c); pf=x[x>0].sum()/max(-x[x<0].sum(),1e-9)
    return f"n={len(g):4d} win={100*(x>0).mean():4.1f}% avgR={x.mean():+.3f} totR={x.sum():+6.0f} PF={pf:.2f}"
for c in (0.0012,0.0008,0.0006,0.0004):
    print(f"\n== 往返成本 {c*100:.2f}% ==")
    for per in ("INS","OOS"):
        print(f" {per} ALL  ",st(t[t.period==per],c))
        for en in ("MR","TR"):
            g=t[(t.period==per)&(t.engine==en)]
            if len(g): print(f" {per} {en:2s}  ",st(g,c))
    print(" ALL      ",st(t,c))
t.to_csv("v4_quick_trades.csv",index=False)
print("\nexit reasons:\n",t.groupby(["engine","exit_reason"]).net_r if False else t.exit_reason.value_counts().to_string())
