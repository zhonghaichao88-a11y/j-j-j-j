import unittest,tempfile
from pathlib import Path
from unittest.mock import patch
import numpy as np
import alpha_fast_v7 as v
from alpha_v7_pine import save,run
from test_v73_pine import frame,SOURCE
from test_v72_api import APITests
from alpha_v7_replay import aggregate,simulate

class IntegrationTests(unittest.TestCase):
 def setUp(self):
  v._RUNTIME={};self.tmp=tempfile.TemporaryDirectory();self.patcher=patch('alpha_v7_pine.ROOT',Path(self.tmp.name));self.patcher.start()
 def tearDown(self):self.patcher.stop();self.tmp.cleanup()
 def params(self,id):return {'strategy':'pine_import','pine_id':id,'sl_mode':'atr','dynamic_tp':0,'max_stop':.5,'min_risk_cost':.1,'min_target_cost':.1,'min_net_rr':.1}
 def test_pine_to_trade_meta_and_locked_exit(self):
  f=frame(288);src='strategy("test")\nif bar_index==287\n    strategy.entry("L",strategy.long)';id=save(src)
  data=dict(frames={'5m':f},ticker_last=float(f['close'][-1]),missing=[],as_of_ms=int(f['ts'][-1]+300000))
  pred=v.decide('TEST',data,self.params(id));self.assertEqual(pred['signal'],'LONG',pred['reason']);meta=v.position_meta(pred);self.assertEqual(meta['v7_pine_id'],id)
  # A close after a prior entry is matched by immutable script/order ID, not current UI params.
  close_src='strategy("test")\nstrategy.entry("L",strategy.long)\nif bar_index>280\n    strategy.close("L")';cid=save(close_src)
  pos=dict(entry=100,side='long',opened_at=280*300,base_sl_pct=.03,sl=90,**meta);pos['v7_max_seconds']=100000;pos['v7_pine_id']=cid
  plan=v.exit_plan(pos,105,288*300,{'5m':f},False);self.assertIn('Pine',plan['close'])
 def test_pine_invalid_indicator_cannot_trade(self):
  id=save('indicator("test")\nplot(close)')
  with self.assertRaises(ValueError):v.validate_params(self.params(id))
 def test_flow_required_fails_closed_or_opposes(self):
  f=frame(288);id=save('strategy("test")\nif bar_index==287\n    strategy.entry("L",strategy.long)');p={**self.params(id),'orderflow_mode':2}
  data=dict(frames={'5m':f},ticker_last=float(f['close'][-1]),missing=[],as_of_ms=288*300000)
  self.assertEqual(v.decide('T',data,p)['signal'],'FLAT')
  data['orderflow']=dict(fresh=True,received_at=288*300,trade_imbalance=-.8,book_imbalance=-.6)
  self.assertEqual(v.decide('T',data,p)['signal'],'FLAT')
  data['orderflow']['trade_imbalance']=.8
  self.assertEqual(v.decide('T',data,p)['signal'],'LONG')
 def test_aggregate_excludes_partial_buckets(self):
  f=frame(38);r=aggregate(f,3600000,300000);self.assertEqual(len(r['close']),3);self.assertEqual(r['close'][-1],f['close'][35])
 def test_replay_rejects_fake_flow_history(self):
  f=frame(310);rows=[dict(timestamp=int(f['ts'][i]),**{k:float(f[k][i]) for k in ('open','high','low','close','volume')}) for i in range(310)]
  with self.assertRaisesRegex(ValueError,'逐笔'):simulate(rows,{'orderflow_mode':2},10000)
 def test_chan_signal_protects_actual_invalidation(self):
  f=frame(288);signal={'side':1,'reason':'2买严格规则确认','chan_signal':{'label':'2买','invalidation':80,'id':'test'}}
  data=dict(frames={'5m':f},ticker_last=float(f['close'][-1]),missing=[],as_of_ms=288*300000)
  fake={'candidates':{'chan_quant':signal},'regime':'趋势'}
  with patch.object(v,'analyze',return_value=fake):
   pred=v.decide('T',data,{'strategy':'chan_quant','chan_mtf':0,'max_stop':.5,'dynamic_tp':0,'min_risk_cost':.1,'min_target_cost':.1,'min_net_rr':.1})
  self.assertEqual(pred['signal'],'LONG');self.assertLess(pred['fast_strategy']['v7_strategy_stop'],80);self.assertEqual(v.position_meta(pred)['v7_chan_signal']['id'],'test')

class PineAPITests(APITests):
 def test_pine_endpoint_preview(self):
  r=self.client.post('/tv/api/pine',json={'source':SOURCE,'rows':self.rows()})
  self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['bars'],288);self.assertTrue(r.json()['events'])
 def test_bad_source_and_future_rejected(self):
  for source in ('indicator("x")\nplot(close[-1])','indicator("x")\nrequest.security("ETH-USDT-SWAP","D",close)'):
   self.assertEqual(self.client.post('/tv/api/pine',json={'source':source,'rows':self.rows(),'symbol':'BTC-USDT-SWAP'}).status_code,400)
 def test_save_and_reload_hash(self):
  with tempfile.TemporaryDirectory() as tmp,patch('alpha_v7_pine.ROOT',Path(tmp)):
   r=self.client.post('/tv/api/pine',json={'source':SOURCE,'rows':self.rows(),'save':True});self.assertEqual(r.status_code,200,r.text)
   identifier=r.json()['script_id'];self.assertEqual(self.client.get('/tv/api/pine/'+identifier).json()['source'],SOURCE)
   self.assertEqual(self.client.get('/tv/api/pine/not-a-hash').status_code,400)
