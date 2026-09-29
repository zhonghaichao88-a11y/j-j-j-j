"""V7.6.15：信号到下单的分配——每笔满额风险、空位不够按成本占比排序、同方向上限。"""
import unittest
from unittest.mock import patch
import alpha_engine as ae


def v7(signal, sl, cost=0.002):
    return {'signal': signal, 'sl': sl, 'model_ready': True, 'strategy_mode': 'FAST',
            'fast_strategy': {'engine_version': 'v7', 'estimated_round_cost': cost}}


class PlanTests(unittest.TestCase):
    def test_new_signals_ranked_by_cost_share_and_get_full_weight(self):
        preds = {'HELD': v7('FLAT', 0.02), 'A': v7('LONG', 0.01), 'B': v7('SHORT', 0.04), 'C': v7('LONG', 0.02), 'D': {'signal': 'FLAT'}}
        order, full, msg = ae._v7_entry_plan(preds, {'HELD'}, 3)
        self.assertEqual(order[0], 'HELD')                 # 持仓先管理
        self.assertEqual(order[1:4], ['B', 'C', 'A'])      # 止损越宽、成本占比越低越先
        self.assertEqual(set(full), {'A', 'B', 'C'})       # 每笔都按设定的单笔风险
        self.assertIn('空位 2 个', msg); self.assertIn('B、C', msg)

    def test_no_message_when_enough_slots_and_non_v7_untouched(self):
        other = {'signal': 'LONG', 'sl': 0.01, 'model_ready': True, 'fast_strategy': {'engine_version': 'v6'}}
        order, full, msg = ae._v7_entry_plan({'X': other, 'A': v7('LONG', 0.02)}, set(), 5)
        self.assertEqual(full, ['A']); self.assertEqual(msg, ''); self.assertEqual(sorted(order), ['A', 'X'])


class SameSideTests(unittest.TestCase):
    def test_cap_counts_open_positions_of_the_same_side(self):
        pos = {'P1': {'side': 'long'}, 'P2': {'side': 'long'}, 'P3': {'side': 'short'}}
        with patch.dict(ae.STATE, {'positions': pos}):
            self.assertIn('上限 2/2', ae._same_side_blocked('N', v7('LONG', .02), {'max_same_side': 2}))
            self.assertEqual(ae._same_side_blocked('N', v7('SHORT', .02), {'max_same_side': 2}), '')
            self.assertEqual(ae._same_side_blocked('N', v7('LONG', .02), {'max_same_side': 0}), '')   # 0=不限制
            self.assertEqual(ae._same_side_blocked('N', v7('LONG', .02), {}), '')


if __name__ == '__main__':
    unittest.main()
