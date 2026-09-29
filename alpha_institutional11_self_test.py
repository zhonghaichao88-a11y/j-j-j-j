from pathlib import Path
import tempfile
from alpha_institutional11 import *


def test_normalize():
    x=normalize_fill({'instId':'BTC-USDT-SWAP','ordId':'1','tradeId':'7','fillTime':'1000','fillPx':'100','fillSz':'2','fee':'-0.1'})
    assert x['fill_px']==100 and x['fill_sz']==2 and x['trade_id']=='7'
    assert len(dedupe_fills([{'ordId':'1','tradeId':'7','fillTime':1000,'fillPx':100,'fillSz':2}]*2))==1
    b=normalize_bill({'billId':'9','ts':'1000','pnl':'2','fee':'-0.1'})
    assert b['bill_id']=='9' and b['pnl']==2


def test_reconcile():
    x=reconcile_orders([{'ordId':'1','state':'live'}],[{'ordId':'1','state':'filled','tradeId':'9'}])
    assert x['count']==1 and x['orders'][0]['state']=='filled' and x['orders'][0]['terminal']


def test_clock():
    g=ClockGuard(max_skew_ms=100)
    assert g.check(1000,1050)['ok']
    assert not g.check(1000,1201)['ok']


def test_lease():
    with tempfile.TemporaryDirectory() as d:
        p=Path(d)/'lease.json'; a=Lease(p,ttl_sec=10,owner='a'); b=Lease(p,ttl_sec=10,owner='b')
        assert a.acquire(now=100); assert not b.acquire(now=105); assert b.acquire(now=111)


def test_config():
    assert not production_config_check({'ALPHA_BIND_HOST':'0.0.0.0','ALPHA_LIVE_ALLOWED':'1','ALPHA_LIVE_API_KEY':'short','OKX_DEMO_MODE':'1','ALPHA_AUTOPROMOTE':'1'})['ok']
    assert production_config_check({'ALPHA_BIND_HOST':'127.0.0.1','ALPHA_LIVE_ALLOWED':'0'})['ok']


def test_slo():
    assert slo_snapshot(ws_age_sec=1,reconcile_age_sec=1,clock_ok=True,breaker_open=False,queue_depth=2)['ready']
    assert not slo_snapshot(ws_age_sec=99,reconcile_age_sec=1,clock_ok=True,breaker_open=False,queue_depth=2)['ready']

if __name__=='__main__':
    tests=[test_normalize,test_reconcile,test_clock,test_lease,test_config,test_slo]
    for t in tests: t()
    print('ALPHA-X Institutional 11.0 self-test: PASS (%d/%d)'%(len(tests),len(tests)))
