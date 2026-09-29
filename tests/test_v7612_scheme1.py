"""V7.6.12 方案一：顺势 3买/3卖 + 宽止损 + 2ATR 移动止损。"""
import unittest
from unittest.mock import patch
import numpy as np
import alpha_fast_v7 as v7

BAR = 900000


def trend(n, slope, seed=1, start=100.0):
    rng = np.random.default_rng(seed); c = start * np.exp(np.cumsum(rng.normal(slope, 0.004, n))); o = np.r_[c[0], c[:-1]]
    return dict(ts=np.arange(n, dtype=np.int64) * BAR, open=o, close=c, high=np.maximum(o, c) * 1.002,
                low=np.minimum(o, c) * 0.998, volume=np.ones(n))


def event(f, kind='T3', side=1, dist=0.03):
    px = float(f['close'][-1]); ts = int(f['ts'][-1])
    return dict(id='e', label=('3买' if side == 1 else '3卖') if kind == 'T3' else '1买', bsp_class=3 if kind == 'T3' else 1,
                kind=kind, side=side, known_at=ts, ts=ts - 2 * BAR, price=px * (1 - side * dist),
                invalidation=px * (1 - side * dist), evidence={}, zone_id=None)


class ParamTests(unittest.TestCase):
    def test_switch_forces_the_whole_scheme(self):
        p = v7.validate_params(dict(chan_scheme1=1, base_tf='5m', chan_level=1, chan_buy1=1, chan_buy2=1, runner=1, max_hold_bars=12))
        for k, v in v7.SCHEME1.items(): self.assertEqual(p[k], v, k)
        self.assertEqual(v7.validate_params(dict(chan_scheme1=0))['base_tf'], v7.PARAMS['base_tf'])


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.f = trend(2000, 0.0004); self.btc = trend(2000, 0.0003, seed=2, start=60000)
        self.a = float(v7.atr_arr(self.f)[-1]); self.px = float(self.f['close'][-1])

    def plan(self, ev, btc=None, direction=1):
        return v7.scheme1_plan(self.f, ev, direction, self.px, self.a, self.btc if btc is None else btc)

    def test_uptrend_long_is_accepted_with_one_atr_beyond_the_pivot(self):
        ev = event(self.f); r = self.plan(ev)
        self.assertIsInstance(r, dict, r)
        self.assertAlmostEqual(r['stop'], ev['invalidation'] - self.a)

    def test_rejections(self):
        self.assertIn('只做3买', self.plan(event(self.f, kind='T1')))
        self.assertIn('BTC方向', self.plan(event(self.f), btc=trend(2000, -0.0003, seed=3, start=60000)))
        self.assertIn('4小时', self.plan(event(self.f, side=-1), direction=-1))            # 4h 向上，不做空
        self.assertIn('不足1.72%', self.plan(event(self.f, dist=0.002)))
        self.assertIn('缺少BTC', self.plan(event(self.f), btc={'ts': [], 'close': []}))
        stale = {k: v[:-10] for k, v in self.btc.items()}
        self.assertIn('不同步', self.plan(event(self.f), btc=stale))

    def test_h4_ema_uses_only_complete_4h_bars(self):
        f = {k: v[:-3] for k, v in self.f.items()}      # 最后一根 4h 不完整
        full = v7.h4_ema50(self.f); part = v7.h4_ema50(f)
        closes = self.f['close'][15::16][:len(self.f['close']) // 16]
        self.assertAlmostEqual(full, v7.ema_last(closes, 50))
        self.assertAlmostEqual(part, v7.ema_last(closes[:-1], 50))


class DecideTests(unittest.TestCase):
    def test_end_to_end_entry_and_exit_settings(self):
        f = trend(2000, 0.0004); btc = trend(2000, 0.0003, seed=2, start=60000); ev = event(f)
        fake = dict(candidates={}, regime='测试', values={}, events=[],
                    chan=dict(signals=[ev], signal_status={}, strokes=[], segments=[], zones=[]))
        data = dict(frames={'15m': f}, ticker_last=float(f['close'][-1]), spread_bps=2,
                    as_of_ms=int(f['ts'][-1]) + BAR + 1000, btc_frame=btc)
        with patch.object(v7, 'analyze', return_value=fake):
            out = v7.decide('X', data, dict(chan_scheme1=1))
        self.assertEqual(out['signal'], 'LONG', out['reason'])
        fs = out['fast_strategy']; a = float(v7.atr_arr(f)[-1])
        self.assertAlmostEqual(fs['sl_price'], ev['invalidation'] - a)
        self.assertEqual(fs['max_seconds'], 192 * 900)
        self.assertEqual(fs['v7_exit_config']['scheme1'], 1); self.assertEqual(fs['v7_exit_config']['close_confirm'], 0)
        self.assertEqual(fs['v7_chan_config']['chan_exit_opposite'], 0)
        risk = float(f['close'][-1]) - fs['sl_price']
        self.assertAlmostEqual(out['tp'], min(20 * risk / float(f['close'][-1]), 0.9))
        # 移动止损：盈利 1R 激活，回撤 2ATR（不加 0.6R 下限）
        pos = dict(entry=float(f['close'][-1]), side='long', base_sl_pct=out['sl'], **v7.position_meta(out))
        active, cb = v7.native_trail_config(pos)
        self.assertAlmostEqual(active, pos['entry'] + max(risk, pos['entry'] * pos['v7_round_cost'] * 1.5), places=6)
        self.assertAlmostEqual(cb, min(max(2 * a / active, .001), .10))
        with patch.object(v7, 'analyze', return_value=fake):
            self.assertIn('BTC', v7.decide('X', {**data, 'btc_frame': None}, dict(chan_scheme1=1))['reason'])


if __name__ == '__main__':
    unittest.main()
