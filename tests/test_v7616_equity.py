"""V7.6.16：实时权益读取——自动重试、短时缓存、成功后清掉旧提示、临时失败沿用最近读数。"""
import unittest
from unittest.mock import patch
import alpha_engine as ae


class Account:
    def __init__(self, seq): self.seq = list(seq); self.calls = 0
    def account(self):
        self.calls += 1; x = self.seq.pop(0)
        if isinstance(x, Exception): raise x
        return {'total': x, 'free': x / 2}


class EquityTests(unittest.TestCase):
    def setUp(self):
        ae._ACCOUNT_CACHE.update(ts=0.0, total=0.0, free=0.0, fails=0)
        self.p = [patch.object(ae.time, 'sleep', lambda s: None)]
        for x in self.p: x.start()

    def tearDown(self):
        for x in self.p: x.stop()

    def test_retry_then_success_and_clears_old_message(self):
        acc = Account([RuntimeError('timeout'), RuntimeError('502'), 1000.0])
        with patch.object(ae, 'alpha_live', acc), patch.dict(ae.STATE, {'live_error': '无法读取实时权益，暂停开仓: x'}):
            self.assertEqual(ae._live_account_snapshot(), (1000.0, 500.0))
            self.assertEqual(ae.STATE['live_error'], '')
        self.assertEqual(acc.calls, 3)

    def test_other_errors_are_not_cleared(self):
        with patch.object(ae, 'alpha_live', Account([10.0])), patch.dict(ae.STATE, {'live_error': '持仓读取失败'}):
            ae._live_account_snapshot(); self.assertEqual(ae.STATE['live_error'], '持仓读取失败')

    def test_cache_shared_but_pre_trade_reads_fresh(self):
        acc = Account([10.0, 20.0])
        with patch.object(ae, 'alpha_live', acc):
            ae._live_account_snapshot(); self.assertEqual(ae._live_account_snapshot(), (10.0, 5.0))   # 2 秒内复用
            self.assertEqual(ae._live_account_snapshot(max_age=0), (20.0, 10.0))                        # 下单前重新读
        self.assertEqual(acc.calls, 2)

    def test_all_retries_fail_raises_and_last_good_available(self):
        with patch.object(ae, 'alpha_live', Account([50.0])): ae._live_account_snapshot()
        with patch.object(ae, 'alpha_live', Account([RuntimeError('a')] * 3)):
            with self.assertRaises(RuntimeError): ae._live_account_snapshot(max_age=0)
        good = ae._last_good_account(); self.assertEqual(good[:2], (50.0, 25.0))
        ae._ACCOUNT_CACHE['ts'] -= 1000; self.assertIsNone(ae._last_good_account())

    def test_zero_equity_is_treated_as_failure(self):
        with patch.object(ae, 'alpha_live', Account([0.0, 0.0, 0.0])):
            with self.assertRaises(RuntimeError): ae._live_account_snapshot()


if __name__ == '__main__':
    unittest.main()
