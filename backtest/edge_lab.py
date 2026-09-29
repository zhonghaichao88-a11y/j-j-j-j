#!/usr/bin/env python3
"""聚焦 edge 实验：成本/止损/止盈/管理 几何扫描，样本内 vs 样本外。"""
import os,sys,json
import numpy as np,pandas as pd
BT=os.path.dirname(os.path.abspath(__file__));sys.path.insert(0,BT)
import fast_backtest as fb
from arch_edge import archetype_signals

def sim(E, df, pol):
    o=df["open"].to_numpy();h=df["high"].to_numpy();l=df["low"].to_numpy();c=df["close"].to_numpy()
    n=len(c); cost=pol.get("cost",0.0015); bebuf=cost*0.5
    res=[];ready=-1
    for e in E:
        i=e["i"]
        if i<ready or i+1>=n: continue
        side=e["side"];entry=float(o[i+1])
        slp=max(e["atr"]/entry*pol["k"],pol.get("floor",0.0)); tpp=slp*pol["rr"]; risk=entry*slp
        costr=cost/slp
        stop=entry*(1-slp) if side>0 else entry*(1+slp)
        tp=entry*(1+tpp) if side>0 else entry*(1-tpp)
        be=entry*(1+bebuf) if side>0 else entry*(1-bebuf)
        armed=False;maxr=0;out=None;px=None;jend=None;partial_done=False;partial_r=0.0
        for j in range(i+1,min(i+1+pol.get("hold",48),n)):
            hi=h[j];lo=l[j];cl=c[j];bars=j-i
            if side>0 and lo<=stop:
                px=stop; out=("TP" if (not armed and stop>=tp*0.999999) else ("BE" if armed else "SL"));jend=j;break
            if side<0 and hi>=stop:
                px=stop; out=("TP" if (not armed and stop<=tp*1.000001) else ("BE" if armed else "SL"));jend=j;break
            if side>0 and hi>=tp: px=tp;out="TP";jend=j;break
            if side<0 and lo<=tp: px=tp;out="TP";jend=j;break
            fav=(hi-entry)/risk if side>0 else (entry-lo)/risk
            maxr=max(maxr,fav)
            # 分批：+1R 平半仓并保本
            if pol.get("partial") and not partial_done and fav>=1.0:
                partial_done=True;partial_r=0.5*1.0;armed=True
                stop=be
            elif (not armed) and fav>=pol.get("be_r",1.0):
                armed=True;stop=be
            if pol.get("stagnate") and bars>=8 and maxr<0.3:
                px=cl;out="STAG";jend=j;break
            if bars>=pol.get("hold",48): px=cl;out="TIME";jend=j;break
        if px is None:
            jend=min(i+pol.get("hold",48),n-1);px=c[jend];out="END"
        gr=side*(px-entry)/risk
        if partial_done:
            # 后半仓按结果结算（TP/BE/TIME）
            rest = 1.0*pol["rr"] if out=="TP" else (0.0 if out in("BE",) else gr)
            gross=partial_r+0.5*rest
        else:
            gross=gr
        net=gross-costr
        res.append(net); ready=jend+1+3
    return np.array(res)

def report(syms, arch, pol, lo_ms, hi_ms, label):
    allr=[];ns=0
    for sym in syms:
        df,rs=fb.load_symbol(sym)
        _,S=archetype_signals(df,rs)
        E=[e for e in S[arch] if lo_ms<=df.open_ms.iloc[e["i"]]<=hi_ms]
        r=sim(E,df,pol); allr.append(r); ns+=len(r)
    r=np.concatenate(allr) if allr else np.array([])
    if not len(r): return {}
    win=(r>0).mean();pf=r[r>0].sum()/max(-r[r<0].sum(),1e-9)
    return dict(label=label,n=len(r),win=round(100*win,1),avgR=round(r.mean(),3),
                totR=round(r.sum(),0),pf=round(pf,2))

if __name__=="__main__":
    syms=["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","LINKUSDT"]
    ins=(fb.ms("2026-03-01"),fb.ms("2026-06-30"))
    oos=(fb.ms("2026-07-01"),fb.ms("2026-09-15"))
    models=["trend_sweep","sweep_rev","vwap_pull","breakout_pull","vwap_fade"]
    CACHE={}
    for sym in syms:
        df,rs=fb.load_symbol(sym); _,S=archetype_signals(df,rs); CACHE[sym]=(df,S)
        print("cached",sym,{k:len(v) for k,v in S.items()},flush=True)
    def rep(arch,pol,lo,hi):
        allr=[]
        for sym in syms:
            df,S=CACHE[sym]
            E=[e for e in S[arch] if lo<=df.open_ms.iloc[e["i"]]<=hi]
            allr.append(sim(E,df,pol))
        r=np.concatenate(allr) if allr else np.array([])
        win=(r>0).mean();pf=r[r>0].sum()/max(-r[r<0].sum(),1e-9)
        return dict(n=len(r),win=round(100*win,1),avgR=round(r.mean(),3),totR=round(r.sum(),0),pf=round(pf,2))
    print("\n=== 成本0.15% / BE保本 / 持48根 ===")
    for arch in models:
        print("\n###",arch)
        for (k,rr) in [(2.0,1.3),(2.5,1.5),(3.0,1.5),(3.0,2.0),(2.5,1.3)]:
            pol=dict(k=k,rr=rr,floor=0.004,cost=0.0015,be_r=1.0,hold=48,stagnate=False)
            print(f"k{k} rr{rr}: INS {rep(arch,pol,*ins)} | OOS {rep(arch,pol,*oos)}")
    print("\n=== trend_sweep 成本敏感性 k2.5 rr1.5 ===")
    for cost in (0.001,0.0015,0.002):
        pol=dict(k=2.5,rr=1.5,floor=0.004,cost=cost,hold=48)
        print(cost,"INS",rep("trend_sweep",pol,*ins),"OOS",rep("trend_sweep",pol,*oos))
    print("\n=== trend_sweep 分批(半仓1R+保本+runner) ===")
    for (k,rr) in [(2.5,2.0),(3.0,2.5),(2.5,3.0)]:
        pol=dict(k=k,rr=rr,floor=0.004,cost=0.0015,partial=True,hold=48)
        print(f"k{k} rr{rr}: INS",rep("trend_sweep",pol,*ins),"OOS",rep("trend_sweep",pol,*oos))
