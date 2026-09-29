#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v5-XS 横截面中性 · 严谨组合回测（直接驱动 alpha_xs_neutral 生产信号函数）。
防假处理：
 1) 全币 dropna 对齐(剔除上线初期/缺口行)；
 2) forward 两根 K线时间间隔必须=持有期，跨缺口当期作废；
 3) 同时给 原始 与 单日收益 winsorize±CAP 两套结果，区分核心edge与妖币极端彩票；
 4) 精确换手只对变化的腿收单边费；权益复利；资金费缺失按0(保守)。
"""
import os, math
import numpy as np, pandas as pd
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE)
import sys; sys.path.insert(0,ROOT)
import alpha_xs_neutral as XS
SMALL=["WIF","SUI","SEI","APT","INJ","TIA","OP","ARB","WLD","FET","RUNE","AAVE","CRV","ENS",
 "GALA","SAND","AXS","CHZ","LDO","ALT","JTO","PEPE","SHIB","FLOKI","MEME","BONK","RENDER","STX","IMX","ARKM","ORDI"]
CUT=pd.Timestamp("2026-07-01",tz="UTC")

def load(s):
    d=pd.read_csv(os.path.join(HERE,"data_small",f"{s}USDT_5m.csv"))
    d["t"]=pd.to_datetime(d["open_ms"],unit="ms",utc=True); return d.set_index("t")

def prep(syms):
    op={};cl={}
    for s in syms:
        d=load(s); op[s]=d["open"]; cl[s]=d["close"]
    OP=pd.DataFrame(op).sort_index().dropna(how="any")
    CL=pd.DataFrame(cl).reindex(OP.index).dropna(how="any"); OP=OP.reindex(CL.index)
    return OP,CL

def backtest(k=5,look_h=24,hold_h=24,gross=2.0,fee=0.0006,cap=None,longs_only=False,label=""):
    L=look_h*12; rb=hold_h*12
    OP,CL=prep(SMALL); T=len(CL); cols=CL.columns
    step=pd.Timedelta(minutes=5*rb)
    equity=1.0; prev=pd.Series(0.0,index=cols); rows=[]; legL=[]; legS=[]
    i=L
    while i < T-rb-1:
        t=CL.index[i]
        if OP.index[i+1+rb]-OP.index[i+1] != step:   # 跨缺口保护
            i+=rb; continue
        sc=XS.momentum_score(CL.iloc[:i+1],L); b=XS.select_basket(sc,k=k)
        if longs_only: b["shorts"]=[]
        if b.get("reason")!="ok": i+=rb; continue
        tw=XS.target_weights(b,gross=gross)
        r=(OP.iloc[i+1+rb]/OP.iloc[i+1]-1).reindex(cols); r=r[np.isfinite(r)]
        if cap is not None: r=r.clip(-cap,cap)
        turn=float((tw-prev.reindex(cols).fillna(0)).abs().sum()); cost=fee*turn
        pnl=float((tw*r).sum())
        legL.append((t,float(tw[tw>0].mul(r.reindex(tw.index).fillna(0)).sum())))
        legS.append((t,float((-tw[tw<0]).mul(r.reindex(tw.index).fillna(0)).sum())))
        equity*=(1+pnl-cost); rows.append((t,pnl,cost,equity)); prev=tw; i+=rb
    R=pd.DataFrame(rows,columns=["t","ret","cost","eq"]).set_index("t")
    def met(seg):
        x=seg["ret"].values; n=len(x)
        win=(x>0).mean(); pf=x[x>0].sum()/max(1e-12,-x[x<0].sum())
        sh=x.mean()/x.std()*math.sqrt(365/(rb/288))
        dd=((seg["eq"].cummax()-seg["eq"])/seg["eq"].cummax()).max()
        return f"n={n} 胜率{100*win:.0f}% PF{pf:.2f} 夏普{sh:.2f} 复利{100*(seg['eq'].iloc[-1]-1):+.1f}% 最大回撤{100*dd:.1f}%"
    print(f"\n### {label}")
    print("  INS:",met(R[R.index<CUT])); print("  OOS:",met(R[R.index>=CUT]))
    m=R["ret"].resample("MS").sum()
    print("  分月单利%:",{d.strftime('%m'):round(100*v,1) for d,v in m.items()})
    for nm,sg in (("多头腿",legL),("空头腿",legS)):
        z=pd.Series(dict(sg)); z=z[z.index>=CUT].values
        if len(z): print(f"  OOS {nm}单利: {100*z.sum():+.1f}% 胜{(z>0).mean()*100:.0f}%")
    return R

if __name__=="__main__":
    print("="*92); print("【原始】多空 k5 gross2 taker(单边0.06%)，含妖币极端行情")
    backtest(cap=None,label="多空 k5 taker · 原始(含妖币)")
    print("\n"+"="*92); print("【可持续口径】单日收益 winsorize ±40%（剔除不可复制妖币彩票）")
    backtest(cap=0.40,label="多空 k5 taker · 截尾±40%")
    backtest(cap=0.40,fee=0.0003,label="多空 k5 maker · 截尾±40%")
    print("\n"+"="*92); print("【参数高原·截尾±40% taker】")
    for kk in (3,5,8): backtest(k=kk,cap=0.40,label=f"多空 k{kk} 截尾")
    backtest(look_h=12,cap=0.40,label="看12h持24h k5 截尾")
    backtest(hold_h=12,cap=0.40,label="看24h持12h k5 截尾")
    print("\n"+"="*92); print("【对照】纯多头 k5 满仓 截尾±40%")
    backtest(gross=2.0,cap=0.40,longs_only=True,label="纯多头 k5 截尾")
    print("\n完成。可持续 edge 以『截尾±40%、OOS PF>1/夏普>0、相邻参数为正』为准。")
