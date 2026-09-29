# -*- coding: utf-8 -*-
"""
open_maker 状态机本地模拟撮合测试（不联网、不下真单）。
用一个假的 ccxt 交易所驱动各种结局，验证 alpha_live.AlphaLiveExecutor.open_maker：
  A 立即全部成交→保护验证→成功
  B 挂到超时零成交→撤单→返回 filled=0/timeout
  C post-only 首次被拒→按新盘口重挂→成交
  D 部分成交→撤剩余→按已成交部分挂保护→成功(partial_entry)
  E 价格朝有利方向跑掉→撤单不追→filled=0/ran_away
  F 成交但保护验证失败→强平并确认归零→抛错（fail-closed）
  G maker_market：超时未成交→回退市价 open()
"""
import os,sys
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,ROOT)
import alpha_live as AL

class FakeEx:
    def __init__(self,sc): self.sc=sc; self.orders={}; self.n=0; self.cancels=[]; self.creates=0
    def price_to_precision(self,cs,x): return float(round(float(x),6))
    def amount_to_precision(self,cs,x): return float(round(float(x),6))
    def fetch_ticker(self,cs):
        bid,ask,last=self.sc.ticker(self.sc.clock)
        return {"bid":bid,"ask":ask,"last":last}
    def create_order(self,cs,typ,side,amount,price,params):
        self.creates+=1
        if self.sc.reject_creates and self.creates<=self.sc.reject_creates:
            raise Exception("51020 Post Only orders cannot be executed immediately")
        self.n+=1; oid="O%d"%self.n
        self.orders[oid]={"side":side,"amount":amount,"price":price,"params":params,"canceled":False}
        return {"id":oid,"status":"open","filled":0.0,"average":0.0}
    def fetch_order(self,oid,cs):
        o=self.orders[oid]
        if o["canceled"]:
            f=o.get("final_filled",0.0)
            return {"status":("closed" if f>0 else "canceled"),"filled":f,"average":(o.get("final_avg",0.0) if f>0 else 0.0)}
        st,fi,av=self.sc.order_state(self.sc.clock)
        return {"status":st,"filled":fi,"average":av}
    def cancel_order(self,oid,cs):
        o=self.orders[oid]; o["canceled"]=True
        st,fi,av=self.sc.order_state(self.sc.clock)
        o["final_filled"]=fi; o["final_avg"]=av
        self.cancels.append(oid); return {"id":oid,"status":"canceled"}

class Scenario:
    def __init__(self,name,order_state,ticker,protect_ok=True,reject_creates=0,fallback=False,close_ok=True):
        self.name=name; self.order_state=order_state; self.ticker=ticker; self.protect_ok=protect_ok
        self.reject_creates=reject_creates; self.fallback=fallback; self.close_ok=close_ok
        self.clock=0.0

def run(sc):
    ex=FakeEx(sc)
    ex_=AL.AlphaLiveExecutor()
    ex_._exchange=ex
    ex_._ensure=lambda: ex
    ex_.market=lambda cs: {"info":{"tickSz":"0.01"},"precision":{"price":0.01}}
    ex_._ensure_leverage=lambda cs,lv: None
    ex_.account=lambda: {"free":1000.0,"total":1000.0}
    ex_.pos_mode=lambda: "long_short_mode"
    ex_._contracts=lambda cs,notional,px: round(notional/px,6)
    ex_.amend_protection=lambda *a,**k: ({"verified":True,"count":2} if sc.protect_ok else {"verified":False,"error":"SIM_PROTECT_FAIL"})
    ex_.close=lambda *a,**k: {"flat_confirmed":sc.close_ok}
    ex_.wait_order_terminal=lambda symbol,oid,timeout=8.0: ex.fetch_order(oid,None)
    ex_.order_by_client_id=lambda symbol,clid: None
    market_called={"v":False}
    def fake_open(*a,**k):
        market_called["v"]=True
        return {"filled":1.0,"average":100.0,"tp":101.0,"sl":99.0,"order_id":"MKT","client_order_id":clid,"maker":False,"status":"closed","notional_usdt":100.0}
    ex_.open=fake_open
    # 控制时钟：sleep 推进；每次轮询 2s；time.time 也读假时钟，避免真实等待
    import alpha_live as L
    real_sleep=L.time.sleep; real_time_fn=L.time.time
    def fake_sleep(s): sc.clock+=float(s)
    L.time.sleep=fake_sleep; L.time.time=lambda: sc.clock
    clid="AX"+"a"*28
    try:
        r=ex_.open_maker("BTC-USDT-SWAP","long",100.0,0.013,0.01,3,client_order_id=clid,
                         ttl=45.0,max_reprice=2,improve_bps=0.5,chase_bps=12.0,
                         fallback_to_market=sc.fallback,ref_price=100.0)
        return r,ex,market_called["v"],None
    except Exception as e:
        return None,ex,market_called["v"],str(e)
    finally:
        L.time.sleep=real_sleep; L.time.time=real_time_fn

results=[]
# A 立即全部成交
A=Scenario("A_立即成交", order_state=lambda t: (("closed",1.0,100.0) if t>=2 else ("open",0.0,0.0)),
           ticker=lambda t:(99.99,100.01,100.0))
# B 超时零成交
B=Scenario("B_超时不成交", order_state=lambda t:("open",0.0,0.0), ticker=lambda t:(99.99,100.01,100.0))
# C 首次被拒后重挂成交
C=Scenario("C_拒单重挂成交", order_state=lambda t: (("closed",1.0,100.0) if t>=2 else ("open",0.0,0.0)),
           ticker=lambda t:(99.99,100.01,100.0), reject_creates=1)
# D 部分成交（第4秒成交0.5）
D=Scenario("D_部分成交", order_state=lambda t:(("open",0.5,100.0) if t>=4 else ("open",0.0,0.0)),
           ticker=lambda t:(99.99,100.01,100.0))
# E 价格跑掉（第4秒拉到100.3，超过 chase 12bps）
E=Scenario("E_价格跑掉", order_state=lambda t:("open",0.0,0.0),
           ticker=lambda t:(100.29,100.31,100.30) if t>=4 else (99.99,100.01,100.0))
# F 成交但保护失败→应强平并抛错
F=Scenario("F_保护失败强平", order_state=lambda t:(("closed",1.0,100.0) if t>=2 else ("open",0.0,0.0)),
           ticker=lambda t:(99.99,100.01,100.0), protect_ok=False)
# G 超时 + 回退市价
G=Scenario("G_超时回退市价", order_state=lambda t:("open",0.0,0.0), ticker=lambda t:(99.99,100.01,100.0), fallback=True)

for sc in [A,B,C,D,E,F,G]:
    r,ex,mkt,err=run(sc)
    results.append((sc.name,r,ex.cancels,ex.creates,mkt,err))

print("="*70)
allok=True
for name,r,cancels,creates,mkt,err in results:
    if name=="A_立即成交":
        ok=bool(r and r.get("filled")==1.0 and r.get("maker") and r.get("tp") and r.get("sl") and not cancels)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: filled={r and r.get('filled')} 撤单={cancels}")
    elif name=="B_超时不成交":
        ok=bool(r and r.get("filled")==0 and r.get("maker_status")=="timeout" and cancels)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: status={r and r.get('maker_status')} 撤单={cancels}（应撤单不追）")
    elif name=="C_拒单重挂成交":
        ok=bool(r and r.get("filled")==1.0 and creates>=2)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: 提交次数={creates} filled={r and r.get('filled')}")
    elif name=="D_部分成交":
        ok=bool(r and abs(r.get("filled",0)-0.5)<1e-9 and r.get("partial_entry") and cancels)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: filled={r and r.get('filled')} partial={r and r.get('partial_entry')} 撤余单={cancels}")
    elif name=="E_价格跑掉":
        ok=bool(r and r.get("filled")==0 and r.get("maker_status")=="ran_away" and cancels)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: status={r and r.get('maker_status')} 撤单={cancels}（应不追价）")
    elif name=="F_保护失败强平":
        ok=bool(err and "强制平仓" in err and r is None)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: 应抛错并强平 → {err}")
    elif name=="G_超时回退市价":
        ok=bool(r and mkt and r.get("order_id")=="MKT")
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: 回退市价={mkt} 结果order={r and r.get('order_id')}")
    allok=allok and ok
print("="*70)
print("✅ open_maker 状态机全部场景通过" if allok else "❌ 存在失败场景")
sys.exit(0 if allok else 1)
