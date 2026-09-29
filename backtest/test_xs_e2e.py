#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v5-XS 实盘调仓全链路端到端（mock OKX，K线用真实历史数据，验证下单方向/数量/换手/反手）。"""
import os,sys
import pandas as pd
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE); sys.path.insert(0,ROOT); sys.path.insert(0,HERE)
import alpha_xs_neutral as XS, alpha_xs_executor as XE
SMALL=["WIF","SUI","SEI","APT","INJ","TIA","OP","ARB","WLD","FET","RUNE","AAVE","CRV","ENS",
 "GALA","SAND","AXS","CHZ","LDO","ALT","JTO","PEPE","SHIB","FLOKI","MEME","BONK","RENDER","STX","IMX","ARKM","ORDI"]

class MockOKX:
    is_connected=True
    def __init__(self,tail=300): self.tail=tail; self.orders=[]; self._pos=[]
    def set_leverage_for_symbol(self,s,l): return True
    def get_ohlcv(self,symbol,tf="5m",limit=300):
        base=symbol.split("/")[0]
        d=pd.read_csv(os.path.join(HERE,"data_small",f"{base}USDT_5m.csv")).tail(self.tail)
        return [[int(r.open_ms),r.open,r.high,r.low,r.close,r.vol] for r in d.itertuples()]
    def get_balance(self): return {"available_equity":10000.0,"equity":10000.0}
    def get_positions(self): return self._pos
    def place_order(self,side,ot,amount=None,price=None,symbol=None,reduce_only=False):
        self.orders.append((symbol,side,round(amount,6),reduce_only)); return {"id":f"o{len(self.orders)}","status":"closed"}

def build(client,k=5,lookback=288,gross=2.0,equity=None,live=False):
    closes={}
    for b in SMALL:
        raw=client.get_ohlcv(f"{b}/USDT:USDT","5m",lookback+2)
        df=pd.DataFrame(raw,columns=["ts","o","h","l","c","v"]); df["t"]=pd.to_datetime(df["ts"],unit="ms",utc=True)
        closes[b]=df.set_index("t")["c"].astype(float)
    CL=pd.DataFrame(closes).sort_index()
    sc=XS.momentum_score(CL,lookback); basket=XS.select_basket(sc,k=k)
    w=XS.target_weights(basket,gross=gross); prices={s:float(CL[s].iloc[-1]) for s in w.index}
    eq=equity or client.get_balance()["available_equity"]
    plan=XE.plan_orders(w,prices,client.get_positions(),float(eq),max_gross=gross)
    res=XE.execute_plan(plan,client,live=live)
    return basket,w,plan,res

fails=[]
def chk(c,m):
    print(("  OK  " if c else " FAIL ")+m);
    if not c: fails.append(m)

print("=== 场景1: 空仓首次调仓，live=True(mock成交) ===")
c=MockOKX(); b,w,plan,res=build(c,live=True)
print("  多",b["longs"]); print("  空",b["shorts"])
chk(plan["ok"] and plan["n_orders"]==10,"生成10条调仓单")
chk(len(c.orders)==10,"mock实际收到10次下单")
chk(sum(1 for o in c.orders if o[1]=="buy")==5 and sum(1 for o in c.orders if o[1]=="sell")==5,"5买5空")
chk(all(o[3] is False for o in c.orders),"首次建仓无reduce_only")
long_notional=sum(o[2] for o in c.orders if o[1]=="buy")  # 币数,价格≈量级不同,仅核对方向
chk(len(res["failed"])==0,"无失败单")

print("\n=== 场景2: 持仓已等于目标 -> 再次调仓零下单(精确换手省费) ===")
# 用场景1目标构造持仓
c2=MockOKX()
_,w1,_,_=build(c2,live=False)
c2._pos=[{"symbol":f"{s}/USDT:USDT","side":"long" if w1[s]>0 else "short",
          "contracts":abs(w1[s])*10000/1.0,"type":"swap"} for s in w1.index]
# 价格用1会失真, 这里直接让 plan 用真实价: 重新build但注入持仓
b2,w2,plan2,res2=build(c2,live=False)
# 因 c2._pos 名义按价1.0算与真实价不符, 会有调整; 改为按真实价重算持仓量
prices={s:float(pd.read_csv(os.path.join(HERE,'data_small',f'{s}USDT_5m.csv')).tail(1)['close'].iloc[0]) for s in w1.index}
c2._pos=[{"symbol":f"{s}/USDT:USDT","side":"long" if w1[s]>0 else "short",
          "contracts":abs(w1[s])*10000/prices[s],"type":"swap"} for s in w1.index]
b2,w2,plan2,res2=build(c2,live=False)
chk(plan2["n_orders"]==0,f"持仓=目标时零换手(实际{plan2['n_orders']}单)")

print("\n=== 场景3: 干跑 live=False 绝不调用 place_order ===")
c3=MockOKX(); build(c3,live=False)
chk(len(c3.orders)==0,"干跑零真实下单")

print("\n=== 场景4: 风控-权益过小导致名义低于最小量不影响, 但 gross 超限拒绝 ===")
c4=MockOKX()
closes={}
for sym in SMALL:
    raw=c4.get_ohlcv(f"{sym}/USDT:USDT",290)
    df=pd.DataFrame(raw,columns=["ts","o","h","l","c","v"]); df["t"]=pd.to_datetime(df["ts"],unit="ms",utc=True)
    closes[sym]=df.set_index("t")["c"].astype(float)
CL=pd.DataFrame(closes).sort_index(); sc=XS.momentum_score(CL,288); bb=XS.select_basket(sc,k=5)
ww=XS.target_weights(bb,gross=3.2)  # 超过2.0上限
pr={s:float(CL[s].iloc[-1]) for s in ww.index}
bad=XE.plan_orders(ww,pr,[],10000,max_gross=2.0)
chk((not bad["ok"]) and "上限" in bad["reason"],"gross3.2被风控整批拒绝")

print("\n端到端测试：", "全部通过 ✅" if not fails else f"{len(fails)}失败 ❌ {fails}")
sys.exit(1 if fails else 0)
