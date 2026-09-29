#!/usr/bin/env python3
"""15m 顺势扫损 + 吊灯移动止损(让利润奔跑) 正偏趋势出场测试。"""
import os,sys
import numpy as np,pandas as pd
BT=os.path.dirname(os.path.abspath(__file__));sys.path.insert(0,BT)
import fast_backtest as fb
from edge_lab3 import prep, gen, SYMS, ag

def trail(E, base, pol):
    o=base["open"].to_numpy();h=base["high"].to_numpy();l=base["low"].to_numpy();c=base["close"].to_numpy()
    n=len(c);cost=pol["cost"];out=[];ready=-1
    for e in E:
        i=e["i"]
        if i<ready or i+1>=n: continue
        side=e["side"]
        if pol.get("side") and side!=pol["side"]: continue
        entry=float(o[i+1]); buf=pol["init"]*e["atr"]
        init=(l[i]-buf) if side==1 else (h[i]+buf)
        risk=(entry-init) if side==1 else (init-entry)
        if risk<=0: continue
        slp=risk/entry; costr=cost/slp
        stop=init; ext=entry; px=None;jend=None;res=None
        trail_k=pol["trail"]
        for j in range(i+1,min(i+1+pol.get("hold",64),n)):
            hi=h[j];lo=l[j];cl=c[j]
            if side==1:
                ext=max(ext,hi); newstop=ext-trail_k*e["atr"]; stop=max(stop,newstop)
                if lo<=stop: px=stop;res="STOP";jend=j;break
            else:
                ext=min(ext,lo); newstop=ext+trail_k*e["atr"]; stop=min(stop,newstop)
                if hi>=stop: px=stop;res="STOP";jend=j;break
            if j-i>=pol.get("hold",64): px=cl;res="TIME";jend=j;break
        if px is None: jend=min(i+pol.get("hold",64),n-1);px=c[jend];res="END"
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
            vals.append(trail(E,b,pol))
        r=np.concatenate(vals) if vals else np.array([])
        d=ag(r);
        if len(r):
            d["avgWin"]=round(r[r>0].mean(),2);d["avgLoss"]=round(r[r<0].mean(),2);d["maxW"]=round(r.max(),1)
        return d
    print("=== 吊灯移动止损（无固定止盈，hold64根15m=16h）===")
    for cost in (0.0005,0.0012):
        for init in (0.8,1.2):
            for tr in (1.5,2.5,3.5):
                pol=dict(cost=cost,init=init,trail=tr,hold=64,side=0)
                print(f"cost{cost} init{init} trail{tr}: INS",run(*ins,pol)," OOS",run(*oos,pol))
    print("\n=== 分方向 (cost0.0005 init1.0 trail2.5) ===")
    pol=dict(cost=0.0005,init=1.0,trail=2.5,hold=64)
    for sd_,nm in [(1,"只多"),(-1,"只空"),(0,"双向")]:
        p=dict(pol,side=sd_); print(nm,"INS",run(*ins,p)," OOS",run(*oos,p))
