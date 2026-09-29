"""Offline exchange scenarios: no credentials, sockets or real orders."""
import ast, math, time, types, uuid, threading, unittest
from unittest.mock import Mock
from pathlib import Path
from typing import Optional
import numpy as np
import alpha_fast_v7 as strategy
from alpha_v7_feed import tf_ms, higher_tfs
from test_v7_repairs import load_functions
ROOT=Path(__file__).resolve().parents[1]

def executor():
    tree=ast.parse((ROOT/'alpha_live.py').read_text())
    methods={'_fee_cost','_normalize_order','_cancel_confirm_entry','open_maker','_entry_protection','open'}
    klass=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='AlphaLiveExecutor')
    klass.body=[n for n in klass.body if isinstance(n,ast.FunctionDef) and n.name in methods]
    ns={'Optional':Optional,'uuid':uuid,'time':types.SimpleNamespace(time=time.time,sleep=lambda _:None),'logger':Mock(), 'config':types.SimpleNamespace(trading=types.SimpleNamespace(margin_mode='isolated')),'symbol_to_ccxt':lambda s,t:'BTC/USDT:USDT'}
    load_functions('alpha_live.py',['_safe_num'],ns)
    exec(compile(ast.Module(body=[klass],type_ignores=[]),'alpha_live.py','exec'),ns)
    a=ns['AlphaLiveExecutor']();a.PREFIX='AX';a.TAG='ALPHAX'
    ex=Mock();ex.fetch_ticker.return_value={'bid':99,'ask':101,'last':100};ex.price_to_precision.side_effect=lambda s,p:str(p);ex.create_order.return_value={'id':'order1'}
    a._ensure=lambda:ex;a.market=lambda _: {'contractSize':.1,'info':{'tickSz':'.01'}};a._ensure_leverage=Mock();a.account=lambda:{'free':1000,'total':1000};a.pos_mode=lambda:'net_mode';a._contracts=lambda *args:10
    a.wait_order_terminal=Mock(return_value={'status':'canceled','filled':0,'average':0});a.order_by_client_id=Mock(return_value={});a.amend_protection=Mock(return_value={'verified':True});a.close=Mock(return_value={'flat_confirmed':True})
    return a,ex

class ExecutionTests(unittest.TestCase):
    def test_fee_dict(self):
        a,_=executor();self.assertEqual(a._normalize_order({'fee':{'cost':'0.125','currency':'USDT'}})['fee'],.125)
    def test_fee_array(self):
        a,_=executor();self.assertAlmostEqual(a._fee_cost({'fees':[{'cost':'.1'},{'cost':'.2'}]}),.3)
    def test_native_fee_sign_and_null_info(self):
        a,_=executor();self.assertEqual(a._fee_cost({'info':{'fee':'-.2'}}),.2);self.assertEqual(a._fee_cost({'info':None}),0)
    def test_native_order_normalized(self):
        a,_=executor();r=a._normalize_order({'state':'partially_filled','accFillSz':'2','avgPx':'100'});self.assertEqual((r['status'],r['filled'],r['average']),('open',2,100))
    def test_cancel_uncertain_blocks_fallback(self):
        a,e=executor();e.cancel_order.side_effect=TimeoutError();a.wait_order_terminal.return_value={'status':'open','filled':0};a.open=Mock()
        with self.assertRaises(RuntimeError) as c:a.open_maker('BTC-USDT-SWAP','long',100,.02,.01,1,client_order_id='AXtest',ttl=0,fallback_to_market=True)
        self.assertEqual(c.exception.client_order_id,'AXtest');a.open.assert_not_called();self.assertEqual(e.create_order.call_count,1)
    def test_confirmed_empty_cancel_allows_distinct_market_child(self):
        a,e=executor();a.open=Mock(return_value={'filled':1});a.open_maker('BTC-USDT-SWAP','long',100,.02,.01,1,client_order_id='AXtest',ttl=0,fallback_to_market=True)
        self.assertEqual(a.open.call_args.kwargs['client_order_id'],'AXtestM1')
    def test_market_child_timeout_keeps_child_identity(self):
        a,e=executor();a.open=Mock(side_effect=TimeoutError('unknown'))
        with self.assertRaises(TimeoutError) as c:a.open_maker('BTC-USDT-SWAP','long',100,.02,.01,1,client_order_id='AXtest',ttl=0,fallback_to_market=True)
        self.assertEqual(c.exception.client_order_id,'AXtestM1')
    def test_submit_timeout_never_resubmits(self):
        a,e=executor();e.create_order.side_effect=TimeoutError();a.open=Mock()
        with self.assertRaises(RuntimeError):a.open_maker('BTC-USDT-SWAP','long',100,.02,.01,1,client_order_id='AXtest',ttl=0,fallback_to_market=True)
        self.assertEqual(e.create_order.call_count,1);a.open.assert_not_called()
    def test_partial_cancel_books_only_filled(self):
        a,e=executor();a.wait_order_terminal.return_value={'status':'canceled','filled':2,'average':100};a.open=Mock()
        r=a.open_maker('BTC-USDT-SWAP','long',100,.02,.01,1,client_order_id='AXtest',ttl=0,fallback_to_market=True)
        self.assertEqual(r['filled'],2);self.assertEqual(r['notional_usdt'],20);self.assertTrue(r['partial_entry']);a.open.assert_not_called()
    def test_partial_missing_average_blocks_market(self):
        a,e=executor();a.wait_order_terminal.return_value={'status':'canceled','filled':2,'average':0};a.open=Mock()
        with self.assertRaises(RuntimeError):a.open_maker('BTC-USDT-SWAP','long',100,.02,.01,1,client_order_id='AXtest',ttl=0,fallback_to_market=True)
        a.open.assert_not_called()
    def test_market_open_partial_nonterminal_not_booked(self):
        a,e=executor();a.wait_order_terminal.return_value={'status':'open','filled':2,'average':100}
        with self.assertRaises(RuntimeError):a.open('BTC-USDT-SWAP','long',100,.02,.01,1,client_order_id='AXtest')
        a.amend_protection.assert_not_called()
    def test_market_canceled_partial_booked(self):
        a,e=executor();a.wait_order_terminal.return_value={'status':'canceled','filled':2,'average':100};r=a.open('BTC-USDT-SWAP','long',100,.02,.01,1,client_order_id='AXtest');self.assertEqual(r['notional_usdt'],20)

class ChartDataTests(unittest.TestCase):
    def test_bars_dedup_sort_and_pagination(self):
        ex=Mock();ex.request.return_value={'code':'0','data':[['600000','10','12','9','11','3','0','0','0'],['300000','9','11','8','10','2','0','0','1'],['300000','9','11','8','10','2','0','0','1']]}
        ns={'math':math,'re':__import__('re'),'time':time,'LOCK':threading.RLock(),'CACHE':{},'TF':{'5m':('5m',300000)},'exchange':lambda:ex}
        load_functions('tv_plus.py',['symbol_ok','bars'],ns);r=ns['bars']('BTC-USDT-SWAP','5m');self.assertEqual([x['timestamp'] for x in r],[300000,600000]);self.assertFalse(r[-1]['confirmed']);self.assertEqual(len(ns['bars']('BTC-USDT-SWAP','5m',600000)),1)
    def test_alerts(self):
        ns={'np':np,'strategy':strategy};load_functions('tv_plus.py',['check_alert'],ns);self.assertTrue(ns['check_alert']({'kind':'above','price':100},101));self.assertFalse(ns['check_alert']({'kind':'below','price':100},101))
    def test_backtest_no_future_bars_and_costs(self):
        seen=[]
        def decide(sym,data,p):
            self.assertLess(max(data['frames']['5m']['ts']),data['as_of_ms']);seen.append(data['as_of_ms'])
            return {'signal':'LONG','fast_strategy':{'sl_price':99,'tp_price':102},'sl':.01,'tp':.02}
        fake=types.SimpleNamespace(validate_params=strategy.validate_params,decide=decide,position_meta=lambda _:{},exit_plan=lambda *a,**k:{'close':'','stop':None})
        ns={'strategy':fake,'np':np,'tf_ms':tf_ms,'higher_tfs':higher_tfs};load_functions('alpha_v7_replay.py',['simulate'],ns)
        rows=[dict(timestamp=i*300000,open=100,high=103,low=98,close=100,volume=1,confirmed=True) for i in range(318)]
        result=ns['simulate'](rows,{},10000)
        self.assertEqual(len(seen),30);self.assertTrue(all(t['exit']==99 and t['pnl']<0 for t in result['trades']));self.assertLess(result['summary']['净收益'],0)
        rows[50]['timestamp']+=1
        with self.assertRaises(ValueError):ns['simulate'](rows,{},10000)

if __name__=='__main__':unittest.main()
