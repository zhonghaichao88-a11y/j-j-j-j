import sys,types,unittest
from unittest.mock import patch
import numpy as np
from test_v72_analysis import frame

class APITests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  from fastapi import FastAPI
  from fastapi.testclient import TestClient
  # Import the research-only router under a fake market connector; never import account configuration.
  with patch.dict(sys.modules,{'okx_client':types.SimpleNamespace(okx_client=types.SimpleNamespace(is_connected=False))}):
   import tv_plus
  cls.module=tv_plus;app=FastAPI();app.include_router(tv_plus.router);cls.client=TestClient(app)
 def rows(self,n=310):
  f=frame(n);return [dict(timestamp=int(f['ts'][i]),**{k:float(f[k][i]) for k in ('open','high','low','close','volume')},confirmed=True) for i in range(n)]
 def test_analysis_json_and_closed_only(self):
  rows=self.rows();last=rows[-1];r=self.client.post('/tv/api/analysis',json={'rows':rows,'tf':'5m','as_of_ms':last['timestamp']+1000})
  self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['as_of'],rows[-2]['timestamp']);self.assertTrue(r.json()['overlays'])
 def test_bad_data_error(self):
  rows=self.rows();rows[-2]['low']=-1
  self.assertEqual(self.client.post('/tv/api/analysis',json={'rows':rows}).status_code,400)
 def test_gap_rejected(self):
  rows=self.rows();rows.pop(100)
  self.assertEqual(self.client.post('/tv/api/analysis',json={'rows':rows}).status_code,400)
 def test_backtest_switches_route(self):
  rows=self.rows()
  def bars(symbol,tf,before=None,limit=300):return [r for r in rows if before is None or r['timestamp']<before][-limit:]
  with patch.object(self.module,'bars',side_effect=bars):
   r=self.client.post('/tv/api/backtest',json={'symbol':'BTC-USDT-SWAP','count':310,'params':{'strategy':'ema_cross'},'trailing':True,'partial':True})
  self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['switches'],{'趋势跟踪':True,'分批止盈':True})
