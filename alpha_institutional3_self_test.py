from alpha_l2_replay import validate_l2,L2Replay
from alpha_orderbook_sim import replay_market
from alpha_portfolio_optimizer import optimize,portfolio_stats
from alpha_research_stats import bootstrap_mean,multiple_testing_adjust,promotion_gate
from alpha_institutional3 import readiness,research_gate

events=[{'ts':1,'side':'bid','price':99,'size':10},{'ts':1,'side':'ask','price':101,'size':10},{'ts':2,'side':'bid','price':99.5,'size':8},{'ts':2,'side':'ask','price':100.5,'size':8}]
assert validate_l2(events)['ok']; assert len(L2Replay(events).run())==4
fills=replay_market(events,[{'ts':2,'side':'buy','qty':2,'participation':.05}]); assert fills and fills[0]['qty']>0
cov={'BTC':{'BTC':.04,'ETH':.01},'ETH':{'BTC':.01,'ETH':.09}}
w=optimize({'BTC':.02,'ETH':.01},cov,max_weight=.6); assert sum(w.values())<=1.00001
assert portfolio_stats(w,cov)['volatility']>=0
s=bootstrap_mean([.01,.02,.03,.00]); assert s['n']==4
assert len(multiple_testing_adjust([.01,.02,.5]))==3
assert promotion_gate({'n':120,'ci_low':.001,'mean':.003,'max_drawdown':.1})['decision']=='PROMOTION_REVIEW'
r=readiness(events,{'BTC':.02,'ETH':.01},cov); assert r['ready']
assert research_gate([.01,.02,.03])['n']==3
print('INSTITUTIONAL 3.0 SELF-TEST: PASS')
