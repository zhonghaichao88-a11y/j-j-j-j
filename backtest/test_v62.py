"""V6.2 offline regression: actual production functions with exchange doubles."""
import ast,copy,json,sys,types,unittest
from pathlib import Path
from unittest.mock import Mock
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(Path(__file__).parent))
import alpha_fast_v62 as v62
from test_v6 import extract,fast_module
from v6_replay import synthetic,FrameSource

def executor():
 tree=ast.parse((ROOT/'alpha_live.py').read_text())
 names={'positions','recover_filled_entry','_algo_client_id','_algo_is_active','protection_status','amend_tp_only','amend_protection','amend_sl_only','cancel_algo','_entry_protection','wait_position_closed'}
 cls=next(x for x in tree.body if isinstance(x,ast.ClassDef));cls.body=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in names]
 ns={'symbol_to_ccxt':lambda s,t:s,'logger':Mock(),'time':types.SimpleNamespace(time=Mock(side_effect=[0,0,2]),sleep=Mock())}
 exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),cls],type_ignores=[])),'live-production','exec'),ns)
 obj=ns[cls.name]();obj._ensure=Mock(return_value=Mock());obj.market=Mock(return_value={'contractSize':.01});obj._inst_id=Mock(return_value='BTC-USDT-SWAP')
 obj._protection_rows=Mock();obj._wait_for_attached_protection=Mock();return obj

def pair():return [dict(algoClOrdId='t',algoId='11',tpTriggerPx='110',tpTriggerPxType='last',state='live'),dict(algoClOrdId='s',algoId='12',slTriggerPx='95',slTriggerPxType='last',state='live')]

class Exchange62(unittest.TestCase):
 def test_queries_both_fail_never_flat(self):
  e=executor();ex=e._ensure();ex.request.side_effect=TimeoutError();ex.fetch_positions.side_effect=TimeoutError()
  with self.assertRaisesRegex(RuntimeError,'状态未知'):e.positions()
  self.assertFalse(e.wait_position_closed('X','long',timeout=1)['closed'])
 def test_valid_empty_native_is_flat(self):
  e=executor();e._ensure().request.return_value={'code':'0','data':[]};self.assertEqual(e.positions(),[]);e._ensure().fetch_positions.assert_not_called()
 def test_bad_native_empty_uses_fallback(self):
  e=executor();ex=e._ensure();ex.request.return_value={'code':'500','data':[]};ex.fetch_positions.return_value=[dict(symbol='BTC/USDT:USDT',contracts=2,side='short',contractSize=.01)]
  self.assertEqual(e.positions()[0]['side'],'short')
 def test_signed_net_and_contract_value(self):
  e=executor();e._ensure().request.return_value={'code':'0','data':[dict(instId='BTC-USDT-SWAP',pos='-2',posSide='net',avgPx='100',markPx='99')]}
  r=e.positions()[0];self.assertEqual((r['symbol'],r['side'],r['contract_size']),('BTC/USDT:USDT','short',.01))
 def test_inactive_protection_not_accepted(self):
  e=executor()
  for state in ('canceled','effective','order_failed',''):
   rows=pair();rows[0]['state']=state;e._protection_rows.return_value=rows
   self.assertFalse(e.protection_status('X','long',['t','s'])['verified'])
 def test_duplicate_ids_not_pair(self):
  e=executor();e._protection_rows.return_value=pair();self.assertFalse(e.protection_status('X','long',['t','t'])['verified'])
 def test_cancel_native_array_and_bad_ack(self):
  e=executor();ex=e._ensure();ex.request.return_value={'code':'0','data':[{'sCode':'0'}]};e.cancel_algo('X',algo_id='11')
  self.assertEqual(ex.request.call_args.args[3],[{'instId':'BTC-USDT-SWAP','algoId':'11'}])
  ex.request.return_value={}
  with self.assertRaises(RuntimeError):e.cancel_algo('X',algo_id='11')
 def test_cancel_resolves_only_exact_client(self):
  e=executor();e._protection_rows.return_value=pair();ex=e._ensure();ex.request.return_value={'code':'0','data':[{'sCode':'0'}]}
  e.cancel_algo('X',algo_cl_ord_id='t');self.assertEqual(ex.request.call_args.args[3][0]['algoId'],'11')
  with self.assertRaises(RuntimeError):e.cancel_algo('X',algo_cl_ord_id='other')
 def test_tp_ack_needs_new_price_and_type(self):
  e=executor();ex=e._ensure();ex.price_to_precision.side_effect=lambda s,p:str(p);ex.request.return_value={'data':[{'sCode':'0'}]};row=pair()[0]
  for final,want in [(row,False),({**row,'tpTriggerPx':'115','tpTriggerPxType':'mark'},False),({**row,'tpTriggerPx':'115'},True)]:
   e._wait_for_attached_protection.side_effect=[[row],[final]]
   self.assertEqual(e.amend_tp_only('X','long',115,'t')['verified'],want)
 def test_execution_uses_candidate_net_rr(self):
  e=executor();ex=e._ensure();ex.price_to_precision.side_effect=lambda s,p:str(p)
  guard=dict(tp=106.5,sl=95,reference=100,cost=.0022,min_net_rr=1.3)
  with self.assertRaisesRegex(ValueError,'空间不足'):e._entry_protection(ex,'X','long',100,.065,.05,guard)
  guard['min_net_rr']=1.1;self.assertEqual(e._entry_protection(ex,'X','long',100,.065,.05,guard),(106.5,95))
 def test_failed_pair_restores_original_tp_type(self):
  e=executor();ex=e._ensure();ex.price_to_precision.side_effect=lambda s,p:str(p)
  rows=pair();rows[0]['tpTriggerPxType']='mark';changed={**rows[0],'tpTriggerPx':'115','tpTriggerPxType':'last'}
  ex.request.side_effect=[{'data':[{'sCode':'0'}]},RuntimeError('SL rejected'),{'data':[{'sCode':'0'}]}]
  e._wait_for_attached_protection.side_effect=[rows,[changed],[rows[0]],rows]
  result=e.amend_protection('X','long',115,96,['t','s'])
  self.assertFalse(result['verified']);self.assertTrue(result['rolled_back']);self.assertEqual(ex.request.call_args.args[3]['newTpTriggerPxType'],'mark')
 def setup_recovery(self):
  e=executor();row=dict(state='filled',ordId='parent',side='buy',accFillSz='3',avgPx='100',attachAlgoOrds=[dict(attachAlgoClOrdId='t',tpTriggerPx='110'),dict(attachAlgoClOrdId='s',slTriggerPx='95')]);e.order_by_client_id=Mock(return_value=row);e._wait_for_attached_protection.return_value=pair();return e,row
 def test_recovery_actual_prices_not_percentages(self):
  e,row=self.setup_recovery();r=e.recover_filled_entry('X','long','parentclient');self.assertEqual((r['tp'],r['sl'],r['notional_usdt']),(110,95,3))
 def test_partial_requires_cancel_and_terminal(self):
  e,row=self.setup_recovery();e.order_by_client_id.side_effect=[{**row,'state':'partially_filled'},{**row,'state':'canceled'}]
  self.assertEqual(e.recover_filled_entry('X','long','parentclient')['filled'],3);self.assertEqual(e._ensure().request.call_args.args[0],'trade/cancel-order')
  e.order_by_client_id.side_effect=[{**row,'state':'partially_filled'},{**row,'state':'partially_filled'}]
  with self.assertRaisesRegex(RuntimeError,'终态'):e.recover_filled_entry('X','long','parentclient')
 def test_recovery_cannot_borrow_other_protection(self):
  e,row=self.setup_recovery();row['attachAlgoOrds']=[]
  with self.assertRaisesRegex(RuntimeError,'保护身份'):e.recover_filled_entry('X','long','parentclient')
 def test_quarantine_close_without_quote(self):
  c=Mock();c.get_ticker.side_effect=TimeoutError();close=Mock(return_value={'status':'no_position'})
  ns=extract(['_live_manage_v6'],extra={'okx_client':c,'_managed_close':close});ns['_live_manage_v6']('X',{'v6_recovery_pending':True,'side':'long'},{},{})
  close.assert_called_once_with('X','long');c.get_ticker.assert_not_called()
 def test_research_live_entry_disabled_by_default(self):
  ns=extract(['_live_step']);ns['_live_step']('X',{'fast_strategy':{'v62':True}},{})
  texts=[c.args[0] for c in ns['_activity'].call_args_list]
  self.assertTrue(any('V63_LIVE 许可未开启' in t for t in texts))
 def test_research_live_permitted_when_gate_on(self):
  ns=extract(['_live_step'],extra={'gate_enabled':lambda k:k in ('v63_live','model_ready'),'_resume_close_recovery':Mock(return_value=True)})
  ns['_live_step']('X',{'fast_strategy':{'v62':True}},{})
  texts=[c.args[0] for c in ns['_activity'].call_args_list]
  self.assertFalse(any('V63_LIVE 许可未开启' in t for t in texts))
  ns['_resume_close_recovery'].assert_called_once()

class Strategy62(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.df,_=synthetic('SOL',20);src=FrameSource(cls.df);cls.samples=[]
  for i in range(730,len(cls.df)-1):
   t=int(cls.df.open_ms.iloc[i])+300000;data=dict(frames=src.at(t),ticker_last=float(cls.df.close.iloc[i]),as_of_ms=t,missing=[],summary={},benchmark={'return_72h':.08,'symbols':10})
   pred=v62.decide('SOL',data)
   if pred['signal']!='FLAT':cls.samples.append((data,pred))
   if len(cls.samples)>=8:break
 def test_signals_prices_schema_metadata(self):
  self.assertTrue(self.samples)
  for data,pred in self.samples:
   json.dumps(pred,allow_nan=False);fs=pred['fast_strategy'];self.assertGreaterEqual(fs['net_rr'],1.3);self.assertTrue(v62.position_meta(pred)['v62']);self.assertGreater(pred['tp'],0);self.assertGreater(pred['sl'],0)
 def test_unclosed_stale_cost_rejected(self):
  data,pred=copy.deepcopy(self.samples[0]);data['as_of_ms']-=1;self.assertEqual(v62.decide('X',data)['signal'],'FLAT')
  data,pred=copy.deepcopy(self.samples[0]);data['as_of_ms']+=900000;self.assertEqual(v62.decide('X',data)['signal'],'FLAT')
  data,pred=self.samples[0];self.assertEqual(v62.decide('X',data,{'fee_side':.1})['signal'],'FLAT')
 def test_range_trail_switch_and_monotonicity(self):
  for side,px,sl,tp in [('long',107,95,110),('short',93,105,90)]:
   p=dict(v62=True,v62_policy='range',side=side,entry=100,sl=sl,tp=tp,base_sl_pct=.05,base_tp_pct=.1,v6_round_cost=.0022,opened_at=0,v6_max_seconds=10000)
   self.assertIsNone(v62.exit_plan(p,px,100,trailing=False)['stop']);stop=v62.exit_plan(p,px,100,trailing=True)['stop'];self.assertIsNotNone(stop)
   p['sl']=stop;self.assertIsNone(v62.exit_plan(p,px,100,trailing=True)['stop'])
 def test_breakout_only_postentry_closed_bars(self):
  p=dict(side='long',entry=100,sl=95,base_sl_pct=.05,opened_at=1000,v6_max_seconds=7200,v6_engine='V6_BREAKOUT',v6_level=99)
  f={'5m':dict(close=[98,98],ts=[600000,900000])};self.assertFalse(v62.exit_plan(p,98,2000,f)['close'])
  f['5m']['ts']=[1200000,1500000];self.assertFalse(v62.exit_plan(p,98,1799,f)['close']);self.assertTrue(v62.exit_plan(p,98,1800,f)['close'])
 def test_v62_runtime_route(self):
  m=fast_module(ROOT/'alpha_fast_mode.py','test_fast_mode62');self.assertEqual(m.set_active_version('v62'),'v62')
  data,pred=self.samples[0];self.assertTrue(m._build_decision('SOL',data)['fast_strategy']['v62'])
  m.set_active_version('v6');self.assertFalse(m._build_decision('SOL',data)['fast_strategy'].get('v62',False))
if __name__=='__main__':unittest.main(verbosity=2)
