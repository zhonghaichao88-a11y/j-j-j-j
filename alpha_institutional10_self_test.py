import os, tempfile
from pathlib import Path
from alpha_institutional10 import *

def main():
    q=execution_quality([{'fill_px':101,'qty':2,'latency_ms':10},{'fill_px':102,'qty':1,'latency_ms':20}],100,'buy')
    assert q['fills']==2 and q['implementation_shortfall_bps']>0
    p=pnl_attribution(100,105,2,'long',fee=1,funding=.2,arrival_px=99)
    assert p['net_pnl'] < p['gross_pnl']
    r=risk_budget({'BTC':.8,'ETH':.4},{'BTC':{'BTC':.1,'ETH':.02},'ETH':{'BTC':.02,'ETH':.2}},gross_limit=1,max_weight=.7,max_vol=.5)
    assert r['gross']<=1+1e-9
    assert readiness({'data':True,'risk':True,'recon':True},live=False)['allowed']
    os.environ['ALPHA_LIVE_ALLOWED']='0'; assert not readiness({'data':True},live=True)['allowed']
    with tempfile.TemporaryDirectory() as d:
        led=IntentLedger(Path(d)/'intent.jsonl'); i=Intent('BTC/USDT:USDT','long',1,'S','SIG',1.0); led.put(i); led.put(i); assert led.verify()['ok'] and led.verify()['intents']==1
    g=model_promotion_gate([.1,-.02]*60,[.2,-.01]*60,min_n=100); assert g['challenger_n']==120
    assert autonomous_policy(health_ok=False,drift_ok=True,risk_ok=True,execution_ok=True,reconciliation_ok=True)=='HALT_FAIL_CLOSED'
    print('ALPHA-X INSTITUTIONAL 10.0 SELF-TEST: PASS')
if __name__=='__main__':main()
