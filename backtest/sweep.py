#!/usr/bin/env python3
"""信号只生成一次，出场策略快速扫描；含随机入场对照。用法 python3 sweep.py"""
import os, sys, importlib.util, json
import numpy as np, pandas as pd
BT = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(BT)
sys.path.insert(0, BT); sys.path.insert(0, ROOT)
import fast_backtest as fb

FEE, SLIP = 0.0005, 0.0005
ROUND_COST = 2*(FEE+SLIP); BE_BUFFER = 2*(FEE+SLIP)

def collect_entries(mod_path, mod_name, sym, df, rs, start_ms, end_ms):
    mod = fb.load_module(mod_path, mod_name)
    eng = fb.Engine(mod)
    t = df["open_ms"].to_numpy(float); n=len(df)
    i0 = max(400, int(np.searchsorted(t, start_ms, side="left")))
    i1 = min(n-2, int(np.searchsorted(t, end_ms, side="right")))
    o=df["open"].to_numpy(float);h=df["high"].to_numpy(float);l=df["low"].to_numpy(float);c=df["close"].to_numpy(float)
    pc=np.roll(c,1);pc[0]=c[0]
    tr=np.maximum.reduce([h-l,np.abs(h-pc),np.abs(l-pc)])
    atr=pd.Series(tr).rolling(14).mean().to_numpy()
    entries=[]
    for i in range(i0,i1):
        res,_=eng.decide(sym,df,rs,i)
        sig=res.get("signal")
        if sig in ("LONG","SHORT"):
            sl=float(res.get("sl") or 0); tp=float(res.get("tp") or 0)
            if sl>0 and tp>0:
                eq=(res.get("fast_strategy") or {}).get("entry_quality") or {}
                entries.append(dict(i=i, side=1 if sig=="LONG" else -1,
                                    ssl=sl, stp=tp, score=float(res.get("confidence") or 0),
                                    path=eq.get("path",""), tier=res.get("signal_tier",""),
                                    atr=float(atr[i]) if np.isfinite(atr[i]) else np.nan,
                                    px=float(c[i])))
    return entries, df

def simulate(entries, df, policy, seed_random=None, sides=None):
    """policy: dict mode=signal/atr, k, rr, floor, be(bool), stagnate(bool), reversal_engine=None,
       static(bool), cooldown bars."""
    o=df["open"].to_numpy(float);h=df["high"].to_numpy(float);l=df["low"].to_numpy(float);c=df["close"].to_numpy(float)
    n=len(df)
    rng = np.random.default_rng(seed_random) if seed_random is not None else None
    out=[]; ready=-1; ei=0
    # for random baseline generate random entries across the same index span
    if rng is not None:
        idxs = rng.integers(entries[0]["i"], entries[-1][i1key(entries)], size=len(entries)) if False else None
    while ei < len(entries):
        e=entries[ei]; ei+=1
        i=e["i"]
        if i < ready or i+1>=n: continue
        side=e["side"] if sides is None else sides[ei-1]
        entry=float(o[i+1]); px=entry
        if policy.get("mode")=="atr":
            slp=max((e["atr"]/px)*policy["k"], policy.get("floor",0.0)); tpp=slp*policy["rr"]
        else:
            slp=e["ssl"]; tpp=e["stp"]
        if not np.isfinite(slp) or slp<=0: continue
        risk=entry*slp; costr=ROUND_COST/slp
        stop=entry*(1-slp) if side==1 else entry*(1+slp)
        tp=entry*(1+tpp) if side==1 else entry*(1-tpp)
        be=entry*(1+BE_BUFFER) if side==1 else entry*(1-BE_BUFFER)
        armed=False; maxr=0; exitr=None; exitx=None; exitj=None; revbars=[]
        for j in range(i+1,min(i+1+fb.MAX_HOLD_BARS,n)):
            hi=h[j];lo=l[j];cl=c[j]; bars=j-i
            if side==1 and lo<=stop:
                exitr=("TP" if stop>=tp*0.999999 else ("BE" if armed else "SL"));exitx=stop;exitj=j;break
            if side==-1 and hi>=stop:
                exitr=("TP" if stop<=tp*1.000001 else ("BE" if armed else "SL"));exitx=stop;exitj=j;break
            if side==1 and hi>=tp: exitr="TP";exitx=tp;exitj=j;break
            if side==-1 and lo<=tp: exitr="TP";exitx=tp;exitj=j;break
            if side==1:
                maxr=max(maxr,(hi-entry)/risk)
                if policy.get("be",True) and not armed and hi>=entry+risk: armed=True;stop=be
            else:
                maxr=max(maxr,(entry-lo)/risk)
                if policy.get("be",True) and not armed and lo<=entry-risk: armed=True;stop=be
            if policy.get("static"):
                if bars>=fb.MAX_HOLD_BARS: exitr="TIMEOUT";exitx=cl;exitj=j
                if exitr: break
                continue
            if policy.get("stagnate",True) and bars>=fb.STAGNATE_BARS and maxr<fb.STAGNATE_MIN_R:
                exitr="STAGNATION";exitx=cl;exitj=j;break
            # 反转平仓在 sweep 中关闭（需要模块），默认依赖 TP/SL/BE/横盘/超时
            if bars>=fb.MAX_HOLD_BARS:
                exitr="TIMEOUT";exitx=cl;exitj=j;break
        if exitx is None:
            exitj=min(i+fb.MAX_HOLD_BARS,n-1);exitx=c[exitj];exitr="OPEN_END"
        gr=side*(exitx-entry)/risk
        out.append(dict(path=e["path"],tier=e["tier"],reason=exitr,bars=exitj-i,
                        gr=gr,net=gr-costr,slp=slp,tpp=tpp))
        ready=exitj+1+policy.get("cooldown",fb.COOLDOWN_BARS)
    return pd.DataFrame(out)

def i1key(entries): return "i"

def summ(tr,label):
    if not len(tr): return {"label":label,"n":0}
    r=tr.net; w=(r>0).mean(); pf=r[r>0].sum()/max(-r[r<0].sum(),1e-9)
    return dict(label=label,n=len(tr),win=round(100*w,1),avgR=round(r.mean(),3),
                totR=round(r.sum(),1),pf=round(pf,2),
                tp=round(100*(tr.reason=="TP").mean(),1),be=round(100*(tr.reason=="BE").mean(),1),
                sl=round(100*(tr.reason=="SL").mean(),1),
                slp=round(100*tr.slp.median(),3),tpp=round(100*tr.tpp.median(),3))

if __name__=="__main__":
    syms=["BTCUSDT","ETHUSDT","SOLUSDT"]
    start=fb.ms("2026-03-01"); end=fb.ms("2026-06-30")
    v2=os.path.join(BT,"ref_alpha_fast_mode_v2.py")
    cache={}
    for sym in syms:
        df,rs=fb.load_symbol(sym)
        cache[sym]=collect_entries(v2,"v2_"+sym,sym,df,rs,start,end)
        print(sym,"entries",len(cache[sym][0]),flush=True)
    allE=[]; 
    for sym in syms: allE+=cache[sym][0]
    # df for simulation: use BTC df only for prices? Need per-symbol prices. Simulate per symbol then concat.
    def run_policy(pol, flip=False, random_n=False):
        frames=[]
        rng=np.random.default_rng(7)
        for sym in syms:
            E,df=cache[sym]
            sides=None
            if flip: sides=[-e["side"] for e in E]
            if random_n:
                # 随机方向对照
                sides=rng.choice([-1,1],size=len(E))
            frames.append(simulate(E,df,pol,sides=sides))
        return pd.concat(frames,ignore_index=True)
    pols=[
      ("v2信号原生TP/SL(管理)",dict(mode="signal",be=True,stagnate=True)),
      ("v2静态纯TP/SL",dict(mode="signal",static=True)),
      ("ATR k1.2 rr1.3 BE",dict(mode="atr",k=1.2,rr=1.3,floor=0.0035,be=True,stagnate=True)),
      ("ATR k1.5 rr1.3 BE",dict(mode="atr",k=1.5,rr=1.3,floor=0.0035,be=True,stagnate=True)),
      ("ATR k1.5 rr1.5 BE",dict(mode="atr",k=1.5,rr=1.5,floor=0.004,be=True,stagnate=True)),
      ("ATR k2.0 rr1.5 BE",dict(mode="atr",k=2.0,rr=1.5,floor=0.004,be=True,stagnate=True)),
      ("ATR k1.5 rr2.0 BE",dict(mode="atr",k=1.5,rr=2.0,floor=0.004,be=True,stagnate=True)),
      ("ATR k2.0 rr2.0 静态",dict(mode="atr",k=2.0,rr=2.0,floor=0.004,static=True)),
    ]
    print("\n=== 真实信号方向 ===")
    for name,pol in pols:
        print(json.dumps(summ(run_policy(pol),name),ensure_ascii=False))
    print("\n=== 随机方向对照（ATR k1.5 rr1.5） ===")
    pol=dict(mode="atr",k=1.5,rr=1.5,floor=0.004,be=True,stagnate=True)
    print(json.dumps(summ(run_policy(pol,random_n=True),"随机方向"),ensure_ascii=False))
    print(json.dumps(summ(run_policy(pol,flip=True),"翻转方向"),ensure_ascii=False))
