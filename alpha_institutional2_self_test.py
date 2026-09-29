from alpha_institutional2 import ClockGuard,TokenBucket,FailureBreaker,portfolio_stats,impact_cost_bps,readiness

def main():
    c=ClockGuard(100); assert c.update(1000100,1000000)['ok']; assert not c.update(1000201,1000000)['ok']
    b=TokenBucket(100,2); assert b.allow() and b.allow() and not b.allow()
    br=FailureBreaker(2,60); br.failure(); assert not br.is_open(); br.failure(); assert br.is_open(); br.success(); assert not br.is_open()
    s=portfolio_stats({'BTC':.5,'ETH':.5},{'BTC':[.01,-.01,.02],'ETH':[.015,-.005,.01]}); assert s['portfolio_vol']>=0
    x=impact_cost_bps(10000,100000,.5,.01); assert x['participation']==.1 and x['all_in_bps']>=.5
    assert readiness(True,True,True,True,True)['ready']; assert not readiness(True,False,True,True,True)['ready']
    print('ALPHA-X INSTITUTIONAL 2.0 SELF-TEST PASS')
if __name__=='__main__': main()
