#!/usr/bin/env python3
"""结构化止损止盈 + 共振子集实验（顺势扫损 playbook）。"""
import os,sys
import numpy as np,pandas as pd
BT=os.path.dirname(os.path.abspath(__file__));sys.path.insert(0,BT)
import fast_backtest as fb
from arch_edge import indicators

SW=12
def signals(df,rs):
    I=indicators(df,rs); n=len(I["c"]); E=[]
    h=I["h"];l=I["l"];c=I["c"];o=I["o"];atr=I["atr"];vw=I["vwap"]
    hu=I["htf_up"];hd=I["htf_dn"];er=I["er15"]
    rng=np.maximum(h-l,1e-12)
    for i in range(140,n-2):
        if not np.isfinite(atr[i]) or not np.isfinite(vw[i]): continue
        plo=np.min(l[i-SW:i]); phi=np.max(h[i-SW:i])
        bp=(c[i]-l[i])/rng[i]
        # 15m 趋势对齐（收盘在vwap同侧由htf表达，再加ER）
        for side in (1,-1):
            if side==1:
                sweep = hu[i] and l[i]<plo and c[i]>plo and bp>=0.55
            else:
                sweep = hd[i] and h[i]>phi and c[i]<phi and bp<=0.45
            if not sweep: continue
            if er[i] < 0.30: continue
            buf=0.25*atr[i]
            stop = (l[i]-buf) if side==1 else (h[i]+buf)
            tgt_liq = np.max(h[i-24:i]) if side==1 else np.min(l[i-24:i])
            E.append(dict(i=int(i),side=side,atr=float(atr[i]),stop=float(stop),liq=float(tgt_liq),
                          er=float(er[i]), discount=bool(c[i]<vw[i]) if side==1 else bool(c[i]>vw[i]),
                          hour=pd.to_datetime(df.open_ms.iloc[i],unit="ms").hour,
                          depth=abs((c[i]-plo))/atr[i] if side==1 else abs((phi-c[i]))/atr[i],
                          bp=float(bp)))
    return E

def sim(E,df,pol):
    o=df["open"].to_numpy();h=df["high"].to_numpy();l=df["low"].to_numpy();c=df["close"].to_numpy()
    n=len(c);cost=pol["cost"];out=[];ready=-1
    for e in E:
        i=e["i"]
        if i<ready or i+1>=n: continue
        side=e["side"];entry=float(o[i+1])
        if side==1:
            risk=entry-e["stop"]; liq=e["liq"]
        else:
            risk=e["stop"]-entry; liq=e["liq"]
        if risk<=0: continue
        slp=risk/entry
        # 结构目标盈亏比；按 rr_min/rr_max 裁剪
        rr_struct=side*(liq-entry)/risk
        rr=min(max(rr_struct,pol["rr_min"]),pol["rr_max"]) if rr_struct>0 else pol["rr_max"]
        if rr_struct<pol["rr_min"] and pol.get("need_struct_rr",True): continue
        tpp=slp*rr; costr=cost/slp
        stop=entry*(1-slp) if side>0 else entry*(1+slp)
        tp=entry*(1+tpp) if side>0 else entry*(1-tpp)
        be=entry*(1+cost*0.5) if side>0 else entry*(1-cost*0.5)
        armed=False;res=None;px=None;jend=None;maxr=0
        for j in range(i+1,min(i+1+pol.get("hold",48),n)):
            hi=h[j];lo=l[j];cl=c[j];bars=j-i
            if side>0 and lo<=stop: px=stop;res=("TP" if (not armed and stop>=tp*0.999999) else ("BE" if armed else "SL"));jend=j;break
            if side<0 and hi>=stop: px=stop;res=("TP" if (not armed and stop<=tp*1.000001) else ("BE" if armed else "SL"));jend=j;break
            if side>0 and hi>=tp: px=tp;res="TP";jend=j;break
            if side<0 and lo<=tp: px=tp;res="TP";jend=j;break
            fav=side*((hi-entry) if side>0 else (entry-lo))/risk; maxr=max(maxr,fav)
            if not armed and fav>=1.0: armed=True;stop=be
            if bars>=pol.get("hold",48): px=cl;res="TIME";jend=j;break
        if px is None: jend=min(i+pol.get("hold",48),n-1);px=c[jend];res="END"
        gr=side*(px-entry)/risk
        out.append((gr-costr,e,res));ready=jend+1+3
    return out

def agg(out,label):
    if not out: return dict(label=label,n=0)
    r=np.array([x[0] for x in out]);win=(r>0).mean();pf=r[r>0].sum()/max(-r[r<0].sum(),1e-9)
    return dict(label=label,n=len(r),win=round(100*win,1),avgR=round(r.mean(),3),totR=round(r.sum(),0),pf=round(pf,2))

if __name__=="__main__":
    syms=["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","LINKUSDT"]
    ins=(fb.ms("2026-03-01"),fb.ms("2026-06-30"));oos=(fb.ms("2026-07-01"),fb.ms("2026-09-15"))
    C={}
    for s in syms:
        df,rs=fb.load_symbol(s); E=signals(df,rs); C[s]=(df,E); print(s,len(E),flush=True)
    def run(lo,hi,pol,sub=lambda e:True):
        vals=[]
        for s in syms:
            df,E=C[s]; EE=[e for e in E if lo<=df.open_ms.iloc[e["i"]]<=hi and sub(e)]
            vals+=sim(EE,df,pol)
        return agg(vals,"")
    base=lambda cost,rrmin,rrmax: dict(cost=cost,rr_min=rrmin,rr_max=rrmax,hold=48,need_struct_rr=True)
    print("\n=== 结构止损+流动性目标（要求结构rr>=rr_min）===")
    for cost in (0.0006,0.001,0.0015):
        for rrm in (1.0,1.3,1.6):
            pol=base(cost,1.0,rrm)
            print(f"cost{cost} rr<= {rrm}: INS",run(*ins,pol),"OOS",run(*oos,pol))
    print("\n=== 共振子集（cost0.001, rr1.0~1.5）===")
    pol=base(0.001,1.0,1.5)
    subs={
        "all":lambda e:True,
        "discount回踩到位":lambda e:e["discount"],
        "er>=0.45":lambda e:e["er"]>=0.45,
        "discount&er0.45":lambda e:e["discount"] and e["er"]>=0.45,
        "深扫depth>=0.3":lambda e:e["depth"]>=0.3,
        "强收回bp>=0.7":lambda e:(e["bp"]>=0.7 if e["side"]==1 else e["bp"]<=0.3),
    }
    for nm,fn in subs.items():
        print(f"{nm:18s} INS",run(*ins,pol,fn),"OOS",run(*oos,pol,fn))
    print("\n=== 时段(UTC) edge，cost0.001 rr1.5 ===")
    for h0,h1 in [(0,8),(8,12),(12,16),(16,24)]:
        fn=lambda e,h0=h0,h1=h1:h0<=e["hour"]<h1
        print(f"{h0:02d}-{h1:02d} UTC INS",run(*ins,pol,fn),"OOS",run(*oos,pol,fn))
