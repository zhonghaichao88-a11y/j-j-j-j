#!/usr/bin/env python3
"""通用周期(5m/15m) 裸K原型：顺势扫损(趋势) 与 扫损回归VWAP(震荡)，结构止损。"""
import os,sys
import numpy as np,pandas as pd
BT=os.path.dirname(os.path.abspath(__file__));sys.path.insert(0,BT)
import fast_backtest as fb

def ema(s,n): return s.ewm(span=n,adjust=False).mean()

def prep(base, htf_list, vwap_bars):
    o=base["open"];h=base["high"];l=base["low"];c=base["close"];v=base["vol"];t=base["open_ms"]
    pc=c.shift(1);tr=pd.concat([(h-l),(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1)
    atr=tr.rolling(14).mean()
    typ=(h+l+c)/3; vwap=(typ*v).rolling(vwap_bars).sum()/v.rolling(vwap_bars).sum()
    # ER on base(14)
    er=(abs(c-c.shift(14))/c.diff().abs().rolling(14).sum()).to_numpy()
    # HTF 趋势：用更高周期 ema50/21
    trends=[]
    for r,mins in htf_list:
        rr=r.copy(); rr["ema50"]=ema(rr["close"],50); rr["ema21"]=ema(rr["close"],21)
        z=pd.merge_asof(pd.DataFrame({"cc":(t+ (t.diff().fillna(300000)).astype('int64'))}).sort_values("cc"),
                        pd.DataFrame({"ct":rr["ts_ms"]+mins*60000,"c":rr["close"].to_numpy(),
                                      "e50":rr["ema50"].to_numpy(),"e21":rr["ema21"].to_numpy()}).sort_values("ct"),
                        left_on="cc",right_on="ct",direction="backward")
        up=((z["c"]>z["e50"])&(z["e21"]>z["e50"])).to_numpy()
        dn=((z["c"]<z["e50"])&(z["e21"]<z["e50"])).to_numpy()
        trends.append((up,dn))
    htf_up=np.all(np.column_stack([a for a,_ in trends]),axis=1) if trends else np.ones(len(c),bool)
    htf_dn=np.all(np.column_stack([b for _,b in trends]),axis=1) if trends else np.ones(len(c),bool)
    return dict(o=o.to_numpy(),h=h.to_numpy(),l=l.to_numpy(),c=c.to_numpy(),t=t.to_numpy(),
                atr=atr.to_numpy(),vwap=vwap.to_numpy(),er=er,up=htf_up,dn=htf_dn)

def gen(I, kind, SW=12, er_trend=0.30):
    h=I["h"];l=I["l"];c=I["c"];atr=I["atr"];vw=I["vwap"];up=I["up"];dn=I["dn"];er=I["er"]
    rng=np.maximum(h-l,1e-12); n=len(c); E=[]
    for i in range(140,n-2):
        if not np.isfinite(atr[i]) or not np.isfinite(vw[i]): continue
        plo=np.min(l[i-SW:i]); phi=np.max(h[i-SW:i]); bp=(c[i]-l[i])/rng[i]
        if kind=="trend_sweep":
            if up[i] and l[i]<plo and c[i]>plo and bp>=0.55 and er[i]>=er_trend:
                E.append(mk(i,1,I,plo,phi,bp))
            if dn[i] and h[i]>phi and c[i]<phi and bp<=0.45 and er[i]>=er_trend:
                E.append(mk(i,-1,I,plo,phi,bp))
        elif kind=="rev_vwap":
            # 震荡：扫前低/前高后收回，目标VWAP；要求ER低(区间)、价格已偏离VWAP
            if l[i]<plo and c[i]>plo and bp>=0.6 and er[i]<0.30 and c[i]<vw[i]:
                E.append(mk(i,1,I,plo,phi,bp,target=vw[i]))
            if h[i]>phi and c[i]<phi and bp<=0.4 and er[i]<0.30 and c[i]>vw[i]:
                E.append(mk(i,-1,I,plo,phi,bp,target=vw[i]))
    return E

def mk(i,side,I,plo,phi,bp,target=None):
    atr=float(I["atr"][i])
    stop=(I["l"][i]-0.25*atr) if side==1 else (I["h"][i]+0.25*atr)
    liq=target if target is not None else (np.max(I["h"][i-24:i]) if side==1 else np.min(I["l"][i-24:i]))
    return dict(i=i,side=side,atr=atr,stop=float(stop),liq=float(liq),
                er=float(I["er"][i]),bp=float(bp))

def sim(E, o,h,l,c, pol):
    n=len(c); cost=pol["cost"]; out=[];ready=-1
    for e in E:
        i=e["i"]
        if i<ready or i+1>=n: continue
        side=e["side"];entry=float(o[i+1])
        risk=(entry-e["stop"]) if side==1 else (e["stop"]-entry)
        if risk<=0: continue
        slp=risk/entry
        rr_struct=side*(e["liq"]-entry)/risk
        if pol.get("need_struct_rr",True) and rr_struct<pol["rr_min"]: continue
        rr=min(max(rr_struct,pol["rr_min"]),pol["rr_max"]) if rr_struct>0 else pol["rr_max"]
        tpp=slp*rr;costr=cost/slp
        stop=entry*(1-slp) if side>0 else entry*(1+slp)
        tp=entry*(1+tpp) if side>0 else entry*(1-tpp)
        be=entry*(1+cost*0.5) if side>0 else entry*(1-cost*0.5)
        armed=False;px=None;res=None;jend=None
        for j in range(i+1,min(i+1+pol.get("hold",32),n)):
            hi=h[j];lo=l[j];cl=c[j];bars=j-i
            if side>0 and lo<=stop: px=stop;res=("TP" if (not armed and stop>=tp*0.999999) else ("BE" if armed else "SL"));jend=j;break
            if side<0 and hi>=stop: px=stop;res=("TP" if (not armed and stop<=tp*1.000001) else ("BE" if armed else "SL"));jend=j;break
            if side>0 and hi>=tp: px=tp;res="TP";jend=j;break
            if side<0 and lo<=tp: px=tp;res="TP";jend=j;break
            fav=side*((hi-entry) if side>0 else (entry-lo))/risk
            if not armed and fav>=1.0: armed=True;stop=be
            if bars>=pol.get("hold",32): px=cl;res="TIME";jend=j;break
        if px is None: jend=min(i+pol.get("hold",32),n-1);px=c[jend];res="END"
        out.append(side*(px-entry)/risk-costr);ready=jend+1+3
    return np.array(out) if out else np.array([])

def ag(r,lab=""):
    if not len(r): return dict(n=0)
    return dict(n=len(r),win=round(100*(r>0).mean(),1),avgR=round(r.mean(),3),totR=round(r.sum(),0),
                pf=round(r[r>0].sum()/max(-r[r<0].sum(),1e-9),2))

def build(tf):
    if tf=="5m":
        symdata={}
        for s in SYMS:
            df,rs=fb.load_symbol(s)
            I=prep(df,[(rs["1h"],60),(rs["4h"],240)],96)
            symdata[s]=(df,I)
        return symdata
    else:
        symdata={}
        for s in SYMS:
            df5,rs=fb.load_symbol(s)
            b=rs["15m"].reset_index(drop=True).rename(columns={"ts_ms":"open_ms"})
            I=prep(b,[(rs["1h"],60),(rs["4h"],240)],48)
            symdata[s]=(b,I)
        return symdata

def run(symdata,kind,lo,hi,pol):
    vals=[]
    for s in SYMS:
        base,I=symdata[s]
        E=[e for e in gen(I,kind) if lo<=base.open_ms.iloc[e["i"]]<=hi]
        r=sim(E,base["open"].to_numpy(),base["high"].to_numpy(),base["low"].to_numpy(),base["close"].to_numpy(),pol)
        vals.append(r)
    return ag(np.concatenate(vals) if vals else np.array([]))

SYMS=["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","LINKUSDT"]
if __name__=="__main__":
    ins=(fb.ms("2026-03-01"),fb.ms("2026-06-30"));oos=(fb.ms("2026-07-01"),fb.ms("2026-09-15"))
    # 制度
    df,_=fb.load_symbol("BTCUSDT")
    for nm,(a,b) in [("INS",ins),("OOS",oos)]:
        seg=df[(df.open_ms>=a)&(df.open_ms<=b)]
        print(f"BTC {nm} 涨跌 {100*(seg.close.iloc[-1]/seg.close.iloc[0]-1):.1f}%")
    for tf in ("5m","15m"):
        print("\n############ 周期",tf)
        sd=build(tf)
        hold=48 if tf=="5m" else 24
        print("--- 顺势扫损 trend_sweep ---")
        for cost in (0.0008,0.0012):
            for rrm in (1.3,1.8,2.5):
                pol=dict(cost=cost,rr_min=1.0,rr_max=rrm,hold=hold,need_struct_rr=True)
                print(f"cost{cost} rr<={rrm}: INS",run(sd,"trend_sweep",*ins,pol)," OOS",run(sd,"trend_sweep",*oos,pol))
        print("--- 震荡扫损回归VWAP rev_vwap ---")
        for cost in (0.0008,0.0012):
            for (rlo,rhi) in [(0.4,0.8),(0.5,1.0),(0.6,1.2)]:
                pol=dict(cost=cost,rr_min=rlo,rr_max=rhi,hold=hold,need_struct_rr=True)
                print(f"cost{cost} rr{rlo}-{rhi}: INS",run(sd,"rev_vwap",*ins,pol)," OOS",run(sd,"rev_vwap",*oos,pol))
