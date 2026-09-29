from alpha_institutional_data import validate_bars,replay_fingerprint
from alpha_market_impact import estimate,capacity
from alpha_portfolio import allocate,concentration
from alpha_event_journal import EventJournal
import tempfile,os

def main():
    rows=[{'ts':i*900000,'open':100,'high':101,'low':99,'close':100.5,'volume':10} for i in range(20)]
    assert validate_bars(rows)['ok']; assert len(replay_fingerprint(rows))==24
    x=estimate(1000,100000,4,.01); assert x['all_in_cost_bps']>0
    assert len(capacity([100,1000],100000,4,.01,20))==2
    w=allocate({'BTC':2,'ETH':1},{'BTC':.02,'ETH':.04}); assert abs(sum(w.values())-1)<1e-9
    assert concentration(w)['effective_positions']>1
    p=tempfile.mktemp(); j=EventJournal(p); j.append('TEST',{'x':1}); j.append('TEST2',{'x':2}); assert j.verify()['ok']; os.unlink(p)
    print('ALPHA-X INSTITUTIONAL 1.0 SELF-TEST PASS')
if __name__=='__main__':main()
