#!/usr/bin/env python3
"""独立裸K原型 edge 测试（真实数据，无前视；信号在i收盘确认，i+1开盘成交）。"""
import os, sys, json
import numpy as np, pandas as pd
BT=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,BT)
import fast_backtest as fb
from sweep import simulate, summ

W_VWAP=96           # 8h 滚动VWAP窗口(5m)
SWEEP_N=12

def ema(s,n): return s.ewm(span=n,adjust=False).mean()

def indicators(df, rs):
    c=df["close"];h=df["high"];l=df["low"];o=df["open"];v=df["vol"];t=df["open_ms"]
    pc=c.shift(1); tr=pd.concat([(h-l),(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1)
    atr=tr.rolling(14).mean()
    typ=(h+l+c)/3; vv=typ*v
    vwap=(vv.rolling(W_VWAP).sum()/v.rolling(W_VWAP).sum())
    sd=c.rolling(W_VWAP).std()
    upper=vwap+1.5*sd; lower=vwap-1.5*sd
    # 1h 趋势（只用已收盘1h）
    r1=rs["1h"].copy()
    r1["ema50"]=ema(r1["close"],50); r1["ema21"]=ema(r1["close"],21)
    r1["close_time"]=r1["ts_ms"]+3600_000
    m=pd.DataFrame({"cc":t+300_000})
    m=pd.merge_asof(m.sort_values("cc"), r1[["close_time","close","ema50","ema21"]].sort_values("close_time"),
                    left_on="cc",right_on="close_time",direction="backward")
    htf_up=(m["close"]>m["ema50"]) & (m["ema21"]>m["ema50"])
    htf_dn=(m["close"]<m["ema50"]) & (m["ema21"]<m["ema50"])
    # 15m ER
    r15=rs["15m"]; er15=pd.Series(index=t,dtype=float)
    c15=r15["close"]; er_v=abs(c15-c15.shift(14))/c15.diff().abs().rolling(14).sum()
    er15 = pd.merge_asof(pd.DataFrame({"cc":(t+300_000)}).sort_values("cc"),
                         pd.DataFrame({"ct":r15["ts_ms"]+900_000,"er":er_v.values}).sort_values("ct"),
                         left_on="cc",right_on="ct",direction="backward")["er"].to_numpy()
    return dict(o=o.to_numpy(),h=h.to_numpy(),l=l.to_numpy(),c=c.to_numpy(),v=v.to_numpy(),
                atr=atr.to_numpy(),vwap=vwap.to_numpy(),upper=upper.to_numpy(),lower=lower.to_numpy(),
                htf_up=htf_up.to_numpy(),htf_dn=htf_dn.to_numpy(),er15=er15)

def archetype_signals(df,rs):
    I=indicators(df,rs); n=len(I["c"]); S={k:[] for k in
        ["sweep_rev","vwap_pull","breakout_pull","vwap_fade","trend_sweep"]}
    h=I["h"];l=I["l"];c=I["c"];o=I["o"];atr=I["atr"];vw=I["vwap"];up=I["upper"];lo=I["lower"]
    hu=I["htf_up"];hd=I["htf_dn"];er=I["er15"]
    def mk(i,side): return dict(i=int(i),side=int(side),atr=float(atr[i]),px=float(c[i]),ssl=0.0,stp=0.0,
                                path="",tier="")
    rng=np.maximum(h-l,1e-12)
    for i in range(120,n-2):
        if not np.isfinite(atr[i]) or not np.isfinite(vw[i]): continue
        ph_hi=np.max(h[i-SWEEP_N:i]); ph_lo=np.min(l[i-SWEEP_N:i])
        body_pos=(c[i]-l[i])/rng[i]
        # A 扫损反转：扫前低收回(多)/扫前高收回(空)
        if l[i]<ph_lo and c[i]>ph_lo and body_pos>=0.55:
            S["sweep_rev"].append(mk(i,1))
        if h[i]>ph_hi and c[i]<ph_hi and body_pos<=0.45:
            S["sweep_rev"].append(mk(i,-1))
        # B 趋势中回踩VWAP后重新站回(多)/压回(空)
        touched_lo=np.min(l[i-3:i+1])<=vw[i]*1.001; touched_hi=np.max(h[i-3:i+1])>=vw[i]*0.999
        if hu[i] and touched_lo and c[i]>vw[i] and c[i]>=o[i] and body_pos>=0.55 and er[i]>=0.30:
            S["vwap_pull"].append(mk(i,1))
        if hd[i] and touched_hi and c[i]<vw[i] and c[i]<=o[i] and body_pos<=0.45 and er[i]>=0.30:
            S["vwap_pull"].append(mk(i,-1))
        # C 突破回踩延续（v2风格，验证是否亏）
        bk_hi=np.max(h[i-12:i-4]); bk_lo=np.min(l[i-12:i-4])
        if c[i-4]>bk_hi and np.min(l[i-3:i+1])<=bk_hi and c[i]>bk_hi: S["breakout_pull"].append(mk(i,1))
        if c[i-4]<bk_lo and np.max(h[i-3:i+1])>=bk_lo and c[i]<bk_lo: S["breakout_pull"].append(mk(i,-1))
        # D VWAP延伸均值回归（触碰外轨+拒绝K，朝VWAP回归）
        if h[i]>=up[i] and c[i]<o[i] and body_pos<=0.4: S["vwap_fade"].append(mk(i,-1))
        if l[i]<=lo[i] and c[i]>o[i] and body_pos>=0.6: S["vwap_fade"].append(mk(i,1))
        # E 顺势扫损（HTF趋势方向上，反向扫流动性后收回，沿趋势进场）
        if hu[i] and l[i]<ph_lo and c[i]>ph_lo and body_pos>=0.55 and er[i]>=0.30:
            S["trend_sweep"].append(mk(i,1))
        if hd[i] and h[i]>ph_hi and c[i]<ph_hi and body_pos<=0.45 and er[i]>=0.30:
            S["trend_sweep"].append(mk(i,-1))
    return I,S

def fwd_edge(df,S):
    c=df["close"].to_numpy(); atr=None
    rows=[]
    for name,E in S.items():
        if not E: continue
        for hbar in (6,12,24):
            rs_=[];hit=0
            for e in E:
                i=e["i"]
                if i+hbar>=len(c): continue
                r=e["side"]*(c[i+hbar]-c[i+1])/max(e["atr"],1e-12)  # 以ATR计，i+1开盘近似用c[i]
                rs_.append(r); hit+= (r>0)
            rs_=np.array(rs_)
            rows.append(dict(arch=name,h=hbar,n=len(rs_),hit=round(100*hit/max(len(rs_),1),1),
                             meanATR=round(rs_.mean(),3)))
    return rows

if __name__=="__main__":
    syms=["BTCUSDT","ETHUSDT","SOLUSDT"]
    start=fb.ms("2026-03-01");end=fb.ms("2026-06-30")
    agg={}; rawrows=[]
    for sym in syms:
        df,rs=fb.load_symbol(sym)
        I,S=archetype_signals(df,rs)
        for k in S:
            S[k]=[e for e in S[k] if start<=df.open_ms.iloc[e["i"]]<=end]
        for r in fwd_edge(df,S): r["sym"]=sym; rawrows.append(r)
        for name,E in S.items():
            agg.setdefault(name,[]).append((E,df))
    print("=== 原始forward edge（ATR单位，样本内3-6月）===")
    rr=pd.DataFrame(rawrows)
    print(rr.pivot_table(index=["arch"],columns="h",values=["hit","meanATR"],aggfunc="mean").round(2).to_string())
    print("\n=== ATR管理后期望（BE保本，横盘止损开，48根上限）===")
    grid=[(1.2,1.3,0.0035),(1.5,1.3,0.004),(1.5,1.5,0.004),(1.2,1.0,0.0035),(2.0,1.5,0.0045)]
    for name in agg:
        for (k,rrr,fl) in grid:
            pol=dict(mode="atr",k=k,rr=rrr,floor=fl,be=True,stagnate=True)
            dfs=[simulate(E,dfx,pol) for (E,dfx) in agg[name]]
            s=summ(pd.concat(dfs),f"{name} k{k} rr{rrr}")
            print(json.dumps(s,ensure_ascii=False))
        print()
