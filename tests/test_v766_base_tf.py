"""V7.6.6 主级别 base_tf 全链路 —— Agent C 归属的离线确定性测试。

覆盖契约 §8 中归 C 的项：
  - base_tf 参数校验：合法/非法值、默认 5m
  - 高周期映射：higher_tfs 三档；decide 读取 frames[base_tf] 与对应高周期
  - max_hold 随级别：同 max_hold_bars，15m/1h 的 max_seconds 为 3×/12×
  - replay base_tf：simulate 在各主级别下 frames 键与高周期正确

不读取 .env、不连接交易所、不启动交易；全部用合成 K 线。
"""
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import alpha_fast_v7 as v
from alpha_v7_feed import higher_tfs, tf_ms
import alpha_v7_replay as replay


def trigger_frame(ms, n=300):
    """构造一根可触发唐奇安多头的合成主周期帧：前面 288+ 根平在 100，末根收 101 突破前高。"""
    c = np.full(n, 100.0)
    f = dict(ts=np.arange(n) * ms, open=c.copy(), close=c.copy(),
             high=np.full(n, 100.0), low=np.full(n, 99.0), volume=np.ones(n) * 100)
    f['close'][-1] = 101.0
    f['high'][-1] = 101.0
    return f


def make_rows(ms, n=1600):
    return [dict(timestamp=i * ms, open=100, high=101, low=99, close=100,
                 volume=10, confirmed=True) for i in range(n)]


class BaseTfParamTests(unittest.TestCase):
    """base_tf 参数校验：合法/非法值、默认 5m。"""

    def setUp(self):
        v._RUNTIME = {}

    def tearDown(self):
        v._RUNTIME = {}

    def test_default_is_5m(self):
        self.assertEqual(v.validate_params()['base_tf'], '5m')

    def test_valid_values_accepted(self):
        for bt in ('5m', '15m', '1h'):
            self.assertEqual(v.validate_params({'base_tf': bt})['base_tf'], bt)

    def test_invalid_values_rejected(self):
        for bad in ('4h', '1d', '2m', '30m', '', 5, None):
            with self.assertRaises(ValueError, msg=f'base_tf={bad!r} 应被拒绝'):
                v.validate_params({'base_tf': bad})

    def test_base_tf_not_treated_as_numeric(self):
        # base_tf 是字符串枚举，不应落入“必须是有效数字”的数值校验分支。
        p = v.validate_params({'base_tf': '15m'})
        self.assertEqual(p['base_tf'], '15m')

    def test_unknown_param_still_rejected(self):
        with self.assertRaises(ValueError):
            v.validate_params({'base_tf': '15m', 'not_a_param': 1})


class HigherTfMappingTests(unittest.TestCase):
    """高周期映射：higher_tfs 三档正确（契约 §1 嵌套，不得另立）。"""

    def test_higher_tfs_mapping(self):
        self.assertEqual(higher_tfs('5m'), ('15m', '1h'))
        self.assertEqual(higher_tfs('15m'), ('1h', '4h'))
        self.assertEqual(higher_tfs('1h'), ('4h', '1d'))

    def test_tf_ms_ratios(self):
        self.assertEqual(tf_ms('15m') // tf_ms('5m'), 3)
        self.assertEqual(tf_ms('1h') // tf_ms('5m'), 12)


class DecideFrameSelectionTests(unittest.TestCase):
    """decide 全程以 frames[base_tf] 为主帧，不再写死 frames['5m']。"""

    def setUp(self):
        v._RUNTIME = {}

    def tearDown(self):
        v._RUNTIME = {}

    def test_decide_validates_frames_at_base_tf_not_5m(self):
        # 15m 主帧不足、5m 帧充足 → 报错必须指向 15m（证明校验/取帧用的是 frames[base_tf]）。
        good5 = trigger_frame(300000)
        tiny15 = trigger_frame(900000)
        tiny15 = {k: (x[:10] if hasattr(x, '__len__') else x) for k, x in tiny15.items()}
        as_of = int(tiny15['ts'][-1] + 900000)
        data = {'frames': {'5m': good5, '15m': tiny15},
                'ticker_last': 100.0, 'missing': [], 'as_of_ms': as_of}
        r = v.decide('X', data, {'base_tf': '15m'})
        self.assertTrue(r['market_context']['no_trade'])
        # 报错精确指向 15m 主帧（而非 5m）：证明校验/取帧用的是 frames[base_tf]。
        self.assertEqual(r['reason'], 'V7：15m 已收盘 K 线不足或长度不一致')

    def test_decide_works_with_only_bt_frame_no_5m_key(self):
        # 只提供 frames['15m']，完全没有 '5m' 键：decide 仍能触发 LONG（不再隐式依赖 5m）。
        f = trigger_frame(900000)
        data = {'frames': {'15m': f}, 'ticker_last': 101.0, 'missing': [],
                'as_of_ms': int(f['ts'][-1] + 900000)}
        r = v.decide('X', data, {'base_tf': '15m', 'sl_mode': 'fixed', 'sl_fixed': 0.01})
        self.assertEqual(r['signal'], 'LONG')
        self.assertEqual(r['timeframe'], '15m 收盘触发')
        self.assertEqual(r['fast_strategy']['v7_base_tf'], '15m')
        self.assertEqual(v.position_meta(r)['v7_base_tf'], '15m')

    def test_validate_frame_thin_wrapper_equals_5m(self):
        f = trigger_frame(300000)
        as_of = int(f['ts'][-1] + 300000)
        self.assertEqual(v.validate_frame({'5m': f}, '5m', as_of), '')
        self.assertEqual(v.validate_5m({'5m': f}, as_of), '')


class MaxHoldScaleTests(unittest.TestCase):
    """max_hold 随级别：同 max_hold_bars，15m/1h 的 max_seconds 为 3×/12×。"""

    def setUp(self):
        v._RUNTIME = {}

    def tearDown(self):
        v._RUNTIME = {}

    def test_max_seconds_scales_with_level(self):
        results = {}
        for bt in ('5m', '15m', '1h'):
            ms = tf_ms(bt)
            f = trigger_frame(ms)
            data = {'frames': {bt: f}, 'ticker_last': 101.0, 'missing': [],
                    'as_of_ms': int(f['ts'][-1] + ms)}
            r = v.decide('X', data, {'base_tf': bt, 'sl_mode': 'fixed', 'sl_fixed': 0.01})
            self.assertEqual(r['signal'], 'LONG', bt)
            results[bt] = r['fast_strategy']['max_seconds']
        base = results['5m']
        self.assertEqual(base, 12 * 300)           # 5m: 12 根 × 300s
        self.assertEqual(results['15m'], base * 3)  # 15m: 3×
        self.assertEqual(results['1h'], base * 12)  # 1h: 12×


class ReplayBaseTfTests(unittest.TestCase):
    """replay base_tf：simulate 按主级别构建主帧与高周期（只用已收盘 K 线）。"""

    def test_aggregate_closed_only(self):
        # 12 根 5m → 恰好 4 根 15m；桶起点对齐、只用完整闭合 K 线。
        n = 12
        arrays = dict(ts=np.arange(n) * 300000, open=np.ones(n), close=np.ones(n),
                      high=np.ones(n) * 2, low=np.ones(n) * 0.5, volume=np.ones(n))
        h = replay.aggregate(arrays, 900000, 300000)
        self.assertEqual(len(h['ts']), 4)
        self.assertEqual(int(h['ts'][0]), 0)
        self.assertEqual(int(h['ts'][1]), 900000)

    def _capture_frames(self, bt, ms):
        captured = {}
        orig = replay.strategy.decide

        def fake_decide(symbol, data, params=None):
            captured['keys'] = set(data['frames'].keys())
            return {'signal': 'FLAT'}

        replay.strategy.decide = fake_decide
        try:
            replay.simulate(make_rows(ms, 1600),
                            {'base_tf': bt, 'strategy': 'chan_quant', 'chan_mtf': 1}, 1000)
        finally:
            replay.strategy.decide = orig
        return captured['keys']

    def test_replay_frames_keys_5m(self):
        self.assertEqual(self._capture_frames('5m', 300000), {'5m', '15m', '1h'})

    def test_replay_frames_keys_15m(self):
        self.assertEqual(self._capture_frames('15m', 900000), {'15m', '1h', '4h'})

    def test_replay_frames_keys_1h(self):
        self.assertEqual(self._capture_frames('1h', 3600000), {'1h', '4h', '1d'})

    def test_replay_gap_check_uses_base_tf_step(self):
        # 15m 主周期：两行间距必须是 900000；若混入 5m 间距应被判缺口。
        rows = make_rows(900000, 1600)
        rows[1] = dict(rows[1]); rows[1]['timestamp'] = rows[0]['timestamp'] + 300000
        with self.assertRaises(ValueError):
            replay.simulate(rows, {'base_tf': '15m', 'strategy': 'ema_cross'}, 1000)


if __name__ == '__main__':
    unittest.main()
