import unittest
import numpy as np
from alpha_v7_pine import run,compile_source,PineError

def frame(n=160):
 c=100+np.sin(np.arange(n)*.19)*6
 return dict(ts=np.arange(n)*300000,open=c,close=c,high=c+1,low=c-1,volume=np.ones(n)*100)
SOURCE='''//@version=5
strategy("EMA cross", overlay=true)
fast = ta.ema(close, 5)
slow = ta.ema(close, 14)
long = ta.crossover(fast, slow)
short = ta.crossunder(fast, slow)
plot(fast, title="Fast")
plot(slow, title="Slow")
if long
    strategy.entry("L", strategy.long)
if short
    strategy.close("L")
    strategy.entry("S", strategy.short)
'''
class PineTests(unittest.TestCase):
 def test_strategy_signals_and_plots(self):
  r=run(SOURCE,frame());self.assertEqual(len(r['plots']),2);self.assertTrue(any(e['kind']=='entry' for e in r['events']));self.assertTrue(any(e['kind']=='close' for e in r['events']))
 def test_prefix_invariance(self):
  f=frame();full=run(SOURCE,f)
  for n in (40,80,120):
   short=run(SOURCE,{k:v[:n] for k,v in f.items()})
   self.assertEqual(short['events'],[e for e in full['events'] if e['bar']<n])
   for a,b in zip(short['plots'],full['plots']):self.assertEqual(a['values'],b['values'][:n])
 def test_tuples_macd_bb_hma(self):
  src='indicator("tuple")\n[m,s,h] = ta.macd(close,12,26,9)\n[b,u,d] = ta.bb(close,20,2)\nplot(h)\nplot(u)\nplot(ta.hma(close,16))'
  result=run(src,frame());self.assertTrue(all(p['values'][-1] is not None for p in result['plots']))
  f=frame();ema=lambda a,n: __import__('alpha_fast_v7').ema_arr(a,n)
  m=ema(f['close'],12)-ema(f['close'],26);self.assertAlmostEqual(result['plots'][0]['values'][-1],(m-ema(m,9))[-1])
 def test_multiline_and_inputs(self):
  src='indicator("x",\n overlay=true)\nn=input.int(5, title="周期", minval=2, maxval=10)\nplot(ta.sma(close,n))'
  r=run(src,frame(),{'n':8});self.assertAlmostEqual(r['plots'][0]['values'][-1],np.mean(frame()['close'][-8:]))
  with self.assertRaises(PineError):run(src,frame(),{'n':1})
 def test_var_history(self):
  r=run('indicator("sum")\nvar float total = 0\ntotal := nz(total[1]) + 1\nplot(total)',frame())
  self.assertEqual(r['plots'][0]['values'][-1],160)
 def test_reject_unsupported_and_future(self):
  for src in ('indicator("x")\nx=request.security_lower_tf("BTC","1D",close)','indicator("x")\nplot(close,offset=5001)','indicator("x")\nx=__import__("os")'):
   with self.assertRaises(PineError):compile_source(src)
  with self.assertRaises(PineError):run('indicator("x")\nplot(close[-1])',frame())
  # V8 broker now models commission, so commission_value is accepted.
  compile_source('strategy("x",commission_value=1)')
 def test_conditional_stateful_rejected(self):
  with self.assertRaises(PineError):compile_source('indicator("x")\nif close>open\n    x=ta.ema(close,5)')
 def test_alert_recorded(self):
  r=run('indicator("x")\nalertcondition(close>100,title="high")',frame());self.assertTrue(r['events'])

 def test_change_cannot_read_future(self):
  for name in ('ta.change','ta.roc'):
   with self.assertRaises(PineError):run('indicator("x")\nplot('+name+'(close,-1))',frame())
 def test_conditional_expression_allows_stateful(self):
  src='indicator("x")\nx=close>100 ? ta.ema(close,5) : close\nplot(x)'
  r=run(src,frame());self.assertEqual(len(r['plots']),1)
  self.assertTrue(r['plots'][0]['values'][-1] is not None)
