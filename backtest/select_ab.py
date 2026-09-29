#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""选币 A/B：旧趋势排名 vs v4区间回归排名，用 v4 真实成交的前向(6h)净R 判定优劣。"""
import os, sys, importlib.util
import numpy as np, pandas as pd
BT = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(BT); sys.path.insert(0, BT)
import fast_backtest as fb

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
A = load("afm", os.path.join(ROOT, "alpha_fast_mode.py"))
U = load("afu", os.path.join(ROOT, "alpha_fast_universe.py"))

SYMS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","LINKUSDT"]
COST = 0.0006
# 静态流动性权重（离线无盘口，按主流币常见成交额档位，仅作并列排序，权重很小）
LIQ = {"BTCUSDT":1.0,"ETHUSDT":0.95,"SOLUSDT":0.8,"XRPUSDT":0.7,"DOGEUSDT":0.65,"LINKUSDT":0.6}

# ---- v4 真实成交，净R@0.06% maker，按6h窗口归集 ----
tr = pd.read_csv(os.path.join(BT,"v4_trades.csv")); tr = tr[tr["mode"]=="v4_partial"].copy()
tr["pc"]=tr.part_closed.fillna(0.0)
tr["net"]=tr.gross_r - COST/tr.sl_pct - tr.pc*(COST/2)/tr.sl_pct
tr["ms"]=pd.to_datetime(tr.entry_time).astype("int64")//10**6

data={}; eng=fb.Engine(A)
t0=None; t1=None
for s in SYMS:
    df,rs=fb.load_symbol(s); data[s]=(df,rs)
    t0=df.open_ms.iloc[400] if t0 is None else min(t0,df.open_ms.iloc[400])
    t1=df.open_ms.iloc[-1] if t1 is None else max(t1,df.open_ms.iloc[-1])
STEP=6*3600*1000
blocks=list(range(int(t0),int(t1),STEP))

def old_score(fr):
    ctx=A._directional_context(fr["4h"],fr["1h"],fr["15m"]); d=int(ctx.get("direction",0)); ag=float(ctx.get("agreement",0))
    cfg=U.DEFAULT_UNIVERSE
    if d==0 or ag<cfg["min_agreement"]: return None
    s4=ctx.get("4h") or {}
    if int(s4.get("bias",0) or 0) and int(s4.get("bias",0))!=d and float(s4.get("strength",0) or 0)>=cfg["fourh_reverse_strength"]: return None
    f15=fr["15m"]; c=f15["close"];h=f15["high"];l=f15["low"]
    atr_pct=float(np.median(np.maximum(h[-20:]-l[-20:],1e-12))/max(float(c[-1]),1e-12))
    if atr_pct<cfg["vol_low"] or atr_pct>cfg["vol_high"]: return None
    hs,ls=A._swings(f15); swing_n=len(hs)+len(ls)
    if swing_n<cfg["min_swing"]: return None
    loc=A._price_location(f15); pos=float(loc.get("position",0.5))
    if d>0 and pos>0.68: return None
    if d<0 and pos<0.32: return None
    ext=U._extension_atr(f15); lim=cfg["extension_atr"]
    if (d>0 and ext>lim) or (d<0 and ext<-lim): return None
    trend=ag
    if int(s4.get("bias",0) or 0)==d: trend=min(1,ag+0.05)
    elif int(s4.get("bias",0) or 0): trend=max(0,ag-0.10)
    pull=U._bell(pos,0.42,0.30) if d>0 else U._bell(pos,0.58,0.30)
    fresh=U._bell(ext,0.30,lim) if d>0 else U._bell(ext,-0.30,lim)
    s15=ctx.get("15m") or {}; b=int(s15.get("bias",0) or 0); st=float(s15.get("strength",0) or 0)
    if (d>0 and b>0) or (d<0 and b<0): align=0.5+0.5*st
    elif b==0: align=0.40
    else: align=0.15
    struct=0.6*min(swing_n/10,1)+0.4*align
    vf=U._bell(atr_pct,cfg["vol_sweet"],(cfg["vol_high"]-cfg["vol_low"])/2)
    return float(np.clip(0.26*trend+0.28*pull+0.24*fresh+0.14*struct+0.08*vf,0,1))

def new_score(fr,sym):
    reg=A._v4_regime(fr); r=int(reg["regime"])
    f15=fr["15m"]; c=f15["close"];h=f15["high"];l=f15["low"]
    atr_pct=float(np.median(np.maximum(h[-20:]-l[-20:],1e-12))/max(float(c[-1]),1e-12))
    if atr_pct<0.003 or atr_pct>0.05: return None
    range_fit={0:1.0,9:0.45,1:0.0,-1:0.0}[r]
    vf=U._bell(atr_pct,0.009,0.007)
    f5=fr["5m"]; bb=A._bollinger(f5,20,2.0); rsi=float(A._rsi_arr(f5["close"],2)[-1]); px=float(f5["close"][-1])
    stretch=min(abs(px-bb["mid"])/max(2.0*bb["sd"],1e-12),1.3)/1.3 if bb["ok"] else 0.0
    rsi_ext=abs(rsi-50)/50.0
    opp=0.6*stretch+0.4*rsi_ext
    liq=LIQ.get(sym,0.5)
    return float(np.clip(0.45*range_fit+0.25*vf+0.30*opp+0.00*liq,0,1)), liq

rows=[]
K=3
for bi,T in enumerate(blocks):
    Tn=T+STEP
    oldc={}; newc={}
    for s in SYMS:
        df,rs=data[s]; i=int(np.searchsorted(df.open_ms.to_numpy(),T,side="left"))
        if i<400 or i>=len(df)-2: continue
        try: fr=eng.frames_at(df,rs,i)
        except Exception: continue
        try:
            os_=old_score(fr)
            if os_ is not None: oldc[s]=os_
        except Exception: pass
        try:
            ns=new_score(fr,s)
            if ns is not None: newc[s]=ns[0]+0.001*ns[1]
        except Exception: pass
    win=tr[(tr.ms>=T)&(tr.ms<Tn)]
    def fwd(coins):
        g=win[win.symbol.isin(coins)]; return len(g), g.net.sum(), (g.net>0).mean() if len(g) else np.nan
    old_pick=[s for s,_ in sorted(oldc.items(),key=lambda x:-x[1])[:K]]
    new_pick=[s for s,_ in sorted(newc.items(),key=lambda x:-x[1])[:K]]
    no,ro,wo=fwd(old_pick); nn,rn,wn=fwd(new_pick); na,ra,wa=fwd(SYMS)
    rows.append(dict(blk=T,old_n=no,old_R=ro,new_n=nn,new_R=rn,all_n=na,all_R=ra,
                     old_pick=";".join(old_pick),new_pick=";".join(new_pick)))

r=pd.DataFrame(rows)
def summ(col_n,col_R,name):
    n=int(r[col_n].sum()); R=r[col_R].sum();
    # 胜率按被选到的成交加权
    print(f"{name:10s}: 选中成交 {n:4d}笔  总净R={R:+7.1f}  每窗口均R={r[col_R].mean():+.3f}  正R窗口占比={100*(r[col_R]>0).mean():.1f}%")
print(f"窗口数={len(r)}  每窗口选K={K}  成本=0.06% maker\n")
summ("old_n","old_R","旧趋势选币")
summ("new_n","new_R","新区间选币")
summ("all_n","all_R","全部6币基准")
print("\n新区间选币 相对 旧趋势选币：总R差 %+.1f，每窗口均R差 %+.3f"%(r.new_R.sum()-r.old_R.sum(), r.new_R.mean()-r.old_R.mean()))
# 稳定性：INS/OOS 分开
r["per"]=np.where(r.blk<fb.ms("2026-07-01"),"INS","OOS")
print("\n分周期 总净R（旧 / 新 / 全）:")
print(r.groupby("per")[["old_R","new_R","all_R"]].sum().round(1).to_string())
r.to_csv(os.path.join(BT,"select_ab.csv"),index=False)
