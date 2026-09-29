from alpha_l2_replay import L2Replay,validate_l2
from alpha_smart_execution import plan,child_orders
from alpha_risk_kernel import var_cvar,drawdown,gate
from alpha_event_store import EventStore
from alpha_outbox import Outbox
import tempfile,os

def main():
    ev=[{'ts':1,'side':'bid','price':100,'size':5},{'ts':1,'side':'ask','price':101,'size':4},{'ts':2,'side':'bid','price':100,'size':0}]
    assert validate_l2(ev)['ok']; r=L2Replay(ev).run(); assert r[-1]['top']['ask']==101
    assert len(L2Replay(ev).fingerprint())==32
    p=plan(10000,100,100000,4,.01,slices=5); assert p['planned_notional']>0 and len(child_orders(p,'buy'))==5
    assert var_cvar([-0.1,-0.02,0.01,.03],.95)['cvar']>=0; assert drawdown([100,110,99])>0
    assert not gate([100,90],[-.1,-.05],1.2)['ok']
    f=tempfile.mktemp(); s=EventStore(f); s.append('A',{'x':1},'id1'); s.append('A',{'x':1},'id1'); assert s.verify()['events']==1 and s.verify()['ok']; os.unlink(f)
    o=Outbox(tempfile.mktemp()); k=o.key({'a':1}); assert o.put('ORDER',{'a':1},k); assert not o.put('ORDER',{'a':1},k); assert len(o.pending())==1; os.unlink(o.path)
    print('ALPHA-X INSTITUTIONAL MAX SELF-TEST PASS')
if __name__=='__main__':main()
