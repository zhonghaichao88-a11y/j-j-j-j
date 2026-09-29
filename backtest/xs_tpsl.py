#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""截面多空 + 每条腿止盈/止损管理：检验能否把胜率提到≥60%且PF仍>1。
信号同 xs_backtest(24h动量 top5/bottom5, t收盘信号 t+1开盘进场)；
每条腿在最多持有24h内用5m high/low 判 TP/SL，同根同时触及按【先止损】悲观处理；
taker 单边费0.0006(开+平)；提前平仓资金当日空仓到下次调仓。INS/OOS。"""
import os,math
import numpy as np,pandas as pd
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE); import sys; sys.path.insert(0,ROOT)
import alpha_xs_neutral as XS
SMALL=["WIF","SUI","SEI","APT","INJ","TIA","OP","ARB","WLD","FET","RUNE","AAVE","CRV","ENS",
 "GALA","SAND","AXS","CHZ","LDO","ALT","JTO","PEPE","SHIB","FLOKI","MEME","BONK","RENDER","STX","IMX","ARKM","ORDI"]
CUT=pd.Timestamp("2026-07-01",tz="UTC"); L=rb=288; k=5; FEE=0.0006

def load(s):
    d=pd.read_csv(os.path.join(HERE,"data_small",f"{s}USDT_5m.csv")); d["t"]=pd.to_datetime(d["open_ms"],unit="ms",utc=True); return d.set_index("t")
data={s:load(s) for s in SMALL}
CL=pd.DataFrame({s:data[s]["close"] for s in SMALL}).sort_index().dropna(how="any")
idx=np.arange(L,len(CL)-rb-1,rb); cols=list(CL.columns)

def leg_exit(s,side,entry_i,tp,sl):
    """从 entry_i(开盘进场)起最多 rb 根，返回(收益率, 退出类型)。悲观:同根先止损。"""
    d=data[s]; j0=entry_i
    hi=d["high"].iloc[j0:j0+rb].values; lo=d["low"].iloc[j0:j0+rb].values
    op=d["open"].iloc[j0:j0+rb].values
    for j in range(len(op)):
        h,l=hi[j],lo[j]
        if side==1:
            if l<=entry_i and False: pass
            sl_px=op[0]*(1-sl); tp_px=op[0]*(1+tp)
            if l<=sl_px: return (-sl-FEE*2,"SL")
            if h>=tp_px: return (tp-FEE*2,"TP")
        else:
            sl_px=op[0]*(1+sl); tp_px=op[0]*(1-tp)
            if h>=sl_px: return (-sl-FEE*2,"SL")
            if l<=tp_px: return (tp-FEE*2,"TP")
    end=op[-1]
    return ((end/op[0]-1) if side==1 else (op[0]/end-1))-FEE*2,"TIME"

def run(tp,sl,cap=None):
    # 每日组合收益(等权10腿, 权重±0.2 相对权益); 逐腿结果按调仓日聚合
    rows=[]; typ_cnt={"TP":0,"SL":0,"TIME":0}
    for i in idx:
        t=CL.index[i]
        sc=XS.momentum_score(CL.iloc[:i+1],L); b=XS.select_basket(sc,k=k)
        if b.get("reason")!="ok": continue
        legs=[]
        for s in b["longs"]:
            r,ty=leg_exit(s,1,i+1,tp,sl); legs.append(r); typ_cnt[ty]+=1
        for s in b["shorts"]:
            r,ty=leg_exit(s,-1,i+1,tp,sl); legs.append(r); typ_cnt[ty]+=1
        if cap is not None: legs=[np.clip(x,-cap,cap) for x in legs]
        rows.append((t,float(np.mean(legs))))   # 10腿等权, 多空各合计1倍名义 -> 日均=均值
    R=pd.Series({t:v for t,v in rows})
    def met(seg):
        x=seg.values; win=(x>0).mean(); pf=x[x>0].sum()/max(1e-12,-x[x<0].sum())
        eq=(1+pd.Series(x)).cumprod(); dd=((eq.cummax()-eq)/eq.cummax()).max()
        sh=x.mean()/x.std()*np.sqrt(365)
        return f"n={len(x)} 胜率{100*win:.0f}% PF{pf:.2f} 夏普{sh:.2f} 复利{100*(eq.iloc[-1]-1):+.0f}% 回撤{100*dd:.0f}%"
    ins=R[R.index<CUT]; oos=R[R.index>=CUT]
    return ins,oos,typ_cnt

print("="*100)
for tp,sl in [(0.05,0.06),(0.06,0.08),(0.08,0.10),(0.10,0.12),(0.12,0.15),(0.20,0.20)]:
    ins,oos,tc=run(tp,sl)
    def line(seg):
        x=seg.values; win=(x>0).mean();pf=x[x>0].sum()/max(1e-12,-x[x<0].sum());eq=(1+pd.Series(x)).cumprod()
        dd=((eq.cummax()-eq)/eq.cummax()).max();sh=x.mean()/x.std()*np.sqrt(365)
        return f"胜{100*win:.0f}% PF{pf:.2f} 夏{sh:.1f} {100*(eq.iloc[-1]-1):+.0f}% 撤{100*dd:.0f}%"
    print(f"TP{tp*100:.0f}%/SL{sl*100:.0f}%  INS {line(ins)} | OOS {line(oos)}")
print("\n基准(无TP/SL, 持有24h) 对照见 xs_backtest：OOS 胜50% PF1.44")
