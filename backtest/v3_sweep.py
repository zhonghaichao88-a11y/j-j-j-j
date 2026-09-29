#!/usr/bin/env python3
"""基于已采集v3信号，快速扫描出场管理/止损倍数/盈亏比/成本，按最差周期PF选稳健配置。"""
import os,sys
import numpy as np,pandas as pd
BT=os.path.dirname(os.path.abspath(__file__));sys.path.insert(0,BT)
import fast_backtest as fb
SYMS=["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","LINKUSDT"]
t=pd.read_csv(os.path.join(BT,"v3_trades.csv"))
t["ms"]=pd.to_datetime(t["entry_time"]).astype("int64")//10**6
DATA={s:fb.load_symbol(s)[0] for s in SYMS}
# 记录信号K的index（entry_bar是成交K，信号K=entry-1）
def idx_of(sym,msv):
    df=DATA[sym]; return int(np.searchsorted(df.open_ms.to_numpy(),msv,side="left"))
t["entry_i"]=[idx_of(r.symbol,r.entry_bar) for r in t.itertuples()]

def sim_trade(sym,ei,side,sl0,tp0,slm,rr,cost,stagnate,hold=48,be=True):
    df=DATA[sym];o=df.open.to_numpy();h=df.high.to_numpy();l=df.low.to_numpy();c=df.close.to_numpy()
    s=1 if side=="LONG" else -1
    entry=float(o[ei]); slp=sl0*slm; tpp=slp*rr
    costr=cost/slp
    sp=entry*(1-slp) if s>0 else entry*(1+slp)
    tp=entry*(1+tpp) if s>0 else entry*(1-tpp)
    bepx=entry*(1+0.001) if s>0 else entry*(1-0.001)
    armed=False;maxr=0
    for j in range(ei,min(ei+hold,len(c))):
        hi=h[j];lo=l[j];cl=c[j];bars=j-ei+1
        if s>0 and lo<=sp: px=sp; tag=("TP" if (not armed and sp>=tp*0.999999) else ("BE" if armed else "SL"));break
        if s<0 and hi>=sp: px=sp; tag=("TP" if (not armed and sp<=tp*1.000001) else ("BE" if armed else "SL"));break
        if s>0 and hi>=tp: px=tp;tag="TP";break
        if s<0 and lo<=tp: px=tp;tag="TP";break
        fav=s*((hi-entry) if s>0 else (entry-lo))/(entry*slp); maxr=max(maxr,fav)
        if be and not armed and fav>=1.0: armed=True;sp=bepx
        if stagnate and bars>=8 and maxr<0.3: px=cl;tag="STAG";break
        if bars>=hold: px=cl;tag="TIME";break
    else: px=c[min(ei+hold-1,len(c)-1)];tag="END"
    return s*(px-entry)/(entry*slp)-costr

def grp(g,**kw):
    r=np.array([sim_trade(x.symbol,x.entry_i,x.side,x.sl_pct,x.tp_pct,**kw) for x in g.itertuples()])
    return pd.Series({"n":len(r),"win%":round(100*(r>0).mean(),1),"avgR":round(r.mean(),3),
                      "totR":round(r.sum(),0),"PF":round(r[r>0].sum()/max(-r[r<0].sum(),1e-9),2)})

tt=t[t.path=="顺向扫损MSS"].copy()
ins=tt[tt.period=="INS"];oos=tt[tt.period=="OOS"]
rows=[]
for slm in (1.0,1.4,1.8):
  for rr in (1.2,1.5,2.0):
    for stag in (False,True):
      for cost in (0.001,0.0015,0.002):
        kw=dict(slm=slm,rr=rr,cost=cost,stagnate=stag)
        a=grp(ins,**kw);b=grp(oos,**kw)
        rows.append(dict(slm=slm,rr=rr,stag=stag,cost=cost,
            INS_n=a["n"],INS_w=a["win%"],INS_R=a["avgR"],INS_PF=a["PF"],
            OOS_w=b["win%"],OOS_R=b["avgR"],OOS_PF=b["PF"],worst_PF=min(a["PF"],b["PF"])))
r=pd.DataFrame(rows).sort_values("worst_PF",ascending=False)
pd.set_option("display.width",200)
print("=== 仅顺向扫损MSS，按最差周期PF排序（前15）===")
print(r.head(15).to_string(index=False))
print("\n=== maker成本0.1% 下各配置 ===")
print(r[r.cost==0.001].sort_values("worst_PF",ascending=False).head(10).to_string(index=False))
