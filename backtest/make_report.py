#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 v2 vs v3 真实数据回测图表(PNG) + ECharts数据JSON。"""
import os,sys,json
import numpy as np,pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
BT=os.path.dirname(os.path.abspath(__file__));ROOT=os.path.dirname(BT)
ASSETS=os.path.join(BT,"report_assets");os.makedirs(ASSETS,exist_ok=True)
for p in ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc","/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"]:
    if os.path.exists(p): fm.fontManager.addfont(p)
plt.rcParams["font.family"]="Noto Sans CJK SC";plt.rcParams["axes.unicode_minus"]=False
COST=0.0012
def load(f):
    t=pd.read_csv(os.path.join(BT,f));t["dt"]=pd.to_datetime(t["entry_time"])
    t["net"]=t["gross_r"]-COST/t["sl_pct"]
    for c in (0.0008,0.0012,0.002): t[f"net_{int(c*1e4)}"]=t["gross_r"]-c/t["sl_pct"]
    return t.sort_values("dt").reset_index(drop=True)
v2=load("v2_trades.csv");v3=load("v3_trades.csv")
C={"v2":"#c0392b","v3":"#1f77b4"}
# 分界
oos=pd.Timestamp("2026-07-01")
# 1 资金曲线
fig,ax=plt.subplots(figsize=(10,4.2))
for nm,t in [("v2 旧版",v2),("v3 最终版",v3)]:
    ax.plot(t.dt,t.net.cumsum(),lw=1.6,label=nm,color=C["v2"] if nm.startswith("v2") else C["v3"])
ax.axvline(oos,color="#888",ls="--",lw=1);ax.text(oos,ax.get_ylim()[1]*0.95," 样本外OOS→",color="#555",fontsize=9)
ax.axhline(0,color="#333",lw=.8);ax.set_title("累计净盈亏(R)，往返成本0.12%（6个主流币，2026-03-01~09-15）")
ax.set_ylabel("累计 R");ax.legend();plt.tight_layout();plt.savefig(os.path.join(ASSETS,"01_equity.png"),dpi=130);plt.close()
# 2 PF/avgR 对比（三成本）
costs=[0.0008,0.0012,0.0020];labels=["0.08%(maker)","0.12%(混合)","0.20%(taker)"]
def mat(t,kind):
    out=[]
    for c in costs:
        x=t[f"net_{int(c*1e4)}"]
        if kind=="pf": out.append(x[x>0].sum()/max(-x[x<0].sum(),1e-9))
        else: out.append(x.mean())
    return out
fig,axs=plt.subplots(1,2,figsize=(11,4))
x=np.arange(3);w=.35
axs[0].bar(x-w/2,mat(v2,"pf"),w,label="v2 旧版",color=C["v2"]);axs[0].bar(x+w/2,mat(v3,"pf"),w,label="v3 最终版",color=C["v3"])
axs[0].axhline(1.0,color="#2ca02c",ls="--",lw=1);axs[0].set_xticks(x);axs[0].set_xticklabels(labels);axs[0].set_title("盈亏比 PF（>1 才盈利）");axs[0].legend()
for i,(a,b) in enumerate(zip(mat(v2,"pf"),mat(v3,"pf"))):
    axs[0].text(i-w/2,a+.02,f"{a:.2f}",ha="center",fontsize=8);axs[0].text(i+w/2,b+.02,f"{b:.2f}",ha="center",fontsize=8)
axs[1].bar(x-w/2,mat(v2,"avg"),w,label="v2 旧版",color=C["v2"]);axs[1].bar(x+w/2,mat(v3,"avg"),w,label="v3 最终版",color=C["v3"])
axs[1].axhline(0,color="#333",lw=.8);axs[1].set_xticks(x);axs[1].set_xticklabels(labels);axs[1].set_title("单笔平均期望(R)");axs[1].legend()
for i,(a,b) in enumerate(zip(mat(v2,"avg"),mat(v3,"avg"))):
    axs[1].text(i-w/2,a+.01,f"{a:.2f}",ha="center",fontsize=8);axs[1].text(i+w/2,b+.01,f"{b:.2f}",ha="center",fontsize=8)
plt.tight_layout();plt.savefig(os.path.join(ASSETS,"02_compare.png"),dpi=130);plt.close()
# 3 平仓原因 v3
fig,ax=plt.subplots(figsize=(9,4))
order=["TP","BE","SL","STAGNATION","REVERSAL","TIMEOUT"]
cnt=v3.groupby(["period","exit_reason"]).size().unstack(fill_value=0).T.reindex(order).fillna(0)
cnt=cnt.div(cnt.sum(axis=0),axis=1)*100
xx=np.arange(len(order));w=.38
ax.bar(xx-w/2,cnt.get("INS",pd.Series(np.zeros(len(order)),index=order)),w,label="样本内INS",color="#7f9dc4")
ax.bar(xx+w/2,cnt.get("OOS",pd.Series(np.zeros(len(order)),index=order)),w,label="样本外OOS",color="#c49a7f")
ax.set_xticks(xx);ax.set_xticklabels(["止盈","保本","止损","横盘","主动反转","超时"]);ax.set_ylabel("%");ax.set_title("v3 平仓方式分布");ax.legend()
plt.tight_layout();plt.savefig(os.path.join(ASSETS,"03_exits.png"),dpi=130);plt.close()
# 4 分币 PF v3
fig,ax=plt.subplots(figsize=(9,4))
syms=sorted(v3.symbol.unique())
def pf(g):
    x=g.net;return x[x>0].sum()/max(-x[x<0].sum(),1e-9)
pins=[pf(v3[(v3.symbol==s)&(v3.period=="INS")]) for s in syms];poos=[pf(v3[(v3.symbol==s)&(v3.period=="OOS")]) for s in syms]
xx=np.arange(len(syms))
ax.bar(xx-w/2,pins,w,label="INS",color="#7f9dc4");ax.bar(xx+w/2,poos,w,label="OOS",color="#c49a7f")
ax.axhline(1,color="#2ca02c",ls="--");ax.set_xticks(xx);ax.set_xticklabels([s.replace("USDT","") for s in syms]);ax.set_title("v3 分币种 PF（0.12%成本）");ax.legend()
plt.tight_layout();plt.savefig(os.path.join(ASSETS,"04_symbol_pf.png"),dpi=130);plt.close()
# JSON for ECharts
def curve(t,c):
    s=(t["gross_r"]-c/t["sl_pct"]).cumsum()
    return [list(map(str,t.dt.dt.strftime("%m-%d").tolist())),[round(float(x),1) for x in s]]
data={
 "costs":labels,
 "pf":{"v2":[round(x,2) for x in mat(v2,"pf")],"v3":[round(x,2) for x in mat(v3,"pf")]},
 "avg":{"v2":[round(x,3) for x in mat(v2,"avg")],"v3":[round(x,3) for x in mat(v3,"avg")]},
 "curve_v2":curve(v2,COST),"curve_v3":curve(v3,COST),
 "exits_order":["止盈","保本","止损","横盘","主动反转","超时"],
 "exits_ins":[round(float(cnt.get("INS",pd.Series(np.zeros(len(order)),index=order)).get(k,0)),1) for k in order],
 "exits_oos":[round(float(cnt.get("OOS",pd.Series(np.zeros(len(order)),index=order)).get(k,0)),1) for k in order],
 "syms":[s.replace("USDT","") for s in syms],"pf_ins":[round(x,2) for x in pins],"pf_oos":[round(x,2) for x in poos],
}
json.dump(data,open(os.path.join(ASSETS,"charts.json"),"w"),ensure_ascii=False)
print("charts done")
