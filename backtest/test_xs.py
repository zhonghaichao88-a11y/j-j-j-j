#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""alpha_xs_neutral 生产信号模块自检（排名/权重/换手指令正确性）。"""
import os,sys
import numpy as np,pandas as pd
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE); sys.path.insert(0,ROOT)
import alpha_xs_neutral as XS

fails=[]
def chk(c,msg):
    print(("  OK " if c else " FAIL ")+msg);
    if not c: fails.append(msg)

# 构造 12 个币、300 根5m：A-E 稳步上涨(强)，H-L 稳步下跌(弱)，F/G 平
n=300; syms=[f"C{i:02d}" for i in range(16)]
cl=pd.DataFrame(index=range(n),columns=syms,dtype=float)
rng=np.linspace
for j,s in enumerate(syms):
    base=100.0
    if j<5:   ret=np.linspace(0.0006,0.0010,5)[min(j,4)]          # 强(递增)
    elif j>=11:ret=-np.linspace(0.0006,0.0010,5)[min(j-11,4)]      # 弱
    else: ret=0.0
    cl[s]=base*np.cumprod(1+pd.Series([ret]*n))
score=XS.momentum_score(cl,288)
b=XS.select_basket(score,k=5)
print("多头篮子:",b["longs"]); print("空头篮子:",b["shorts"])
chk(set(b["longs"])==set(syms[:5]),"选中最强5个做多")
chk(set(b["shorts"])==set(syms[11:]),"选中最弱5个做空")
chk(b["reason"]=="ok","正常返回 ok")

w=XS.target_weights(b,gross=2.0)
chk(abs(w[w>0].sum()-1.0)<1e-9,"多腿合计=1.0(gross/2)")
chk(abs(w[w<0].sum()+1.0)<1e-9,"空腿合计=-1.0")
chk(abs(w.sum())<1e-9,"净敞口≈0(市场中性)")
chk(np.allclose(w[w>0],0.2) and np.allclose(w[w<0],-0.2),"多空各等权±0.2")

# 币不足应放弃
few=XS.select_basket(score[syms[:6]],k=5)
chk(few["longs"]==[] and "放弃" in few["reason"],"币不足时宁可不做")

# 换手指令：从零建仓应 10 条 open；再平衡到相同目标应 0 条
px=pd.Series(100.0,index=syms)
prev0=pd.Series(0.0,index=syms)
o1=XS.rebalance_orders(prev0,w,px,equity=10000,min_notional=1)
chk(len(o1)==10,"首次建仓产生10条订单")
chk(all(x["action"]=="open" for x in o1),"全部为 open")
longs=[x for x in o1 if x["side"]=="long"]; shorts=[x for x in o1 if x["side"]=="short"]
chk(len(longs)==5 and len(shorts)==5,"5多5空")
chk(abs(sum(x["delta_notional"] for x in longs)-10000)<1,"多头名义≈10000(权益1倍)")
chk(abs(sum(x["delta_notional"] for x in shorts)+10000)<1,"空头名义≈-10000")
o2=XS.rebalance_orders(w,w,px,10000,min_notional=1)
chk(len(o2)==0,"目标不变时零换手(省手续费)")
# 平掉一个多头 -> 应产生 close
w2=w.copy(); w2[b["longs"][0]]=0.0
o3=XS.rebalance_orders(w,w2,px,10000,min_notional=1)
chk(len(o3)==1 and o3[0]["action"]=="close" and o3[0]["symbol"]==b["longs"][0],"退出腿正确生成close")

print("\n自检结果：", "全部通过 ✅" if not fails else f"{len(fails)}项失败 ❌")
sys.exit(1 if fails else 0)
