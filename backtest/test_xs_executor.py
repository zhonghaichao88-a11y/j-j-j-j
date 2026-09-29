#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""alpha_xs_executor 下单执行器离线测试（mock OKX，不联网）。"""
import os,sys
import pandas as pd
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE); sys.path.insert(0,ROOT)
import alpha_xs_executor as XE

fails=[]
def chk(c,m):
    print(("  OK  " if c else " FAIL ")+m)
    if not c: fails.append(m)

LONG=["A","B","C","D","E"]; SHORT=["F","G","H","I","J"]
def w(longs=LONG, shorts=SHORT, extra=None):
    d={s:0.2 for s in longs}; d.update({s:-0.2 for s in shorts})
    if extra: d.update(extra)
    return pd.Series(d)
prices={s:1.0 for s in LONG+SHORT+["K","L"]}
def pos(sym,side,amt):
    return {"symbol":f"{sym}/USDT:USDT","side":side,"contracts":amt,"type":"swap"}

# 1 首次建仓
p=XE.plan_orders(w(),prices,[],10000)
chk(p["ok"] and p["n_orders"]==10,"首次建仓10单")
buys=[s for s in p["steps"] if s["side"]=="buy"]; sells=[s for s in p["steps"] if s["side"]=="sell"]
chk(len(buys)==5 and len(sells)==5,"5买5空")
chk(all(not s["reduce_only"] for s in p["steps"]),"建仓均非reduce_only")
chk(abs(sum(s["notional"] for s in buys)-10000)<1 and abs(sum(s["notional"] for s in sells)-10000)<1,"多空各10000U名义")

# 2 目标不变零换手
hold=[pos(s,"long",2000) for s in LONG]+[pos(s,"short",2000) for s in SHORT]
p2=XE.plan_orders(w(),prices,hold,10000)
chk(p2["ok"] and p2["n_orders"]==0,"持仓已等于目标时零下单")

# 3 清仓一个多头
w3=w(); w3["A"]=0.0
hold3=hold[:]
p3=XE.plan_orders(w3,prices,hold3,10000)
a=[s for s in p3["steps"] if s["base"]=="A"]
chk(len(a)==1 and a[0]["side"]=="sell" and a[0]["reduce_only"] and abs(a[0]["notional"]-2000)<1,"清多头=卖出reduce_only全额")

# 4 多翻空
w4=w(); w4["A"]=-0.2
p4=XE.plan_orders(w4,prices,hold,10000)
a4=[s for s in p4["steps"] if s["base"]=="A"]
chk(len(a4)==2 and a4[0]["side"]=="sell" and a4[0]["reduce_only"] and (not a4[1]["reduce_only"]),"多翻空:先平多再开空")

# 5 空翻多
w5=w(); w5["F"]=0.2
p5=XE.plan_orders(w5,prices,hold,10000)
f5=[s for s in p5["steps"] if s["base"]=="F"]
chk(len(f5)==2 and f5[0]["side"]=="buy" and f5[0]["reduce_only"] and (not f5[1]["reduce_only"]),"空翻多:先平空再开多")

# 6 减多
w6=w(); w6["A"]=0.1
p6=XE.plan_orders(w6,prices,hold,10000)
a6=[s for s in p6["steps"] if s["base"]=="A"][0]
chk(a6["side"]=="sell" and a6["reduce_only"] and abs(a6["notional"]-1000)<1,"减多50%=卖1000U reduce_only")

# 7 风控拦截
chk(not XE.plan_orders(w(extra={"K":0.2,"L":0.2}),prices,[],10000)["ok"],"gross超限整批拒绝")
chk(not XE.plan_orders(pd.Series({"A":0.3}),prices,[],10000)["ok"],"单币超25%拒绝")
chk(not XE.plan_orders(w(),prices,[],0)["ok"],"权益非正拒绝")
# 有持仓但缺价格 -> 看门狗新行为：不再整批拒单，坏币进 blocked 告警，其余币正常下单
pbad=XE.plan_orders(w(),{k:v for k,v in prices.items() if k!="A"},[pos("A","long",2000)],10000)
chk(pbad["ok"],"坏币无价格不再整批拒单,其余正常")
chk(len(pbad["blocked"])==1 and pbad["blocked"][0]["base"]=="A","坏币A进入blocked告警")
chk(pbad["n_orders"]==9 and all(s["base"]!="A" for s in pbad["steps"]),"其余9币正常下单且不含坏币A")

# 8 干跑不下单
class MockClient:
    is_connected=True
    def __init__(self,fail_sym=None): self.calls=[]; self.fail_sym=fail_sym
    def set_leverage_for_symbol(self,s,l): return True
    def place_order(self,side,ot,amount=None,price=None,symbol=None,reduce_only=False):
        if self.fail_sym and symbol.startswith(self.fail_sym): raise RuntimeError("模拟交易所拒单")
        self.calls.append((symbol,side,amount,reduce_only)); return {"id":"o%d"%len(self.calls),"status":"closed"}
mc=MockClient()
dry=XE.execute_plan(p,mc,live=False)
chk(dry["dry_run"] and len(mc.calls)==0,"live=False 干跑绝不真实下单")
mc2=MockClient(fail_sym="B")
live=XE.execute_plan(p,mc2,live=True)
chk(len(live["executed"])==9 and len(live["failed"])==1,"live=True 下单;单腿失败不影响其余9腿")
chk(len(mc2.calls)==9,"mock收到9次真实下单调用")
chk(any(c[0]=="A/USDT:USDT" and c[1]=="buy" and abs(c[2]-2000)<1 for c in mc2.calls),"A多:buy 2000币")
chk(any(c[0]=="F/USDT:USDT" and c[1]=="sell" and abs(c[2]-2000)<1 for c in mc2.calls),"F空:sell 2000币")
mc3=MockClient(); mc3.is_connected=False
chk(XE.execute_plan(p,mc3,live=True)["failed"]!=[],"未连接时拒绝实盘下单")

# 9 maker 限价：全成交 / 不成交 / 部分成交（ttl=0 加速）
class MakerMock:
    is_connected=True
    def __init__(self, fill=1.0): self.fill=fill; self.limit=[]; self.market=[]; self._n=0
    def set_leverage_for_symbol(self,s,l): return True
    def price_to_precision(self,s,px): return round(float(px),4)
    def get_ticker(self,s): return {"bid":0.9995,"ask":1.0005,"last":1.0}
    def place_order(self,side,ot,amount=None,price=None,symbol=None,reduce_only=False,post_only=False):
        if ot=="limit":
            self._n+=1; oid="L%d"%self._n
            self.limit.append((symbol,side,amount,price,post_only))
            return {"id":oid,"status":"open","filled":0}
        self.market.append((symbol,side,amount,reduce_only)); return {"id":"M%d"%len(self.market),"status":"closed","filled":amount}
    def fetch_order(self,oid,symbol=None):
        if str(oid).startswith("L"):
            idx=int(str(oid)[1:])-1; sym,side,amt,price,po=self.limit[idx]
            if self.fill>=1: return {"id":oid,"status":"closed","filled":amt}
            return {"id":oid,"status":"canceled","filled":amt*self.fill}
        return {"id":oid,"status":"closed","filled":0}
    def cancel_order(self,oid,symbol=None): return {"id":oid}
    def get_filled_amount(self,order,symbol): return float(order.get("filled") or 0)

mm=MakerMock(fill=1.0)
rm=XE.execute_plan(p,mm,live=True,entry_mode="maker_market",maker_ttl=0)
chk(rm["entry_mode"]=="maker_market" and rm["maker_filled"]==10 and rm["market_fallback"]==0,"maker全成交:不补市价")
chk(len(mm.limit)==10 and all(x[4] is True for x in mm.limit) and len(mm.market)==0,"10条post-only限价单、无市价")
mm0=MakerMock(fill=0.0)
r0=XE.execute_plan(p,mm0,live=True,entry_mode="maker_market",maker_ttl=0)
chk(r0["maker_filled"]==0 and r0["market_fallback"]==10 and len(mm0.market)==10,"maker不成交:全部转市价兜底")
mmh=MakerMock(fill=0.5)
rh=XE.execute_plan(p,mmh,live=True,entry_mode="maker_market",maker_ttl=0)
chk(rh["maker_filled"]==10 and rh["market_fallback"]==10,"maker部分成交:余量转市价")
mmd=MakerMock(fill=0.0)
rd=XE.execute_plan(p,mmd,live=True)
chk(rd["entry_mode"]=="market" and len(mmd.limit)==0 and len(mmd.market)==10,"默认仍走市价,旧行为不变")

# 10 maker 查询异常：必须先撤掉残留限价单，再转市价兜底，防止超仓
class MakerQueryFailMock(MakerMock):
    def __init__(self): super().__init__(fill=0.0); self.cancelled=[]
    def fetch_order(self,oid,symbol=None): raise RuntimeError("模拟查询失败")
    def cancel_order(self,oid,symbol=None): self.cancelled.append(oid); return {"id":oid}
mqf=MakerQueryFailMock()
rqf=XE.execute_plan(p,mqf,live=True,entry_mode="maker_market",maker_ttl=0)
chk(len(mqf.cancelled)==10,"maker查询异常:10条残留限价单全部被撤")
chk(len(mqf.market)==10 and rqf["market_fallback"]==10,"maker查询异常:撤单后全额转市价兜底")

# 11 回归：已持仓调仓必须用【账户总权益】锚定目标仓位，不能因“可用余额”缩水（真实事故：权益74/可用23，单14只留5仓）
OL=["ARB","FET","APT","WLD","STX"]; OS=["CHZ","BONK","MEME","JTO","WIF"]      # 9/18 旧持仓
NL=["INJ","STX","OP","WIF","IMX"]; NS=["WLD","ARB","FET","CRV","JTO"]          # 9/19 新篮子
allb=sorted(set(OL+OS+NL+NS)); px11={b:1.0 for b in allb}
hold11=[pos(b,"long",14.76) for b in OL]+[pos(b,"short",14.76) for b in OS]
w11=pd.Series({b:(0.2 if b in NL else -0.2) for b in (NL+NS)})
# 正确：用总权益 74 -> 每腿14.8U(>6U最小额)，新币都能开，16单
pOK=XE.plan_orders(w11,px11,hold11,74)
opened_new=[s for s in pOK["steps"] if s["base"] in ("INJ","OP","IMX","CRV") and not s["reduce_only"]]
chk(pOK["ok"] and pOK["n_orders"]==16,"总权益74:换手16单(8平8开)")
chk(len(opened_new)==4,"总权益74:4个纯新币(INJ/OP/IMX/CRV)全部开仓")
chk(all(abs(s["notional"]-14.8)<0.2 for s in opened_new),"总权益74:新币目标仓位14.8U(锚定总权益,不缩水)")
# 事故复现：若错用可用余额 23 -> 每腿仅4.6U，新币低于6U被跳过，仓位缩水（证明口径必须用总权益）
pBad=XE.plan_orders(w11,px11,hold11,23)
opened_bad=[s for s in pBad["steps"] if s["base"] in ("INJ","OP","IMX","CRV") and not s["reduce_only"]]
chk(pBad["n_orders"]==14 and len(opened_bad)==0,"错用可用余额23:仅14单且4个新币全部开不出(事故复现)")

# 12 回归：执行必须“先全部平仓(reduce_only)、再开仓”，保证平仓释放保证金后再开新仓
class OrderMock:
    is_connected=True
    def __init__(self): self.seq=[]
    def set_leverage_for_symbol(self,s,l): return True
    def place_order(self,side,ot,amount=None,price=None,symbol=None,reduce_only=False,post_only=False):
        self.seq.append((ot,bool(reduce_only),symbol))
        return {"id":"x%d"%len(self.seq),"status":"closed","filled":amount}
def close_first(seq):
    first_open=min([i for i,x in enumerate(seq) if not x[1]] or [len(seq)])
    return all(x[1] for x in seq[:first_open])
om=OrderMock()
XE.execute_plan(pOK,om,live=True,entry_mode="market")
chk(any(not x[1] for x in om.seq) and close_first(om.seq),"市价模式:所有平仓单都在开仓单之前")
class MakerOrder(OrderMock):
    def price_to_precision(self,s,px): return round(float(px),4)
    def get_ticker(self,s): return {"bid":0.9995,"ask":1.0005,"last":1.0}
mo=MakerOrder()
XE.execute_plan(pOK,mo,live=True,entry_mode="maker_market",maker_ttl=0)
lim=[x for x in mo.seq if x[0]=="limit"]
chk(any(not x[1] for x in lim) and close_first(lim),"maker模式:平仓限价单先挂、开仓限价单后挂(先平后开)")

print("\n执行器测试：", "全部通过 ✅" if not fails else f"{len(fails)}项失败 ❌ {fails}")
sys.exit(1 if fails else 0)
