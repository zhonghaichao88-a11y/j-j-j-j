import unittest
import numpy as np
from alpha_v7_chan import unit,segments,centers,trade_signals,analyze,pens

def units(prices):
 pts=[dict(t=i*300000,price=float(p),kind='L' if i%2==0 else 'H',i=i,raw_i=i,known_at=(i+1)*300000) for i,p in enumerate(prices)]
 return [unit(a,b,(i+2)*300000,i,'pen') for i,(a,b) in enumerate(zip(pts,pts[1:]))]

class Chan73Tests(unittest.TestCase):
 def test_no_gap_segment(self):
  u=units([0,10,5,12,7,11,4,8,2,6,0,4]);out,_,_=segments(u)
  self.assertTrue(out);self.assertEqual(out[0]['a']['price'],0);self.assertEqual(out[0]['b']['price'],12)
  self.assertTrue(out[0]['confirmed']);self.assertGreater(out[0]['known_at'],out[0]['to'])
 def test_first_pen_break_case71(self):
  u=units([0,10,5,12,3,8,1,5,-2]);out,_,_=segments(u)
  self.assertTrue(out);self.assertEqual(out[0]['b']['price'],12)
 def test_gap_waits_for_second_fractal(self):
  u=units([0,5,2,10,7,9,6]);out,_,_=segments(u)
  self.assertFalse(out)
  # Bottom fractal in upward feature sequence confirms the descending leg.
  u=units([0,5,2,10,7,9,6,8,4,7,5,9]);out,_,_=segments(u)
  self.assertTrue(out);self.assertEqual(out[0]['b']['price'],10)
 def test_segment_prefix_frozen(self):
  rng=np.random.default_rng(821);v=[100.]
  for i in range(100):v.append(v[-1]+(1 if i%2==0 else -1)*rng.uniform(1,8))
  us=units(v);full=segments(us)[0]
  for n in range(6,len(us)):
   for x in segments(us[:n])[0]:self.assertIn(x,full)
 def test_center_core_never_shrinks_to_later_overlap(self):
  us=units([0,10,4,12,5,11,6,13]);z,e,x=centers(us,0)
  self.assertEqual((z[0]['low'],z[0]['high']),(4,10));self.assertTrue(z[0]['extended'])
 def test_third_point_is_first_pullback_outside(self):
  us=units([0,10,4,12,5,15,11,17,12,19]);z,_,_=centers(us,0)
  signals,_=trade_signals(us,z,np.ones(20),np.arange(20)*300000)
  third=[s for s in signals if s['label']=='3买'];self.assertEqual(len(third),1);self.assertEqual(third[0]['price'],11)
 def test_mirror_gives_third_sell(self):
  us=units([30-x for x in [0,10,4,12,5,15,11,17,12,19]]);z,_,_=centers(us,0)
  signals,_=trade_signals(us,z,np.ones(20),np.arange(20)*300000)
  self.assertEqual(len([s for s in signals if s['label']=='3卖']),1)
 def test_no_fake_first_buy_from_range_divergence(self):
  us=units([10,20,12,19,11,18,10]);z,_,_=centers(us,0)
  signals,_=trade_signals(us,z,np.linspace(10,1,20),np.arange(20)*300000)
  self.assertFalse(any(s['label'] in ('1买','1卖') for s in signals))
 def test_candle_append_preserves_confirmed_pens(self):
  rng=np.random.default_rng(121);c=100+np.cumsum(rng.normal(0,.5,600));o=np.r_[c[0],c[:-1]]
  f=dict(ts=np.arange(600)*300000,open=o,close=c,high=np.maximum(o,c)+.1,low=np.minimum(o,c)-.1,volume=np.ones(600))
  full=pens(f)['units']
  for n in (100,150,200,300,450):
   short=pens({k:v[:n] for k,v in f.items()})
   for u in short['units']:self.assertIn(u,full)
 def test_recursive_levels_only_confirmed_children(self):
  n=2000;c=100+np.sin(np.arange(n)*.15)*4+np.sin(np.arange(n)*.018)*12
  f=dict(ts=np.arange(n)*300000,open=c,close=c,high=c+.1,low=c-.1,volume=np.ones(n))
  r=analyze(f,np.sin(np.arange(n)*.15));self.assertTrue(r['levels'])
  for level in r['levels']:
   for u in level['units']:self.assertTrue(u['confirmed']);self.assertGreaterEqual(u['known_at'],u['to'])
 def test_real_centers_first_and_second_buys_and_mirror(self):
  prices=[120,110,116,108,114,100,107,102,106,95,105,98,110]
  for mirror in (False,True):
   us=units([300-x if mirror else x for x in prices]);zs,_,_=centers(us,0)
   hist=np.geomspace(100,1,26);result,_=trade_signals(us,zs,hist,np.arange(26)*300000)
   self.assertIn('1卖' if mirror else '1买',[s['label'] for s in result]);self.assertIn('2卖' if mirror else '2买',[s['label'] for s in result])
 def test_all_signal_prefixes_on_confirmed_units(self):
  prices=[120,110,116,108,114,100,107,102,106,95,105,98,110,101,115,108,119,110,122]
  us=units(prices);hist=np.geomspace(100,1,40);ts=np.arange(40)*300000;zs,_,_=centers(us,0);full=trade_signals(us,zs,hist,ts)[0]
  for n in range(3,len(us)):
   zs,_,_=centers(us[:n],0);short=trade_signals(us[:n],zs,hist,ts)[0]
   self.assertEqual(short,[s for s in full if s['known_at']<=us[n-1]['known_at']])
 def test_expansion_never_uses_future_endpoint(self):
  us=units([0,10,4,12,5,15,11,17,12,19,13,18]);zs,_,_=centers(us,0)
  for n in range(6,len(us)):
   _,_,exp=centers(us[:n],0);_,_,full=centers(us,0)
   for e in exp:self.assertIn(e,full);self.assertLessEqual(e['to'],e['known_at'])
