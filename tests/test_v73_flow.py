import unittest,json
from unittest.mock import Mock,patch
from alpha_v7_orderflow import FlowState,PublicStream,snapshot,_STATES
class FlowTests(unittest.TestCase):
 def setUp(self):_STATES.clear()
 def test_contract_notional_dedup_and_age(self):
  s=FlowState()
  for i in range(6):s.trade({'id':str(i),'timestamp':(100-i)*1000,'price':100,'amount':2,'side':'buy'},.01,100)
  s.trade({'id':'0','timestamp':100000,'price':100,'amount':2,'side':'buy'},.01,100)
  r=s.snapshot(100);self.assertEqual(r['buy_usdt'],12);self.assertEqual(r['observed_cvd_usdt'],12)
  self.assertIsNone(s.snapshot(150)['trade_imbalance'])
 def test_depth_stale_and_wall_persistence(self):
  s=FlowState();b={'timestamp':100000,'bids':[[99,100],[98,1],[97,1],[96,1],[95,1]],'asks':[[101,1],[102,1],[103,1],[104,1],[105,1]]}
  for t in (100,101,102):b['timestamp']=t*1000;s.depth(b,.01,t)
  self.assertFalse(s.snapshot(102)['book_confirmed'])
  b['timestamp']=104000;s.depth(b,.01,104);self.assertTrue(s.snapshot(104)['book_confirmed'])
  self.assertIsNone(s.snapshot(120)['book'])
  with self.assertRaises(ValueError):s.depth(b,.01,120)
 def test_ws_messages_reset_boundary(self):
  stream=PublicStream();key=('okx','TEST');stream.targets['TEST']=(key,.01,100)
  trades=[{'tradeId':str(i),'ts':str(100000-i),'px':'100','sz':'2','side':'sell'} for i in range(6)]
  msg=json.dumps({'arg':{'instId':'TEST','channel':'trades'},'data':trades})
  stream.message(msg,100);stream.message(msg,100)
  s=_STATES[key];self.assertEqual(s.snapshot(100)['delta_usdt'],-12);self.assertTrue(s.source.startswith('WebSocket'))
  stream.reset();self.assertIsNone(s.snapshot(101)['observed_cvd_usdt']);self.assertFalse(s.snapshot(101)['fresh'])
 def test_public_connector_uses_actual_fields(self):
  e=Mock();e.id='okx';e.market.return_value={'linear':True,'quote':'USDT','contractSize':.01}
  e.fetch_order_book.return_value={'timestamp':100000,'bids':[[99,2]],'asks':[[101,1]]}
  e.fetch_trades.return_value=[{'id':str(i),'timestamp':100000,'price':100,'amount':2,'side':'buy'} for i in range(6)]
  e.fetch_funding_rate.return_value={'fundingRate':.0001};e.fetch_open_interest.return_value={'openInterestAmount':300}
  r=snapshot(e,'T',100);self.assertTrue(r['fresh']);self.assertEqual(r['open_interest'],300);self.assertEqual(r['buy_usdt'],12)
  e.fetch_trades.side_effect=RuntimeError('outage');e.fetch_order_book.side_effect=RuntimeError('outage')
  self.assertFalse(snapshot(e,'T',120)['fresh'])
 def test_inverse_contract_rejected(self):
  e=Mock();e.market.return_value={'linear':False,'quote':'USD','contractSize':100}
  with self.assertRaises(ValueError):snapshot(e,'T',100)
