import unittest,tempfile
from pathlib import Path
from unittest.mock import Mock,patch
import numpy as np
import alpha_v7_feed as feed
from test_v73_pine import frame
class FeedTests(unittest.TestCase):
 def setUp(self):feed._CACHE.clear();feed._ANCHORS.clear()
 def test_public_pagination_closed_and_contiguous(self):
  e=Mock();e.id='okx';e.market.return_value={'id':'BTC-USDT-SWAP'}
  def request(path,scope,method,args):
   before=int(args.get('after',100*300000));start=before//300000
   return {'code':'0','data':[[str(i*300000),'100','102','99','101','2','0','0','1'] for i in range(start-1,max(-1,start-31),-1)]}
  e.request.side_effect=request
  f=feed.frame(e,'BTC','5m',60,100*300000);self.assertEqual(len(f['close']),60);self.assertEqual(f['ts'][-1],99*300000)
  self.assertEqual(e.request.call_args[0][0],'market/history-candles')
 def test_anchor_append_and_restart(self):
  e=Mock();e.id='okx';first=frame(100);second=frame(120);second={k:v[20:] for k,v in second.items()}
  with tempfile.TemporaryDirectory() as tmp,patch.object(feed,'HISTORY_ROOT',Path(tmp)),patch.object(feed,'frame',side_effect=[first,second,second]):
   a=feed.anchored_frame(e,'BTC');b=feed.anchored_frame(e,'BTC');self.assertEqual(len(b['close']),120);self.assertEqual(b['ts'][0],0)
   feed._ANCHORS.clear();c=feed.anchored_frame(e,'BTC');np.testing.assert_equal(b['close'],c['close'])
 def test_history_revision_is_not_silently_redrawn(self):
  e=Mock();e.id='okx';f=frame(100);g={k:v.copy() for k,v in f.items()};g['close'][80]+=.1
  with tempfile.TemporaryDirectory() as tmp,patch.object(feed,'HISTORY_ROOT',Path(tmp)),patch.object(feed,'frame',side_effect=[f,g]):
   feed.anchored_frame(e,'BTC')
   with self.assertRaisesRegex(ValueError,'修订'):feed.anchored_frame(e,'BTC')
