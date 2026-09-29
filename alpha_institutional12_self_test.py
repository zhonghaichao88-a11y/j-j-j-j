from alpha_market_data_gateway import SequenceGuard, Watermark, make_event
from alpha_capacity import CapacityModel, canary_gate
from alpha_venue_router import Quote, route
from alpha_institutional12 import readiness

def run():
    e=make_event("OKX","trades","BTC-USDT-SWAP",1000,1,"trade",{"px":"100"},1001)
    assert e.event_key and e.payload_hash
    s=SequenceGuard(); assert s.accept("x",1)["ok"]; assert s.accept("x",3)["ok"] is False
    w=Watermark(20); assert w.update(1000,1010)["ok"]
    c=CapacityModel(1_000_000,0.05,5); assert c.estimate(10_000)["within_capacity"]
    g=canary_gate(shadow_ok=True,reconciliation_ok=True,execution_grade="A",max_notional_usd=10000,requested_notional_usd=5000); assert g["ready"]
    r=route([Quote("OKX",99,101,2,5,1),Quote("X",98,102,1,2,2)],"buy"); assert r["ok"]
    assert readiness(market_data_ok=True,clock_ok=True,risk_ok=True,reconciliation_ok=True,canary_ok=True,queue_depth=0)["ready"]
    return "PASS"
if __name__=="__main__": print(run())
