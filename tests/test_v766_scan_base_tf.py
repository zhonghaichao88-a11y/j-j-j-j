# -*- coding: utf-8 -*-
"""V7.6.6 扫描路径 base_tf 全链路离线测试（Agent B 负责部分）。

只验证 predict() 的 v7 分支：
  1. 所有 v7 策略都经 alpha_v7_feed.bundle 取数，并以 base_tf 调用；
  2. frames 键 = base_tf（+ chan_quant 且 chan_mtf 时的两个嵌套高周期）；
  3. 历史引导后重新取报价（ticker_last / spread_bps 来自引导后的最新报价）；
  4. summary.entry_timeframe = base_tf，completed_bars 与 frames 键一致；
  5. base_tf 缺省时按 '5m'。

不连接交易所、不写真实持仓、不做网络请求；全部用 mock 替换。
运行：python3 -m pytest tests/test_v766_scan_base_tf.py -q
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import alpha_fast_mode as mode
import alpha_v7_feed as feed


def _flat_decision(symbol, data):
    """decide() 的占位返回：原样回传 data，供断言 frames 键/报价。"""
    return {
        "signal": "FLAT",
        "confidence": 0.0,
        "tp": 0.0,
        "sl": 0.0,
        "reason": "测试占位",
        "fast_strategy": {"tier": "等待"},
        "entry_price_confirmation": {"entry_quality": "等待"},
    }


class ScanBaseTfTests(unittest.TestCase):
    def setUp(self):
        # 保存被测模块上会被打补丁的真实对象，tearDown 还原。
        self._orig_version = mode.FAST_ACTIVE_VERSION
        self._orig_okx = mode.okx_client
        self._orig_config = mode.config
        mode.FAST_ACTIVE_VERSION = "v7"

    def tearDown(self):
        mode.FAST_ACTIVE_VERSION = self._orig_version
        mode.okx_client = self._orig_okx
        mode.config = self._orig_config

    def _run_scan(self, runtime_params):
        """以指定运行时参数跑一次 v7 扫描，返回 (bundle_kwargs, decide_data)。"""
        captured = {}

        def fake_bundle(exchange, symbol, multi=True, count=1500, now_ms=None, base_tf="5m"):
            captured["bundle_kwargs"] = dict(exchange=exchange, symbol=symbol,
                                            multi=multi, count=count,
                                            now_ms=now_ms, base_tf=base_tf)
            # 按 feed 的周期表造帧：键 = bundle_tfs(base_tf, multi)。
            return {tf: {"close": np.arange(200, dtype=float),
                         "ts": np.arange(200, dtype=np.int64)}
                    for tf in feed.bundle_tfs(base_tf, multi)}

        ticker_calls = {"n": 0}
        def fake_ticker(symbol):
            # 引导后重新取价：固定 bid/ask/last，便于断言点差。
            ticker_calls["n"] += 1
            return {"last": 100.0, "bid": 99.9, "ask": 100.1}

        fake_okx = types.SimpleNamespace(
            is_connected=True,
            _exchange=MagicMock(name="exchange"),
            get_ticker=fake_ticker,
        )
        fake_config = types.SimpleNamespace(
            trading=types.SimpleNamespace(get_ccxt_symbol=lambda s: s))

        decide_data = {}
        def fake_decide(symbol, data):
            decide_data.update(data)
            return _flat_decision(symbol, data)

        with patch("alpha_fast_v7.get_runtime_params", return_value=runtime_params), \
             patch("alpha_fast_v7.decide", side_effect=fake_decide), \
             patch("alpha_v7_feed.bundle", side_effect=fake_bundle):
            mode.okx_client = fake_okx
            mode.config = fake_config
            result = mode.predict("BTC-USDT-SWAP")

        captured["ticker_calls"] = ticker_calls["n"]
        captured["decide_data"] = decide_data
        captured["result"] = result
        return captured

    def test_default_5m_chan_mtf_keeps_legacy_keys(self):
        p = {"strategy": "chan_quant", "chan_mtf": 1}  # 不带 base_tf -> 5m
        cap = self._run_scan(p)
        kw = cap["bundle_kwargs"]
        self.assertEqual(kw["base_tf"], "5m")
        self.assertTrue(kw["multi"])
        # 历史现状：5m 主级别嵌套 15m + 1h。
        self.assertEqual(set(cap["decide_data"]["frames"].keys()), {"5m", "15m", "1h"})
        self.assertEqual(cap["decide_data"]["summary"]["entry_timeframe"], "5m")

    def test_base_tf_15m_maps_to_1h_4h(self):
        p = {"strategy": "chan_quant", "chan_mtf": 1, "base_tf": "15m"}
        cap = self._run_scan(p)
        kw = cap["bundle_kwargs"]
        self.assertEqual(kw["base_tf"], "15m")
        self.assertTrue(kw["multi"])
        # 主 15m -> 高 1h + 4h。
        self.assertEqual(set(cap["decide_data"]["frames"].keys()), {"15m", "1h", "4h"})
        self.assertEqual(cap["decide_data"]["summary"]["entry_timeframe"], "15m")
        self.assertEqual(set(cap["decide_data"]["summary"]["completed_bars"].keys()),
                         {"15m", "1h", "4h"})

    def test_base_tf_1h_maps_to_4h_1d(self):
        p = {"strategy": "chan_quant", "chan_mtf": 1, "base_tf": "1h"}
        cap = self._run_scan(p)
        kw = cap["bundle_kwargs"]
        self.assertEqual(kw["base_tf"], "1h")
        self.assertTrue(kw["multi"])
        # 主 1h -> 高 4h + 1d。
        self.assertEqual(set(cap["decide_data"]["frames"].keys()), {"1h", "4h", "1d"})
        self.assertEqual(cap["decide_data"]["summary"]["entry_timeframe"], "1h")

    def test_non_chan_strategy_single_frame_only(self):
        # 非缠论策略：不嵌套高周期，frames 只含 base_tf。
        for bt in ("5m", "15m", "1h"):
            p = {"strategy": "ema_cross", "chan_mtf": 1, "base_tf": bt}
            cap = self._run_scan(p)
            kw = cap["bundle_kwargs"]
            self.assertEqual(kw["base_tf"], bt)
            self.assertFalse(kw["multi"], msg=f"{bt} 非缠论不应多周期")
            self.assertEqual(set(cap["decide_data"]["frames"].keys()), {bt})

    def test_chan_quant_but_mtf_off_single_frame(self):
        # 缠论但关闭 chan_mtf：也不嵌套高周期。
        p = {"strategy": "chan_quant", "chan_mtf": 0, "base_tf": "15m"}
        cap = self._run_scan(p)
        self.assertFalse(cap["bundle_kwargs"]["multi"])
        self.assertEqual(set(cap["decide_data"]["frames"].keys()), {"15m"})

    def test_quote_refetched_after_history_bootstrap(self):
        # 引导后重新取报价：ticker_last=100，spread_bps 由 bid/ask 算出。
        p = {"strategy": "ema_cross", "base_tf": "5m"}
        cap = self._run_scan(p)
        d = cap["decide_data"]
        self.assertEqual(d["ticker_last"], 100.0)
        # bid=99.9 ask=100.1 -> spread = 0.2/100.0*1e4 = 20.0 bps
        self.assertAlmostEqual(d["spread_bps"], 20.0, places=3)
        # 至少引导后取过一次报价。
        self.assertGreaterEqual(cap["ticker_calls"], 1)


class BacktestBaseTfRouteTests(unittest.TestCase):
    """/tv/api/backtest 按 req.params.base_tf 拉 K 线并把 base_tf 传给 replay。"""

    def _call_backtest(self, params, count=400):
        import tv_plus
        captured = {"bars_tfs": [], "replay_params": None}

        def fake_bars(symbol, tf="5m", before=None, limit=300):
            captured["bars_tfs"].append(tf)
            # 每次返回一页（300 根），时间戳严格递增、单调向后翻页。
            if before is None:
                start = 10_000_000
            else:
                start = before - 300 * 60_000
            return [{"timestamp": start + i * 60_000, "open": 1.0, "high": 1.1,
                     "low": 0.9, "close": 1.0, "volume": 1.0, "confirmed": True}
                    for i in range(300)]

        class FakeReplay:
            @staticmethod
            def simulate(rows, params, capital, trailing, partial):
                captured["replay_params"] = dict(params)
                captured["row_count"] = len(rows)
                return {"success": True, "trades": []}

        req = types.SimpleNamespace(symbol="BTC-USDT-SWAP", count=count,
                                    params=params, capital=10000.0,
                                    trailing=False, partial=False)
        with patch.object(tv_plus.strategy, "validate_params", lambda p: None), \
             patch.object(tv_plus, "bars", side_effect=fake_bars), \
             patch.dict(sys.modules, {"alpha_v7_replay": FakeReplay}):
            out = tv_plus.backtest(req)
        return captured, out

    def test_backtest_uses_requested_base_tf(self):
        captured, out = self._call_backtest({"base_tf": "15m"})
        self.assertTrue(out["success"])
        # 分页拉取全程都用 15m，不再写死 5m。
        self.assertTrue(captured["bars_tfs"])
        self.assertTrue(all(tf == "15m" for tf in captured["bars_tfs"]))
        # replay 收到的参数带 base_tf=15m。
        self.assertEqual(captured["replay_params"]["base_tf"], "15m")

    def test_backtest_default_5m_unchanged(self):
        captured, out = self._call_backtest({})  # 不带 base_tf
        self.assertTrue(all(tf == "5m" for tf in captured["bars_tfs"]))
        self.assertEqual(captured["replay_params"].get("base_tf"), "5m")

    def test_backtest_rejects_unsupported_tf(self):
        import tv_plus
        req = types.SimpleNamespace(symbol="BTC-USDT-SWAP", count=400,
                                    params={"base_tf": "4h"}, capital=10000.0,
                                    trailing=False, partial=False)
        with patch.object(tv_plus.strategy, "validate_params", lambda p: None):
            with self.assertRaises(Exception) as cm:
                tv_plus.backtest(req)
        self.assertIn("主级别", str(cm.exception.detail if hasattr(cm.exception, "detail") else cm.exception))


if __name__ == "__main__":
    unittest.main()
