#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""24h 横截面动量(市场中性) 稳健性检验：防止参数孤岛/过拟合。
固定先验常识参数(24h形成/24h持有/多空各5)，再检验相邻参数高原、成本敏感、分月一致性、
去meme、风险调整动量、多空腿贡献。信号 t 收盘, t+1 开盘执行, 无未来函数。"""
import os, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
SMALL = ["WIF","SUI","SEI","APT","INJ","TIA","OP","ARB","WLD","FET","RUNE","AAVE","CRV",
         "ENS","GALA","SAND","AXS","CHZ","LDO","ALT","JTO","PEPE","SHIB","FLOKI","MEME",
         "BONK","RENDER","STX","IMX","ARKM","ORDI"]
MEME = {"WIF","PEPE","SHIB","FLOKI","MEME","BONK"}

def load(sym, sub):
    d = pd.read_csv(os.path.join(HERE, sub, f"{sym}USDT_5m.csv"))
    d["t"] = pd.to_datetime(d["open_ms"], unit="ms", utc=True); return d.set_index("t")
op={}; cl={}
for s in SMALL:
    d=load(s,"data_small"); op[s]=d["open"]; cl[s]=d["close"]
OP=pd.DataFrame(op).sort_index(); CL=OP.to_frame() if False else pd.DataFrame(cl).reindex(OP.index)
T=len(CL); SYMS=list(CL.columns)
CUT=pd.Timestamp("2026-07-01",tz="UTC")

def run(W=288, rb=288, k=5, c=0.0012, syms=None, riskadj=False, monthly=False, legs=False):
    X=CL[syms or SYMS]; O=OP[X.columns]
    if riskadj:
        ret=X.pct_change(W); vol=ret.rolling(W,min_periods=W//2).std()
        score=ret/(vol+1e-9)
    else:
        score=X.pct_change(W)
    fwd=O.shift(-(1+rb))/O.shift(-1)-1
    idx=np.arange(W, T-rb-1, rb)
    rows=[]; legL=[]; legS=[]; prev_w=None; tc=0.0
    wsum=0.0
    for i in idx:
        sc=score.iloc[i].values; f=fwd.iloc[i].values
        ok=np.isfinite(sc)&np.isfinite(f)
        if ok.sum()<2*k+5: continue
        ii=np.where(ok)[0]; srt=ii[np.argsort(sc[ii])]
        win=srt[-k:]; los=srt[:k]
        w=np.zeros(len(X.columns)); w[win]=1.0/k; w[los]=-1.0/k
        r=(w*f).sum()
        # 精确换手成本: |w-prev| 之和为双边变动名义, 乘以单边费率c/2(开平合计=c)保守取 c*单边变动
        if prev_w is not None:
            turn=np.abs(w-prev_w).sum()        # 双边变动(新开+平掉)
            cost=c*turn/2.0                     # 每边往返按 c/2 计(保守, 近似一次开或平)
        else:
            cost=c*1.0                          # 建仓 gross=2 -> 按一次往返 c 近似
            turn=2.0
        tc+=cost; wsum+=1
        rows.append((X.index[i], r-cost)); prev_w=w
        legL.append(f[win].mean()-cost/2); legS.append(-f[los].mean()-cost/2)
    rr=pd.DataFrame(rows,columns=["t","ls"]).set_index("t")["ls"]
    ins=rr[rr.index<CUT]; oos=rr[rr.index>=CUT]
    def st(x):
        x=x.values; win=(x>0).mean(); pf=x[x>0].sum()/max(1e-12,-x[x<0].sum())
        pyr=(365*24*60/5)/rb; sh=x.mean()/(x.std()+1e-12)*np.sqrt(pyr)
        return f"n={len(x)} 胜率{100*win:.0f}% PF{pf:.2f} 夏普{sh:.2f} 均{1e4*x.mean():+.1f}bp/期 累计{x.sum()*100:+.1f}%"
    out=f"W={W//288*24 or W*5//60}h rb={rb*5//60}h k={k} c={c:.4f} {'风险调' if riskadj else '动量'} ncoin={len(X.columns)}"
    print(f"{out:62s} | INS {st(ins)} | OOS {st(oos)}")
    if monthly:
        m=rr.resample("MS").sum()
        print("    分月收益%:", {d.strftime('%m'):round(v*100,2) for d,v in m.items()})
    if legs:
        L=pd.Series(legL,index=rr.index); S=pd.Series(legS,index=rr.index)
        for nm,s in (("多头腿",L),("空头腿",S)):
            zz=s[s.index>=CUT].values
            print(f"    OOS {nm}: 累计{zz.sum()*100:+.1f}% 均{1e4*zz.mean():+.1f}bp 胜{(zz>0).mean()*100:.0f}%")
    return rr

print("="*100); print("【基准】24h/24h k=5（先验常识，非事后挑）")
run(monthly=True, legs=True)
print("\n【参数高原】相邻 formation / holding / k（看是否一片为正，而非单点尖峰）")
for W in (144,288,432):
    for k in (3,5,8): run(W=W, rb=288, k=k)
print("--- 持有期 12h ---")
for W in (144,288): run(W=W, rb=144, k=5)
print("\n【成本敏感】taker0.12 / 0.10 / maker0.06 / 近零0.04")
for c in (0.0012,0.0010,0.0006,0.0004): run(c=c)
print("\n【币集稳健】去掉6个meme妖币 / 风险调整动量排名")
run(syms=[s for s in SYMS if s not in MEME])
run(riskadj=True)
run(riskadj=True, c=0.0006)
print("\n完成。判据：OOS 在相邻参数/成本/币集下普遍 PF>1、分月多数为正，才算稳健。")
