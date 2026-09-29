#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""24h截面动量：显著性/大盘beta/纯多头vs多空/环境依赖 定量定性。"""
import os, numpy as np, pandas as pd
HERE=os.path.dirname(os.path.abspath(__file__))
SMALL=["WIF","SUI","SEI","APT","INJ","TIA","OP","ARB","WLD","FET","RUNE","AAVE","CRV","ENS",
 "GALA","SAND","AXS","CHZ","LDO","ALT","JTO","PEPE","SHIB","FLOKI","MEME","BONK","RENDER","STX","IMX","ARKM","ORDI"]
def load(s,sub):
    d=pd.read_csv(os.path.join(HERE,sub,f"{s}USDT_5m.csv")); d["t"]=pd.to_datetime(d["open_ms"],unit="ms",utc=True); return d.set_index("t")
op={};cl={}
d=load("BTC","data"); btc_o=d["open"]; btc_c=d["close"]
for s in SMALL:
    d=load(s,"data_small"); op[s]=d["open"]; cl[s]=d["close"]
OP=pd.DataFrame(op).sort_index(); CL=pd.DataFrame(cl).reindex(OP.index); BO=btc_o.reindex(OP.index)
T=len(CL); CUT=pd.Timestamp("2026-07-01",tz="UTC")
W=rb=288; k=5
score=CL.pct_change(W); fwd=OP.shift(-(1+rb))/OP.shift(-1)-1
bfwd=BO.shift(-(1+rb))/BO.shift(-1)-1
idx=np.arange(W,T-rb-1,rb)
R={"ls":[],"long":[],"short":[],"btc":[]}
prev_w=np.zeros(len(CL.columns)); first=True
for i in idx:
    sc=score.iloc[i].values; f=fwd.iloc[i].values; ok=np.isfinite(sc)&np.isfinite(f)
    ii=np.where(ok)[0]; srt=ii[np.argsort(sc[ii])]
    win=srt[-k:]; los=srt[:k]
    w=np.zeros(len(CL.columns)); w[win]=1/k; w[los]=-1/k
    turn=np.abs(w-prev_w).sum(); cost=0.0012*(1.0 if first else turn/2.0); first=False; prev_w=w
    R["ls"].append((CL.index[i],(w*f).sum()-cost))
    R["long"].append((CL.index[i],f[win].mean()-cost/2))
    R["short"].append((CL.index[i],-f[los].mean()-cost/2))
    R["btc"].append((CL.index[i],bfwd.iloc[i]))
D={k_:pd.Series({t:v for t,v in v_}).sort_index() for k_,v_ in R.items()}
def desc(name,s):
    for seg,mask in (("INS",D["ls"].index<CUT),("OOS",D["ls"].index>=CUT)):
        x=s[mask].values; x=x[np.isfinite(x)]; n=len(x); yrs=n/365.0
        win=(x>0).mean(); pf=x[x>0].sum()/max(1e-12,-x[x<0].sum())
        sh=x.mean()/x.std()*np.sqrt(365); tstat=sh*np.sqrt(yrs)
        print(f"  {name:6s} {seg}: n={n:3d} 胜率{100*win:4.0f}% PF{pf:5.2f} 夏普{sh:5.2f} t≈{tstat:4.1f} 日均{1e4*x.mean():+5.1f}bp 累计{x.sum()*100:+6.1f}% 最大回撤{((np.maximum.accumulate(np.cumsum(x))-np.cumsum(x)).max())*100:5.1f}%")
print("="*95)
for nm in ("long","ls","short","btc"):
    tag={"long":"纯多头 top5(只做多最强5)","ls":"多空对冲 top5-bottom5","short":"纯空头 bottom5","btc":"BTC 买入持有(同期)"}[nm]
    print(f"【{tag}】 taker0.12%")
    desc(nm,D[nm])
    print()
# beta
df=pd.DataFrame({"ls":D["ls"],"long":D["long"],"btc":D["btc"]}).dropna()
for nm in ("ls","long"):
    for seg,m in (("INS",df.index<CUT),("OOS",df.index>=CUT)):
        z=df[m]; b=np.cov(z[nm],z["btc"])[0,1]/np.var(z["btc"])
        print(f"beta[{nm}|{seg}] 对BTC日收益 = {b:+.2f}")
print("\nBTC 区间收益: INS %.1f%%  OOS %.1f%%" % (100*(df[df.index<CUT]['btc'].sum()), 100*(df[df.index>=CUT]['btc'].sum())))
