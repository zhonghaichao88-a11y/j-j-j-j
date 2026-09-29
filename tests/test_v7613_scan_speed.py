"""V7.6.13 多币扫描加速：没有新K线不发请求、全市场报价一次取、分析结果缓存。"""
import types, unittest
from unittest.mock import patch
import numpy as np
import alpha_v7_feed as feed

MS = 300000


class FakeEx:
    id = 'fake'
    def __init__(self): self.calls = 0; self.now = 0
    def market(self, s): return {'id': 'X-USDT-SWAP'}
    def request(self, path, api, method, args):
        self.calls += 1
        last_closed = self.now // MS * MS - MS
        before = int(args['after']) if 'after' in args else last_closed + MS
        rows = [[str(t), '100', '101', '99', '100.5', '1', '0', '0', '1'] for t in range(before - MS, max(-1, before - 301 * MS), -MS)]
        return {'code': '0', 'data': rows}


class FrameSkipTests(unittest.TestCase):
    def setUp(self): feed._CACHE.clear()

    def test_no_request_until_next_bar_can_close(self):
        ex = FakeEx(); ex.now = 1000 * MS + 5000
        f = feed.frame(ex, 'X', '5m', 60, ex.now); n = ex.calls; self.assertEqual(f['ts'][-1], 999 * MS)
        for dt in (4000, 60000, MS - 6000):                       # 同一根K线内：只用缓存
            ex.now = 1000 * MS + 5000 + dt; feed.frame(ex, 'X', '5m', 60, ex.now)
        self.assertEqual(ex.calls, n)
        ex.now = 1001 * MS + 1000                                  # 下一根收盘后：立即重新请求
        g = feed.frame(ex, 'X', '5m', 60, ex.now); self.assertGreater(ex.calls, n); self.assertEqual(g['ts'][-1], 1000 * MS)

    def test_larger_count_is_not_served_from_a_smaller_cache(self):
        ex = FakeEx(); ex.now = 1000 * MS + 5000
        feed.frame(ex, 'X', '5m', 60, ex.now); n = ex.calls
        ex.now += 10000; feed.frame(ex, 'X', '5m', 500, ex.now); self.assertGreater(ex.calls, n)


class BatchTickerTests(unittest.TestCase):
    def setUp(self):
        import alpha_fast_mode as m; self.m = m; m._TICKER_BATCH.update(at=0.0, data={})

    def test_one_request_serves_all_symbols_then_falls_back(self):
        calls = {'all': 0, 'one': 0}
        def all_(): calls['all'] += 1; return [dict(symbol='A-USDT-SWAP', last=1.0, bid=.9, ask=1.1), dict(symbol='B-USDT-SWAP', last=2.0, bid=1.9, ask=2.1)]
        def one(s): calls['one'] += 1; return {'last': 3.0, 'bid': 2.9, 'ask': 3.1}
        fake = types.SimpleNamespace(fetch_swap_tickers=all_, get_ticker=one)
        with patch.object(self.m, 'okx_client', fake):
            self.assertEqual(self.m.batch_ticker('A-USDT-SWAP')['last'], 1.0)
            self.assertEqual(self.m.batch_ticker('B-USDT-SWAP')['last'], 2.0)
            self.assertEqual(self.m.batch_ticker('C-USDT-SWAP')['last'], 3.0)   # 不在全市场快照里 → 单币请求
        self.assertEqual(calls, {'all': 1, 'one': 1})

    def test_error_in_batch_request_falls_back(self):
        def boom(): raise RuntimeError('down')
        fake = types.SimpleNamespace(fetch_swap_tickers=boom, get_ticker=lambda s: {'last': 5.0, 'bid': 4.9, 'ask': 5.1})
        with patch.object(self.m, 'okx_client', fake):
            self.assertEqual(self.m.batch_ticker('A-USDT-SWAP')['last'], 5.0)


class AnalyzeCacheTests(unittest.TestCase):
    def test_same_bars_same_result_object(self):
        from alpha_v7_analysis import analyze
        rng = np.random.default_rng(3); c = 100 + np.cumsum(rng.normal(0, .3, 400)); o = np.r_[c[0], c[:-1]]
        f = dict(ts=np.arange(400, dtype=np.int64) * MS, open=o, close=c, high=np.maximum(o, c) + .1, low=np.minimum(o, c) - .1, volume=np.ones(400))
        a = analyze(f, {'chan_level': 0}); b = analyze({k: v.copy() for k, v in f.items()}, {'chan_level': 0})
        self.assertIs(a, b)
        self.assertIsNot(analyze(f, {'chan_level': 1}), a)
        g = {k: v.copy() for k, v in f.items()}; g['close'][-1] += 0.5; g['high'][-1] += 0.5
        self.assertIsNot(analyze(g, {'chan_level': 0}), a)


if __name__ == '__main__':
    unittest.main()
