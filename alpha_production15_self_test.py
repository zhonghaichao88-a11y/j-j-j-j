from pathlib import Path
from alpha_production15 import *

def run():
    d=DataContract('OKX','BTC-USDT-SWAP','books5')
    assert d.validate({'ts_exchange':1,'seq':2})['ok']
    assert not d.validate({'seq':2})['ok']
    p=Path('/tmp/alpha15_fence.json'); p.unlink(missing_ok=True)
    f=EpochFence(p); t1=f.issue('a',now=100); assert f.authorize(t1,100)
    t2=f.issue('b',now=101); assert not f.authorize(t1,101); assert f.authorize(t2,101)
    m=make_manifest({'m':1},{'d':2},{'c':3},now=100); assert len(m.digest())==32
    assert canary_gate15(data_ok=True,risk_ok=True,reconcile_ok=True,execution_ok=True,model_approved=True,capacity_ok=True)['ready']
    assert not canary_gate15(data_ok=True,risk_ok=True,reconcile_ok=True,execution_ok=True,model_approved=False,capacity_ok=True)['ready']
    assert production_gate15(market_data_ok=True,clock_ok=True,risk_ok=True,reconciliation_ok=True,execution_ok=True,ha_ok=True,audit_ok=True,recovery_ok=True,model_ok=True)['ready']
    assert not production_gate15(market_data_ok=True,clock_ok=True,risk_ok=True,reconciliation_ok=True,execution_ok=True,ha_ok=True,audit_ok=True,recovery_ok=True,model_ok=True,kill_switch=True)['ready']
    assert recovery_plan(exchange_reachable=True,rest_truth=True,ws_fresh=True,local_state_consistent=True,open_orders_known=True)['safe_to_resume']
    h=immutable_event_hash('0',{'a':1}); assert h != immutable_event_hash('0',{'a':2})
    print('ALPHA-X Institutional 15.0 self-test: PASS')
if __name__=='__main__': run()
