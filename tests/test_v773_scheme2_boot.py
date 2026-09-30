"""V7.7.3 方案二：首次处理一个币用长历史（60 天 5 分钟 / 约 62 天 30 分钟）回放建立一买状态；
旧版（只回放约 5 天）建立的状态在无持仓时丢弃重建，有持仓时保留。不连网。"""
import unittest
from unittest.mock import patch
import numpy as np
import alpha_v7_scheme2 as s2
import alpha_fast_mode as fm


def frame(n, ms, end=1_800_000_000_000):
    end = end // ms * ms
    ts = end - (n - 1 - np.arange(n, dtype=np.int64)) * ms
    c = 100.0 + np.sin(np.arange(n) / 7.0)
    return dict(ts=ts, open=c, high=c + .5, low=c - .5, close=c, volume=np.ones(n))


class BootTests(unittest.TestCase):
    def setUp(self):
        s2.reset_state(); s2._LAST.clear(); s2._LVL.clear()

    def test_needs_boot_for_new_and_old_version_state(self):
        self.assertTrue(s2.needs_boot('A'))
        with s2._LOCK:
            st = s2._load(); old = s2._new_state(); old.pop('ver'); old['last_T'] = 123; st['A'] = old
            new = s2._new_state(); new['last_T'] = 123; st['B'] = new
        self.assertTrue(s2.needs_boot('A'))                                   # 旧版、无持仓 → 重建
        self.assertFalse(s2.needs_boot('A', {'side': 'long'}))                # 旧版、有持仓 → 保留
        self.assertFalse(s2.needs_boot('B'))

    def test_old_state_is_rebuilt_when_flat_and_kept_with_position(self):
        fr = {'5m': frame(600, 300000), '30m': frame(300, 1800000), '1d': frame(120, 86400000)}
        with s2._LOCK:
            old = s2._new_state(); old.pop('ver'); old['last_T'] = int(fr['5m']['ts'][-50]) + 300000
            old['p1']['1'] = 1.0; s2._load()['A'] = dict(old); s2._load()['B'] = dict(old, p1={'1': 1.0, '-1': None})
        r = s2.evaluate('A', fr, None, 0.6)
        self.assertTrue(any('首次处理' in n for n in r['notes']))
        self.assertEqual(s2._load()['A']['ver'], s2.STATE_VER)
        s2.evaluate('B', fr, {'side': 'long', 's2_stages': '2'}, 0.6)
        self.assertNotIn('ver', s2._load()['B'])

    def test_scheme2_eval_uses_long_history_only_on_boot(self):
        short5, short30 = frame(1500, 300000), frame(1500, 1800000)
        long5, long30 = frame(s2.BOOT_5M, 300000), frame(s2.BOOT_30M, 1800000)
        seen = []
        def fake_eval(symbol, fr, position, breadth, now_ms=None):
            seen.append((len(fr['5m']['ts']), len(fr['30m']['ts'])))
            with s2._LOCK: s2._load()[symbol] = dict(s2._new_state(), last_T=1)
            return dict(T=1, entry=None, action=None, notes=[])
        def fake_frame(ex, cs, tf, count=1500, now_ms=None):
            return long5 if tf == '5m' else long30
        def fake_anchor(ex, cs, tf, count=1500, now_ms=None):
            return short30 if tf == '30m' else frame(300, 86400000)
        with patch('alpha_v7_feed.anchored_frame', fake_anchor), patch('alpha_v7_feed.frame', fake_frame), \
                patch.object(s2, 'evaluate', fake_eval), patch.object(fm, '_scheme2_breadth', lambda now: 0.6):
            fm._scheme2_eval('A-USDT-SWAP', 'A/USDT:USDT', {'5m': short5}, 1.8e12)
            fm._scheme2_eval('A-USDT-SWAP', 'A/USDT:USDT', {'5m': short5}, 1.8e12)
        self.assertEqual(seen, [(s2.BOOT_5M, s2.BOOT_30M), (1500, 1500)])


if __name__ == '__main__':
    unittest.main()
