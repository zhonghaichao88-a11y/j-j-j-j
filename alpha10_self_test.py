import os, sys, types, tempfile
os.environ.setdefault('ALPHA_LIVE_ALLOWED','0')
if 'ccxt' not in sys.modules:
    m=types.ModuleType('ccxt'); m.okx=type('okx',(),{}); sys.modules['ccxt']=m
from alpha10_platform import returns_stats, bootstrap_mean_ci, multiple_testing_penalty, promotion_gate, cost_stress, capacity_curve, factor_attribution, Lifecycle, fail_closed, stable_id

def main():
    a=[1,2,-.5,1.5,-.2,2,1,-.3]*20
    b=[1.4,1.8,-.2,2.1,1.2,1.6,.8,-.1]*20
    assert returns_stats(b)['n']==160
    ci=bootstrap_mean_ci(b,iterations=300); assert ci['n']==160 and ci['high']>ci['low']
    assert multiple_testing_penalty(20)['adjusted_alpha']<.01
    g=promotion_gate(a,b,trials=1,min_n=100,min_sharpe=0.1); assert g['challenger']['n']==160
    assert len(cost_stress(b))==6 and len(capacity_curve(b))==4
    fa=factor_attribution([{'net_pnl':2,'factor_scores':{'trend':.8,'flow':-.2}},{'net_pnl':-1,'factor_scores':{'trend':.4}}])
    assert 'trend' in fa['factors']
    l=Lifecycle(stable_id('x'),'BTC'); l.transition('SHADOW'); l.transition('PROMOTION_REVIEW'); l.transition('CHAMPION'); l.transition('RETRAIN'); l.transition('CANDIDATE')
    assert fail_closed(data_ok=False,model_ok=True,risk_ok=True,execution_ok=True,reconciliation_ok=True)['allowed'] is False
    assert stable_id('a')==stable_id('a')
    print('ALPHA-X ULTRA MAX 10.0 SELF-TEST PASS')
if __name__=='__main__': main()
