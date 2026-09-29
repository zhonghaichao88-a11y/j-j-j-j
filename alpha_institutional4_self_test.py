from alpha_execution_calibration import calibrate,predict
from alpha_execution_router import choose
from alpha_event_replay import replay,chain
from alpha_institutional4 import execution_readiness,recovery_report

fills=[{'slippage_bps':1+i*.1,'participation':.01+i*.001,'volatility':.001} for i in range(20)]
c=calibrate(fills); assert c['ok'] and c['impact_slope_bps']>0
assert predict(.05,.001,2,c)>0
r=choose(100,10000,2,.001,.5,.1); assert r['style'] in {'PASSIVE_LIMIT','POV','TWAP','URGENT'}
events=[{'seq':1,'type':'ORDER_SUBMITTED','key':'A','status':'submitted'}, {'seq':2,'type':'ORDER_FILLED','key':'A','fillPx':100}, {'seq':3,'type':'POSITION_UPSERT','key':'BTC','qty':1}, {'seq':4,'type':'POSITION_FLAT','key':'BTC'}]
st=replay(events); assert st['last_seq']==4 and 'BTC' not in st['positions']
assert len(chain(events))==4
x=execution_readiness(fills,100,10000,2,.001); assert x['calibration']['n']==20
assert recovery_report(events)['event_count']==4
print('INSTITUTIONAL 4.0 SELF-TEST: PASS')
