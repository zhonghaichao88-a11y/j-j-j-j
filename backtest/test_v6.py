"""离线策略/执行边界测试；账户接口使用替身，不是交易所验证。"""
import ast, asyncio, copy, importlib.util, json, sys, threading, types, unittest
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT)); sys.path.insert(0,str(Path(__file__).parent))
import alpha_fast_v6 as v6
import alpha_fast_v6_data as vd
from v6_replay import synthetic, FrameSource, replay, metrics, resolve_bar

def extract(names,path=None,extra=None):
 tree=ast.parse((path or ROOT/'alpha_engine.py').read_text())
 nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
 assert len(nodes)==len(names)
 for n in nodes:n.decorator_list=[]
 ns={'fast_v6':v6,'time':types.SimpleNamespace(time=lambda:1000.),'LOCK':threading.RLock(),'STATE':{'positions':{},'live_trades':[],'paper_trades':[],'balance':10000.,'consecutive_losses':0},'_activity':Mock(),'_fast_detail':Mock(),'_persist':Mock(),'gate_enabled':lambda key:False}
 ns.update(extra or {});exec(compile(ast.Module(body=nodes,type_ignores=[]),'production-functions','exec'),ns);return ns

def fast_module(path,name):
 with patch.dict(sys.modules,{'loguru':types.SimpleNamespace(logger=Mock()),'config':types.SimpleNamespace(config=Mock()),'okx_client':types.SimpleNamespace(okx_client=Mock())}):
  spec=importlib.util.spec_from_file_location(name,path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
 return mod

class Tests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.df,_=synthetic('SOL',20);cls.src=FrameSource(cls.df);cls.samples={}
  for i in range(730,len(cls.df)-1):
   t=int(cls.df.open_ms.iloc[i])+300000;d=dict(frames=cls.src.at(t),ticker_last=float(cls.df.close.iloc[i]),as_of_ms=t,spread_bps=-1,missing=[],summary={});r=v6.decide('SOL',d)
   if r['signal']!='FLAT':cls.samples.setdefault((r['fast_strategy']['v6_engine'],r['signal']),(d,r))
   if len(cls.samples)==9:break
 def sample(self):return copy.deepcopy(next(iter(self.samples.values())))
 def test_all_engines_both_sides(self):
  # Quality gate is engine-specific: trend, breakout and range each has its own checks.
  self.assertEqual(set(self.samples),{(e,s) for e in ('V6_TREND','V6_BREAKOUT','V6_RANGE') for s in ('LONG','SHORT')})
  for d,r in self.samples.values():
   json.dumps(r,allow_nan=False);self.assertGreater(r['tp'],0);self.assertGreater(r['sl'],0);self.assertFalse(r['fast_strategy']['score_is_probability'])
 def test_legacy_research_engines_both_sides(self):
  samples=set()
  for i in range(730,len(self.df)-1):
   t=int(self.df.open_ms.iloc[i])+300000
   d=dict(frames=self.src.at(t),ticker_last=float(self.df.close.iloc[i]),as_of_ms=t,spread_bps=-1,missing=[],summary={})
   r=v6.decide('SOL',d,{'entry_policy':'legacy','min_risk_cost':0.})
   if r['signal']!='FLAT':samples.add((r['fast_strategy']['v6_engine'],r['signal']))
   if len(samples)==6:break
  self.assertEqual(len(samples),6)
 def test_unclosed_and_stale(self):
  d,r=self.sample();d['as_of_ms']-=1;self.assertIn('未收盘',v6.decide('X',d)['reason'])
  d,r=self.sample();d['as_of_ms']+=700000;self.assertIn('过期',v6.decide('X',d)['reason'])
 def test_bad_data_rejected(self):
  for k in ('ts','close'):
   d,r=self.sample();d['frames']['5m'][k][-1]=float('nan') if k=='close' else d['frames']['5m'][k][-2]
   self.assertEqual(v6.decide('X',d)['signal'],'FLAT')
 def test_no_future_leak(self):
  j=950;a=self.df.iloc[:j+1].copy();b=self.df.copy();b.loc[j+1:,'close']*=2;t=int(a.open_ms.iloc[-1])+300000
  np.testing.assert_array_equal(FrameSource(a).at(t)['1h']['close'],FrameSource(b).at(t)['1h']['close'])
 def test_cost_chase_spread(self):
  d,r=self.sample();self.assertEqual(v6.decide('X',d,{'fee_side':.1})['signal'],'FLAT')
  d['ticker_last']*=1.1;self.assertEqual(v6.decide('X',d)['signal'],'FLAT')
  d,r=self.sample();d['spread_bps']=50;self.assertIn('点差',v6.decide('X',d)['reason'])
 def test_flow_budget_once(self):
  d,r=self.sample();side=1 if r['signal']=='LONG' else -1;d['v6_micro']=dict(book_imbalance=side*.4,trade_imbalance=side*.4,book_confirmed=True,fresh=True)
  a=v6.decide('X',d);self.assertEqual(a['fast_entry_size_multiplier'],1.)
  d['v6_micro'].update(book_imbalance=-side*.4,trade_imbalance=-side*.4);b=v6.decide('X',d);self.assertEqual(b['fast_entry_size_multiplier'],.6);self.assertEqual(a['confidence'],b['confidence']);self.assertEqual(b['adaptive_context']['size_multiplier'],1.)
 def test_missing_micro_not_zero(self):
  vd._CACHE.clear();vd._HISTORY.clear();ex=Mock();ex.fetch_order_book.side_effect=RuntimeError('missing');ex.fetch_trades.return_value=[];ex.fetch_funding_rate.return_value={};ex.fetch_open_interest.return_value={};x=vd.snapshot(ex,'X',1000)
  self.assertIsNone(x['funding_rate']);self.assertIsNone(x['book_imbalance']);self.assertFalse(x['fresh'])
 def test_two_book_updates(self):
  vd._CACHE.clear();vd._HISTORY.clear();ex=Mock()
  for t in [1000,1006]:
   ex.fetch_order_book.return_value=dict(timestamp=t*1000,bids=[[99,10]],asks=[[101,2]])
   ex.fetch_trades.return_value=[dict(id=str(i),timestamp=t*1000,side='buy',price=100,amount=1) for i in range(5)]
   ex.fetch_funding_rate.return_value={'fundingRate':.0001};ex.fetch_open_interest.return_value={'openInterestAmount':100}
   x=vd.snapshot(ex,'X',t);self.assertEqual(x['book_confirmed'],t==1006)
  self.assertIsNone(x['oi_change_pct'])
 def test_gap_and_double_touch(self):
  p=dict(side='long',sl=95,tp=110);self.assertEqual(resolve_bar(p,100,112,94,101),(95,'结构止损'));self.assertEqual(resolve_bar(p,90,105,88,98),(90,'结构止损'))
  p=dict(side='short',sl=105,tp=90);self.assertEqual(resolve_bar(p,112,114,85,100),(112,'结构止损'))
 def test_exit_and_trail(self):
  p=dict(side='long',entry=100,sl=95,base_sl_pct=.05,opened_at=1000,v6_max_seconds=7200,v6_engine='V6_TREND')
  self.assertIsNone(v6.exit_plan(p,110,1200,trailing=False)['stop']);self.assertGreater(v6.exit_plan(p,110,1200,trailing=True)['stop'],95)
  p['sl']=109;self.assertIsNone(v6.exit_plan(p,110,1200,trailing=True)['stop']);self.assertIn('到期',v6.exit_plan(p,110,9000)['close'])
 def test_breakout_two_post_entry_bars(self):
  p=dict(side='long',entry=100,sl=95,base_sl_pct=.05,opened_at=1000,v6_max_seconds=7200,v6_engine='V6_BREAKOUT',v6_level=99)
  f={'5m':dict(close=[98,98],ts=[600000,900000])};self.assertEqual(v6.exit_plan(p,98,2000,f)['close'],'')
  f['5m']['ts']=[1200000,1500000];self.assertIn('突破失败',v6.exit_plan(p,98,2000,f)['close'])
 def test_net_metrics(self):
  t=[dict(net_pnl=x,fees=2,slippage=1,funding_cost=0,net_R=x/10) for x in [10,-20]];m=metrics(t,[10010,9990]);self.assertEqual(m['profit_factor'],.5);self.assertAlmostEqual(m['return_pct'],-.1);self.assertEqual(m['costs'],6)
 def test_duplicate_guard(self):
  d,p=self.sample();f=p['fast_strategy'];n=extract(['_v6_entry_guard','_v6_claim_signal']);now=(f['bar_ts']+300000)/1000
  self.assertTrue(n['_v6_entry_guard']('SOL',p,f['reference_price'],now));n['_v6_claim_signal']('SOL',p);self.assertFalse(n['_v6_entry_guard']('SOL',p,f['reference_price'],now));self.assertTrue(n['_v6_entry_guard']('SOL',{},0,0))
 def test_live_native_trail_no_local_amend(self):
  p=dict(side='long',entry=100,sl=95,base_sl_pct=.05,opened_at=0,v6_max_seconds=7200,v6_engine='V6_TREND',sl_attach_clordid='sl',fast_version='v6');client=Mock();client.get_ticker.return_value={'last':110};live=Mock()
  n=extract(['_live_manage_v6'],extra={'okx_client':client,'alpha_live':live,'gate_enabled':lambda k:True});n['_live_manage_v6']('X',p,{},{})
  self.assertEqual(p['sl'],95);live.amend_sl_only.assert_not_called()
 def test_live_close_confirmation(self):
  p=dict(side='long',entry=100,sl=95,base_sl_pct=.05,opened_at=0,v6_max_seconds=500,v6_engine='V6_TREND',order_id='open');c=Mock();c.get_ticker.return_value={'last':102};a=Mock();a.wait_position_closed.return_value={'closed':False};attr=Mock(return_value={'net_pnl':3.7})
  n=extract(['_live_manage_v6'],extra={'okx_client':c,'alpha_live':a,'_managed_close':Mock(return_value=dict(average=101.9,filled=2,order_id='close',fee=.1)),'trade_attribution':attr,'ledger_record':Mock(),'ops_record':Mock(),'_record_live_consecutive_result':Mock(),'_record_strategy_health_close':Mock()});n['STATE']['positions']['X']=p
  n['_live_manage_v6']('X',p,{},{});self.assertIn('X',n['STATE']['positions']);attr.assert_not_called();a.wait_position_closed.return_value={'closed':True};n['_live_manage_v6']('X',p,{},{});self.assertNotIn('X',n['STATE']['positions']);self.assertEqual(n['STATE']['live_trades'][0]['average'],101.9)
 def test_partial_switch(self):
  n=extract(['_live_partial_take_profit'],extra={'alpha_live':Mock()});n['_live_partial_take_profit']('X',dict(strategy_mode='FAST',fast_version='v6'),{});n['alpha_live'].positions.assert_not_called()
 def test_paper_partial(self):
  p=dict(side='long',entry=100,sl=95,tp=110,base_sl_pct=.05,base_tp_pct=.1,opened_at=0,v6_max_seconds=7200,v6_engine='V6_TREND',notional=1000);cfg=dict(fee_pct=.0006,slippage_pct=.0003);n=extract(['_paper_manage_v6']);n['STATE']['positions']['X']=p
  n['_paper_manage_v6']('X',p,{},cfg,106,1000);self.assertEqual(p['notional'],1000);n['gate_enabled']=lambda k:k=='partial_tp';n['_paper_manage_v6']('X',p,{},cfg,106,1000);self.assertEqual(p['notional'],700);self.assertGreater(p['sl'],100);n['_paper_manage_v6']('X',p,{},cfg,106,1001);self.assertEqual(p['notional'],700)
 def test_real_fast_routing(self):
  m=fast_module(ROOT/'alpha_fast_mode.py','tested_fast');m.set_active_version('v6');d,r=self.sample();self.assertEqual(m._build_decision('X',d),v6.decide('X',d));m.set_active_version('v5');self.assertEqual(m.get_active_version(),'v5')
 def test_api_and_frontend(self):
  e=Mock();e.set_fast_engine.return_value=dict(version='v6',label='V6');n=extract(['alpha_fast_set_engine'],ROOT/'api_server.py',dict(AlphaFastEngineRequest=object,Depends=lambda _:None,verify_api_key=None,logger=Mock()))
  with patch.dict(sys.modules,{'alpha_engine':e}):r=asyncio.run(n['alpha_fast_set_engine'](types.SimpleNamespace(version='v6'),True))
  e.set_fast_engine.assert_called_once_with('v6');self.assertEqual(r['engine']['version'],'v6');html=(ROOT/'templates/index.html').read_text();self.assertIn("alphaSetFastEngine('v6')",html);self.assertIn("v6:'fe_v6'",html)
 def test_v6_structure_distances_not_overwritten(self):
  d,r=self.sample();self.assertEqual(r['dynamic_tp_sl']['tp'],r['tp']);self.assertEqual(r['dynamic_tp_sl']['sl'],r['sl'])
  n=extract(['_v6_entry_guard']);f=r['fast_strategy'];n['_v6_entry_guard']('SOL',r,f['reference_price'],(f['bar_ts']+300000)/1000)
  self.assertEqual(r['dynamic_tp_sl']['sl'],r['sl'])
 def test_replay_gap_and_segment(self):
  with self.assertRaises(ValueError):replay('SOL',self.df.drop(index=1000),720,len(self.df)-1)
  m,t=replay('SOL',self.df,4500,len(self.df));self.assertAlmostEqual(m['net_pnl']/100,m['return_pct'],places=8);self.assertTrue(all(x['entry_ms']>=self.df.open_ms.iloc[4500] for x in t))

if __name__=='__main__':unittest.main(verbosity=2)
