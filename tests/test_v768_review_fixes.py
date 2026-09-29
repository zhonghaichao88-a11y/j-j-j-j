"""V7.6.8 review fixes: base_tf entry window, anchor gap recovery, chart display of other Chan levels."""
import glob,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import numpy as np
import alpha_v7_feed as feed
from alpha_v7_chan import analyze
from alpha_v7_analysis import analyze as analyze_all
from test_v7_repairs import load_functions
from test_v73_pine import frame
import alpha_fast_v7


def guard_ns():
    return load_functions('alpha_engine.py',['_v7_entry_guard'],{'STATE':{'v7_attempted':{}},'_activity':lambda *a:None,'fast_v7':alpha_fast_v7})


def pred(tf,bar_ts):
    return {'signal':'LONG','fast_strategy':{'engine_version':'v7','signal_id':'s1','v7_base_tf':tf,'bar_ts':bar_ts,
            'reference_price':100,'sl_price':98,'tp_price':106,'estimated_round_cost':.002}}


class EntryWindowTests(unittest.TestCase):
    def test_15m_and_1h_signal_accepted_just_after_close(self):
        for tf in ('5m','15m','1h'):
            ms=alpha_fast_v7.tf_ms(tf);bar_ts=10*ms
            self.assertTrue(guard_ns()['_v7_entry_guard']('X',pred(tf,bar_ts),100,(bar_ts+ms+2000)/1000),tf)

    def test_stale_or_unclosed_still_rejected(self):
        ms=alpha_fast_v7.tf_ms('15m');bar_ts=10*ms;g=guard_ns()['_v7_entry_guard']
        self.assertFalse(g('X',pred('15m',bar_ts),100,(bar_ts+ms-1000)/1000))
        self.assertFalse(g('X',pred('15m',bar_ts),100,(bar_ts+2*ms+20000)/1000))


class AnchorGapTests(unittest.TestCase):
    def setUp(self):feed._CACHE.clear();feed._ANCHORS.clear()

    def test_gap_reanchors_instead_of_blocking_forever(self):
        e=Mock();e.id='okx';old=frame(100);late=frame(400);late={k:v[250:] for k,v in late.items()}
        with tempfile.TemporaryDirectory() as tmp,patch.object(feed,'HISTORY_ROOT',Path(tmp)),patch.object(feed,'frame',side_effect=[old,late,late]):
            feed.anchored_frame(e,'BTC')
            b=feed.anchored_frame(e,'BTC');np.testing.assert_equal(b['ts'],late['ts'])
            feed._ANCHORS.clear();c=feed.anchored_frame(e,'BTC');np.testing.assert_equal(c['ts'],late['ts'])


def real_frame():
    files=sorted(glob.glob(str(Path(__file__).resolve().parents[1]/'data_v62'/'*_5m.csv')))
    if not files:
        return frame(3000)
    a=np.genfromtxt(files[0],delimiter=',',skip_header=1,usecols=(0,2,3,4,5,6))[-3000:]
    return dict(ts=a[:,0].astype(np.int64),open=a[:,1],high=a[:,2],low=a[:,3],close=a[:,4],volume=a[:,5])


class DisplayLevelTests(unittest.TestCase):
    def test_other_levels_are_display_only(self):
        f=real_frame();r=analyze(f,None,signal_level=1)
        self.assertTrue(all(s['level']==1 for s in r['signals']))
        self.assertTrue(all(s['level']!=1 for s in r['display_signals']))
        self.assertTrue(all(s['id'] in r['signal_status'] for s in r['display_signals']))
        # Trading at level 0 yields the same pen-level events that level 1 shows for display.
        r0=analyze(f,None,signal_level=0)
        self.assertEqual([s['id'] for s in r0['signals']],[s['id'] for s in r['display_signals'] if s['level']==0])

    def test_overlays_label_display_level(self):
        f=real_frame();res=analyze_all(f,{'chan_level':1})
        shown=[o for o in res['overlays'] if o.get('trade') is False]
        if res['chan']['display_signals']:
            self.assertTrue(shown)
            self.assertTrue(all(o['group']=='缠论' and ('买' in o['label'] or '卖' in o['label']) for o in shown))


if __name__=='__main__':
    unittest.main()
