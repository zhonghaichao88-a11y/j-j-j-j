"""V7.6.6：pine_v5.live_contract 的实盘能力报告必须跟随主级别 tf（不再硬编码 5m）。

契约 §7/家长核对项1：live_capability(program, tf) / require_live(program, tf)
返回 timeframe=tf，描述含当前级别；默认 tf='5m' 兼容旧调用。离线、确定性。
"""
import unittest
from alpha_v7_pine import compile_source
from pine_v5.live_contract import live_capability, require_live

# 最小可自动交易策略：仅市价 entry，无数量/挂单/平仓参数。
SRC = 'strategy("p")\nif close>open\n    strategy.entry("L",strategy.long)'


class LiveContractTfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.program = compile_source(SRC)

    def test_default_is_5m(self):
        rep = live_capability(self.program)
        self.assertTrue(rep['supported'])
        self.assertEqual(rep['timeframe'], '5m')
        self.assertIn('当前5m', rep['description'])

    def test_tf_15m(self):
        rep = live_capability(self.program, '15m')
        self.assertEqual(rep['timeframe'], '15m')
        self.assertIn('当前15m', rep['description'])
        self.assertNotIn('固定5分钟', rep['description'])

    def test_require_live_1h(self):
        rep = require_live(self.program, '1h')
        self.assertEqual(rep['timeframe'], '1h')
        self.assertIn('当前1h', rep['description'])


if __name__ == '__main__':
    unittest.main()
