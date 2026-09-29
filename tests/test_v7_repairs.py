"""离线回归：不读取 .env，不连接交易所，不启动交易。python -m unittest discover -s tests -v"""
import ast
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import alpha_fast_v7 as v
import launch_v7


def load_functions(filename, names, ns):
    tree=ast.parse((ROOT/filename).read_text(encoding='utf-8-sig'))
    nodes=[]
    for n in tree.body:
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names:
            n.decorator_list=[]
            nodes.append(n)
    assert len(nodes)==len(names), names
    exec(compile(ast.Module(body=nodes,type_ignores=[]),filename,'exec'),ns)
    return ns


class HTTPError(Exception):
    def __init__(self,status_code,detail):
        self.status_code=status_code
        super().__init__(detail)


def frame():
    c=np.ones(100)*100
    return {'ts':np.arange(100)*300000,'open':c.copy(),'close':c.copy(),
            'high':c+1,'low':c-1,'volume':np.ones(100)}


class StrategyTests(unittest.TestCase):
    def setUp(self): v._RUNTIME={}
    def tearDown(self): v._RUNTIME={}
    def test_bad_params_rejected_atomically(self):
        v.set_runtime_params({'don_n':7})
        before=v.get_runtime_params()
        for opts in ({'ema_fast':-1},{'don_n':0},{'rr':float('nan')},
                     {'rsi_ovs':80},{'ema_slow':2},{'sl_fixed':None},
                     {'don_n':1.5},{'strategy':'unknown'}):
            with self.assertRaises(ValueError): v.set_runtime_params(opts)
            self.assertEqual(before,v.get_runtime_params())
    def test_donchian_metadata_locked(self):
        p=v.position_meta({'fast_strategy':{'engine_version':'v7','v7_don_n':7,'bar_ts':123}})
        v.set_runtime_params({'don_n':30})
        self.assertEqual(p['v7_don_n'],7)
        self.assertEqual(p['v7_bar_ts'],123)
    def test_donchian_only_post_entry_closed_bars(self):
        p={'entry':100,'side':'long','opened_at':600,'v7_engine':'donchian','v7_level':100,'v7_max_seconds':3600}
        f={'5m':{'ts':[0,300,600,900], 'close':[99]*4}}
        f['5m']['ts']=[t*1000 for t in f['5m']['ts']]
        self.assertFalse(v.exit_plan(p,99,1199,f)['close'])
        self.assertIn('突破失败',v.exit_plan(p,99,1200,f)['close'])
    def test_donchian_short_invalidation(self):
        p={'entry':100,'side':'short','opened_at':0,'v7_engine':'donchian','v7_level':100,'v7_max_seconds':3600}
        f={'5m':{'ts':[0,300000], 'close':[101,102]}}
        self.assertIn('突破失败',v.exit_plan(p,101,600,f)['close'])
    def test_boll_uses_previous_band(self):
        f=frame();f['close'][-2:]=[97,100];f['open'][-2:]=[97,99];f['low'][-2:]=[96,98]
        d={'frames':{'5m':f},'ticker_last':100,'missing':[],'as_of_ms':30000000}
        # Previous lower band 98: 97 is outside. Current lower band 95: 97 is inside.
        with patch.object(v,'boll_last',side_effect=[(100,105,95),(100,102,98)]):
            r=v.decide('X',d,{'strategy':'boll_revert','sl_mode':'fixed','sl_fixed':.01})
        self.assertEqual(r['signal'],'LONG')
    def test_risk_measured_from_current_price(self):
        f=frame(); f['high'][:-1]=100.1;f['close'][-1]=101;f['high'][-1]=102
        d={'frames':{'5m':f},'ticker_last':101.2,'missing':[],'as_of_ms':30000000}
        r=v.decide('X',d,{'strategy':'donchian','sl_mode':'fixed','sl_fixed':.01})
        self.assertEqual(r['signal'],'LONG')
        self.assertAlmostEqual(r['sl'],(101.2-99.99)/101.2)
        self.assertEqual(r['fast_strategy']['reference_price'],101)
    def test_unfinished_bar_rejected(self):
        self.assertIn('未收盘',v.validate_5m({'5m':frame()},29999999))


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.e=types.ModuleType('alpha_engine');self.e.LOCK=threading.RLock();self.e.CONTROL_LOCK=threading.RLock()
        self.e.LOOP_THREAD=None
        self.e.STATE={'mode':'paper','running':False,'positions':{},'consecutive_losses':5,'day_start_equity':123}
        self.e._persist=lambda:None
        self.mutations=[]
        self.e.set_fast_engine=lambda x:self.mutations.append(x)
        self.e.start=lambda x:False
        self.mods=patch.dict(sys.modules,{'alpha_engine':self.e});self.mods.start()
        self.ns=load_functions('tv_bridge.py',['tv_reset','tv_start','tv_set_params'],{
            'HTTPException':HTTPError,'TVStartRequest':object,'TVParamsRequest':object,
            'time':__import__('time'),'math':__import__('math'),'os':__import__('os'),
            'alpha_fast_v7':v,'tv_universe':types.SimpleNamespace(configure=lambda x:None)})
        self.req=types.SimpleNamespace(live=False,leverage=3,max_positions=5,risk_pct=.02,top=20,params=None)
    def tearDown(self):self.mods.stop();v._RUNTIME={}
    def test_reset_cannot_clear_live(self):
        self.e.STATE.update(mode='live',positions={'X':{'live':True}})
        with self.assertRaises(HTTPError):self.ns['tv_reset']()
        self.assertIn('X',self.e.STATE['positions'])
    def test_reset_checks_position_flag_too(self):
        self.e.STATE['positions']={'X':{'live':True}}
        with self.assertRaises(HTTPError):self.ns['tv_reset']()
    def test_reset_clears_paper_risk_state(self):
        self.assertTrue(self.ns['tv_reset']()['success'])
        self.assertEqual(self.e.STATE['consecutive_losses'],0)
        self.assertEqual(self.e.STATE['day_start_equity'],10000)
        self.assertEqual(self.e.STATE['v7_attempted'],{})
    def test_running_start_never_mutates_engine(self):
        self.e.STATE['running']=True
        with self.assertRaises(HTTPError) as cm:self.ns['tv_start'](self.req)
        self.assertEqual(cm.exception.status_code,409);self.assertEqual(self.mutations,[])
    def test_start_false_not_success(self):
        with self.assertRaises(HTTPError): self.ns['tv_start'](self.req)
    def test_switch_with_position_rejected(self):
        self.e.STATE.update(mode='live',positions={'X':{'live':True}})
        with self.assertRaises(HTTPError): self.ns['tv_start'](self.req)
        self.assertEqual(self.mutations,[])
    def test_params_api_returns_400(self):
        with self.assertRaises(HTTPError) as cm:
            self.ns['tv_set_params'](types.SimpleNamespace(params={'don_n':0},top=None))
        self.assertEqual(cm.exception.status_code,400)


class EngineTests(unittest.TestCase):
    def test_restart_restores_dedupe(self):
        tree=ast.parse((ROOT/'alpha_engine.py').read_text())
        state=next(n.value for n in tree.body if isinstance(n,ast.Assign) and any(getattr(t,'id','')=='STATE' for t in n.targets))
        keys=[k.value for k in state.keys if isinstance(k,ast.Constant)]
        with tempfile.TemporaryDirectory() as td:
            f=Path(td)/'s.json';f.write_text(json.dumps({'v7_attempted':{'X':'bar1'}}))
            ns=load_functions('alpha_engine.py',['_load_state'],{'STATE':dict.fromkeys(keys),'LOCK':threading.RLock(),'STATE_FILE':f,'json':json})
            ns['_load_state']();self.assertEqual(ns['STATE']['v7_attempted'],{'X':'bar1'})
    def test_same_bar_guard_blocks_after_restore(self):
        ns=load_functions('alpha_engine.py',['_v7_entry_guard'],{'STATE':{'v7_attempted':{'X':'bar1'}},'_activity':lambda *a:None})
        self.assertFalse(ns['_v7_entry_guard']('X',{'signal':'LONG','fast_strategy':{'engine_version':'v7','signal_id':'bar1'}},100,0))
    def test_fill_aggregation_and_missing_data(self):
        trades=[{'id':'1','timestamp':1000,'side':'sell','amount':3,'price':110},
                {'id':'2','timestamp':2000,'side':'sell','amount':7,'price':120}]
        ns=load_functions('alpha_engine.py',['_exchange_close_fill'],{
            'okx_client':types.SimpleNamespace(_exchange=types.SimpleNamespace(fetch_my_trades=lambda *a,**k:trades)),
            'config':types.SimpleNamespace(trading=types.SimpleNamespace(get_ccxt_symbol=lambda s:s)),
            'logger':types.SimpleNamespace(debug=lambda *a:None)})
        p={'opened_at':1,'side':'long','filled':10,'fast_version':'v7'}
        r=ns['_exchange_close_fill']('X',p);self.assertEqual(r['price'],117);self.assertEqual(r['qty'],10)
        trades.pop();self.assertEqual(ns['_exchange_close_fill']('X',p)['qty'],0)
        trades.clear();self.assertEqual(ns['_exchange_close_fill']('X',p)['price'],0)
    def test_worker_checks_before_restart(self):
        ns=load_functions('alpha_engine.py',['start'],{
            'STATE':{'running':False},'LOCK':threading.RLock(),
            'LOOP_THREAD':types.SimpleNamespace(is_alive=lambda:True)})
        with self.assertRaises(RuntimeError):ns['start']({})
    def test_no_phantom_close_without_fills(self):
        ns=load_functions('alpha_engine.py',['_finalize_stopped_live_closes'],{
            'STATE':{'positions':{'X':{'live':True,'side':'long','entry':100,'filled':1}},'position_missing_counts':{'X':2}},
            'LOCK':threading.RLock(),'alpha_live':types.SimpleNamespace(positions=lambda:[],pos_mode=lambda:'net_mode'),
            'config':types.SimpleNamespace(trading=types.SimpleNamespace(get_ccxt_symbol=lambda s:s)),
            '_flat_cleanup_or_block':lambda *a:True,'_exchange_close_fill':lambda *a:{},
            '_activity':lambda *a:None,'_persist':lambda:None})
        ns['_finalize_stopped_live_closes'](max_rounds=1)
        self.assertNotIn('X',ns['STATE']['positions'])
        self.assertIn('X',ns['STATE']['flat_fill_pending'])
        self.assertIn('成交明细等待对账',ns['STATE']['live_error'])

    def test_flat_cleanup_blocks_and_retry_unlocks(self):
        state={'positions':{},'flat_cleanup_pending':{},'protection_blocks':{}}
        failures=[True]
        def cleanup(*args):
            if failures[0]:raise RuntimeError('移动单仍有效')
        ns=load_functions('alpha_engine.py',['_flat_cleanup_or_block','_retry_flat_cleanup'],{
            'STATE':state,'LOCK':threading.RLock(),'time':__import__('time'),
            '_cleanup_flat_position_algos':cleanup,'_activity':lambda *a:None,'_persist':lambda:None,
            'config':types.SimpleNamespace(trading=types.SimpleNamespace(get_ccxt_symbol=lambda s:s)),
            'alpha_live':types.SimpleNamespace(positions=lambda:[])})
        self.assertFalse(ns['_flat_cleanup_or_block']('X',{'side':'long'}))
        self.assertIn('X',state['flat_cleanup_pending']);self.assertIn('X',state['protection_blocks'])
        failures[0]=False;ns['_retry_flat_cleanup']()
        self.assertNotIn('X',state['flat_cleanup_pending']);self.assertNotIn('X',state['protection_blocks'])


class DataTests(unittest.TestCase):
    def test_v7_fetch_only_requests_5m(self):
        calls=[]
        ex=types.SimpleNamespace(fetch_ohlcv=lambda s,tf,**k:(calls.append(tf) or [[0,1,2,.5,1,1]]))
        import concurrent.futures
        ns=load_functions('alpha_fast_mode.py',['_fetch_symbol'],{
            'Dict':dict,'Any':object,'ThreadPoolExecutor':concurrent.futures.ThreadPoolExecutor,
            'as_completed':concurrent.futures.as_completed,'np':np,
            'okx_client':types.SimpleNamespace(is_connected=True,_exchange=ex,get_ticker=lambda s:{'last':1}),
            'config':types.SimpleNamespace(trading=types.SimpleNamespace(get_ccxt_symbol=lambda s:s)),
            'FRAME_LIMIT':{'5m':120,'15m':120,'1h':100,'4h':80},'FRAME_TTL':{},'MARKET_SYNC_STATE':{},
            '_cached_fetch':lambda key,ttl,fn:fn(),'_bar_frame':lambda rows,tf,limit=None:{'ts':np.array([0]),'close':np.array([1])} if rows else {},
            '_finite':lambda x,default:default if x is None else float(x)})
        r=ns['_fetch_symbol']('X',only_5m=True)
        self.assertEqual(calls,['5m']);self.assertEqual(r['missing'],[])


class LauncherTests(unittest.TestCase):
    def test_ready_opens_only_matching_service(self):
        process=types.SimpleNamespace(poll=lambda:None)
        opener=types.SimpleNamespace(open=lambda *a,**k:io.BytesIO(json.dumps({'ready':True,'launch_id':'new'}).encode()))
        self.assertTrue(launch_v7.wait_ready(process,'http://127.0.0.1:1234/tv','new',opener=opener))
    def test_wrong_service_not_ready(self):
        opener=types.SimpleNamespace(open=lambda *a,**k:io.BytesIO(b'{"ready":true,"launch_id":"old"}'))
        self.assertFalse(launch_v7.wait_ready(types.SimpleNamespace(poll=lambda:None),'http://localhost/tv','new',timeout=.01,opener=opener))
    def test_failed_process_never_ready(self):
        self.assertFalse(launch_v7.wait_ready(types.SimpleNamespace(poll=lambda:1),'http://localhost/tv','new'))


if __name__=='__main__':unittest.main()
