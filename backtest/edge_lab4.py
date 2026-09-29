#!/usr/bin/env python3
"""15m 顺势扫损 诊断矩阵：方向/止损缓冲/保本/成本/同根先后 上下界。"""
import os,sys
import numpy as np,pandas as pd
BT=os.path.dirname(os.path.abspath(__file__));sys.path.insert(0,BT)
import fast_backtest as fb
from edge_lab3 import prep, gen, SYMS, ag

def sim2(E, base, pol):
    o=base["open"].to_numpy();h=base["high"].to_numpy();l=base["low"].to_numpy();c=base["close"].to_numpy()
    n=len(c);cost=pol["cost"];out=[];ready=-1
    for e in E:
        i=e["i"]
        if i<ready or i+1>=n: continue
        side=e["side"]
        if pol.get("side") and side!=pol["side"]: continue
        entry=float(o[i+1]); buf=pol["buf"]*e["atr"]
        stop=(l[i]-buf) if side==1 else (h[i]+buf)
        risk=(entry-stop) if side==1 else (stop-entry)
        if risk<=0: continue
        slp=risk/entry
        rr_struct=side*(e["liq"]-entry)/risk
        rr=min(max(rr_struct,1.0),pol["rrmax"]) if rr_struct>0 else pol["rrmax"]
        if rr_struct<1.0: continue
        tpp=slp*rr;costr=cost/slp
        stop=entry*(1-slp) if side>0 else entry*(1+slp)
        tp=entry*(1+tpp) if side>0 else entry*(1-tpp)
        be=entry*(1+cost*0.5) if side>0 else entry*(1-cost*0.5)
        armed=False;px=None;res=None;jend=None
        for j in range(i+1,min(i+1+pol.get("hold",24),n)):
            hi=h[j];lo=l[j];cl=c[j];bars=j-i
            tpHit=(side>0 and hi>=tp) or (side<0 and lo<=tp)
            slHit=(side>0 and lo<=stop) or (side<0 and hi>=stop)
            if tpHit and slHit:
                # 同根K同时触达：pol['tie']='sl'保守 / 'tp'乐观，给出上下界
                if pol.get("tie","sl")=="sl": tpHit=False
                else: slHit=False
            if slHit: px=stop;res=("TP" if (not armed and ((side>0 and stop>=tp*0.999999) or (side<0 and stop<=tp*1.000001))) else ("BE" if armed else "SL"));jend=j;break
            if tpHit: px=tp;res="TP";jend=j;break
            fav=side*((hi-entry) if side>0 else (entry-lo))/risk
            if pol.get("be",True) and not armed and fav>=1.0: armed=True;stop=be
            if bars>=pol.get("hold",24): px=cl;res="TIME";jend=j;break
        if px is None: jend=min(i+pol.get("hold",24),n-1);px=c[jend];res="END"
        out.append(side*(px-entry)/risk-costr);ready=jend+1+3
    return np.array(out) if out else np.array([])

if __name__=="__main__":
    ins=(fb.ms("2026-03-01"),fb.ms("2026-06-30"));oos=(fb.ms("2026-07-01"),fb.ms("2026-09-15"))
    sd={}
    for s in SYMS:
        df5,rs=fb.load_symbol(s); b=rs["15m"].reset_index(drop=True).rename(columns={"ts_ms":"open_ms"})
        I=prep(b,[(rs["1h"],60),(rs["4h"],240)],48); sd[s]=(b,I)
    def run(lo,hi,pol):
        vals=[]
        for s in SYMS:
            b,I=sd[s]; E=[e for e in gen(I,"trend_sweep") if lo<=b.open_ms.iloc[e["i"]]<=hi]
            vals.append(sim2(E,b,pol))
        return ag(np.concatenate(vals) if vals else np.array([]))
    rows=[]
    for side in (0,1,-1):
        for buf in (0.25,0.6,1.0):
            for rrmax in (1.5,2.5):
                for be in (True,False):
                    pol=dict(cost=0.0005,buf=buf,rrmax=rrmax,be=be,hold=24,side=side,tie="sl")
                    a=run(*ins,pol);b=run(*oos,pol)
                    rows.append((side,buf,rrmax,be,a.get("n"),a.get("win"),a.get("avgR"),a.get("pf"),b.get("win"),b.get("avgR"),b.get("pf")))
    df=pd.DataFrame(rows,columns="side buf rr be n INSwin INSavgR INSpf OOSwin OOSavgR OOSpf".split())
    print("=== maker成本0.05% 同根先SL ===")
    print(df.to_string(index=False))
    print("\n=== 同根先TP(乐观上界) 子集 side all, buf0.6 ===")
    for tie in ("sl","tp"):
        for cost in (0.0005,0.0012):
            pol=dict(cost=cost,buf=0.6,rrmax=2.0,be=True,hold=24,side=0,tie=tie)
            print(tie,cost,"INS",run(*ins,pol),"OOS",run(*oos,pol))
