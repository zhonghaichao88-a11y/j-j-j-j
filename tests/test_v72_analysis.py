"""Deterministic offline tests; no API keys, no exchange orders."""
import unittest, types, threading, time
from unittest.mock import Mock
import numpy as np
import alpha_fast_v7 as v
from alpha_v7_analysis import analyze, indicators, chan, STRATEGIES
from test_v7_repairs import load_functions

def frame(n=260):
 rng=np.random.default_rng(32);c=100+np.cumsum(rng.normal(.025,.6,n));o=np.r_[c[0],c[:-1]]
 return dict(ts=np.arange(n)*300000,open=o,close=c,high=np.maximum(o,c)+.3,low=np.minimum(o,c)-.3,volume=rng.uniform(10,100,n))

class AnalysisTests(unittest.TestCase):
 def setUp(self):v._RUNTIME={}
 def test_indicator_prefix_invariance(self):
  f=frame();full=indicators(f)
  for size in (60,100,173):
   short=indicators({k:a[:size] for k,a in f.items()})
   for k,x in short.items():np.testing.assert_allclose(x,full[k][:size],err_msg=k)
 def test_structure_events_only_after_confirmation(self):
  f=frame();full=analyze(f)
  for n in (90,130,200):
   short=analyze({k:a[:n] for k,a in f.items()});cut=int(f['ts'][n-1])
   self.assertEqual(short['events'],[e for e in full['events'] if e['known_at']<=cut])
 def test_chan_confirmed_prefix_invariance(self):
  f=frame(420);full=chan(f)
  for n in (100,170,240,330):
   small=chan({k:a[:n] for k,a in f.items()});cut=int(f['ts'][n-1])
   self.assertEqual(small['signals'],[x for x in full['signals'] if x['known_at']<=cut])
   for s in small['strokes']:
    if s['confirmed']:self.assertIn(s,full['strokes'])
 def test_new_strategies_reachable_without_schema_errors(self):
  f=frame();d=dict(frames={'5m':f},ticker_last=float(f['close'][-1]),missing=[],as_of_ms=int(f['ts'][-1]+300000))
  for key in list(STRATEGIES)+['auto_regime']:
   r=v.decide('TEST',d,{'strategy':key});self.assertIn(r['signal'],('FLAT','LONG','SHORT'))
 def test_new_parameter_bounds(self):
  for p in ({'close_confirm':2},{'sl_buffer_atr':-1},{'structure_bars':0},{'runner_rr':1},{'trail_atr_k':0}):
   with self.assertRaises(ValueError):v.validate_params(p)
 def test_wide_structure_stop_sizes_risk_truthfully(self):
  f=frame();f['close'][-1]=max(f['high'][:-1])+1;f['high'][-1]=f['close'][-1]+.1
  d=dict(frames={'5m':f},ticker_last=float(f['close'][-1]),missing=[],as_of_ms=int(f['ts'][-1]+300000),spread_bps=0)
  p={'strategy':'donchian','max_stop':.5,'dynamic_tp':0,'min_risk_cost':.1,'min_net_rr':.1,'max_chase_r':1}
  r=v.decide('TEST',d,p);self.assertEqual(r['signal'],'LONG')
  fs=r['fast_strategy'];self.assertLess(fs['sl_price'],fs['v7_strategy_stop']);self.assertAlmostEqual(r['sl'],(d['ticker_last']-fs['sl_price'])/d['ticker_last'])
 def test_long_short_native_risk_scaling(self):
  for side in ('long','short'):
   p=dict(entry=100,side=side,base_sl_pct=.01,v7_atr=.4,v7_round_cost=.002,v7_exit_config={'trail_activate_r':1,'trail_atr_k':1.5})
   active,cb=v.native_trail_config(p);self.assertAlmostEqual(active,101 if side=='long' else 99);self.assertLess(cb,.02)
 def test_close_stop_ignores_preentry_and_unfinished(self):
  p=dict(entry=100,side='long',opened_at=600,sl=95,base_sl_pct=.05,v7_strategy_stop=98,v7_max_seconds=3600,v7_exit_config={'close_confirm':1,'sl_mode':'structure'})
  f={'5m':dict(ts=[300000,600000],close=[97,97])}
  self.assertFalse(v.exit_plan(p,99,899,f)['close']);self.assertIn('结构失效',v.exit_plan(p,99,900,f)['close'])
 def test_partial_floor_without_trailing(self):
  p=dict(entry=100,side='long',opened_at=0,sl=98,base_sl_pct=.02,base_tp_pct=.04,v7_round_cost=.002,partial_tp_steps=['TP1'])
  self.assertGreater(v.exit_plan(p,102,100,trailing=False)['stop'],100)
 def test_no_stop_loosen_or_wrong_side(self):
  for side,sl,px in (('long',103,105),('short',97,95)):
   p=dict(entry=100,side=side,sl=sl,base_sl_pct=.02,base_tp_pct=.04,opened_at=0,v7_atr=1,v7_round_cost=.002)
   r=v.exit_plan(p,px,100,trailing=True)['stop']
   if r is not None:self.assertGreater((r-sl)*(1 if side=='long' else -1),0)

class NativeTests(unittest.TestCase):
 def test_trigger_is_not_fill_and_stage_id_stable(self):
  ex=Mock();ex._algo_client_id.side_effect=lambda x:x['algoClOrdId']
  ex._protection_rows.return_value=[dict(algoClOrdId='tp1',state='effective',ordIdList=['child'])]
  ex.wait_order_terminal.return_value={'status':'open','filled':3}
  ns=dict(alpha_live=ex,LOCK=threading.RLock(),_persist=Mock(),_activity=Mock())
  load_functions('alpha_engine.py',['_v7_reconcile_partial'],ns)
  p=dict(native_partial_armed=True,side='long',partial_tp_native_specs=[dict(name='TP1',qty=3,client_id='tp1')],partial_tp_order_ids=['tp1'])
  ns['_v7_reconcile_partial']('X',p);self.assertFalse(p.get('partial_tp_steps'))
  ex.wait_order_terminal.return_value={'status':'closed','filled':3}
  ns['_v7_reconcile_partial']('X',p);self.assertEqual(p['partial_tp_steps'],['TP1']);self.assertEqual(p['partial_tp_order_ids'],['tp1'])
  ns['_v7_reconcile_partial']('X',p);self.assertEqual(p['partial_tp_steps'],['TP1'])
 def test_runner_failure_restores_before_cancel(self):
  ex=Mock();ex.place_native_trailing.return_value={'client_id':'trail'};ex.protection_status_set.return_value={'verified':True}
  ex.amend_tp_only.side_effect=[{'verified':False},{'verified':True}]
  p=dict(fast_version='v7',side='long',entry=100,base_sl_pct=.01,base_tp_pct=.02,tp=102,original_tp_price=102,sl_attach_clordid='sl',tp_attach_clordid='tp',v7_exit_config={'runner':1})
  cancel=Mock();ns=dict(alpha_live=ex,fast_v7=v,_current_position_contracts=lambda *a:10,_native_trail_config=lambda *a:(101,.01),_cancel_active_algo=cancel,LOCK=threading.RLock(),STATE={'positions':{'X':p}},_persist=Mock(),_activity=Mock())
  load_functions('alpha_engine.py',['_arm_native_trail'],ns)
  with self.assertRaises(RuntimeError):ns['_arm_native_trail']('X',p,{})
  self.assertEqual(ex.amend_tp_only.call_count,2);cancel.assert_called_once_with('X','long','trail');self.assertFalse(p.get('native_trail_armed'))

class ReplayTests(unittest.TestCase):
 def test_four_switch_combinations_execute_without_future_data(self):
  from alpha_v7_replay import simulate
  from unittest.mock import patch
  rows=[dict(timestamp=i*300000,open=100,high=103,low=99.5,close=101,volume=10,confirmed=True) for i in range(310)]
  def decide(symbol,data,params):
   self.assertLess(max(data['frames']['5m']['ts']),data['as_of_ms'])
   return dict(signal='LONG',sl=.02,tp=.04,fast_strategy=dict(engine_version='v7',sl_price=98,tp_price=104,v7_atr=.5,v7_exit_config={'runner':1,'runner_rr':5,'trail_activate_r':1,'trail_atr_k':1.5}))
  for trailing in (False,True):
   for partial in (False,True):
    with patch.object(v,'decide',side_effect=decide):result=simulate(rows,{},10000,trailing,partial)
    self.assertGreater(result['summary']['交易数'],0)
    self.assertTrue(all(np.isfinite(t['pnl']) for t in result['trades']))
    self.assertEqual(any(t['partial'] for t in result['trades']),partial)

if __name__=='__main__':unittest.main()
