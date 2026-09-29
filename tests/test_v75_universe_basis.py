import unittest
from unittest.mock import patch

import tv_universe


def ticker(sym, pct, pct_utc8, qv=50e6, last=100.0):
    return {"symbol": sym, "last": last, "pct": pct, "pct_utc8": pct_utc8,
            "pct_utc0": None, "quote_volume": qv}


class UniverseBasisTests(unittest.TestCase):
    def setUp(self):
        tv_universe.configure({"basis": "24h", "top": 20})

    def test_default_is_24h(self):
        self.assertEqual(tv_universe.snapshot()["basis"], "24h")

    def test_switch_to_utc8_uses_today_ranking(self):
        data = [
            ticker("A-USDT-SWAP", 5.0, 20.0),
            ticker("B-USDT-SWAP", 30.0, 8.0),
            ticker("C-USDT-SWAP", -2.0, 15.0),  # 今日涨、但24h仍跌
        ]
        with patch.object(tv_universe.okx_client, "fetch_swap_tickers", return_value=data):
            tv_universe.configure({"basis": "utc8"})
            tv_universe.top_gainers([], force=True)
            syms = tv_universe.snapshot()["symbols"]
        # 今日口径：A(20)>C(15)>B(8)，均为正且成交额达标
        self.assertEqual(syms, ["A-USDT-SWAP", "C-USDT-SWAP", "B-USDT-SWAP"])

    def test_24h_uses_rolling_ranking(self):
        data = [
            ticker("A-USDT-SWAP", 5.0, 20.0),
            ticker("B-USDT-SWAP", 30.0, 8.0),
            ticker("C-USDT-SWAP", -2.0, 15.0),
        ]
        with patch.object(tv_universe.okx_client, "fetch_swap_tickers", return_value=data):
            tv_universe.configure({"basis": "24h"})
            tv_universe.top_gainers([], force=True)
            syms = tv_universe.snapshot()["symbols"]
        # 24h口径：B(30)>A(5)；C为负剔除
        self.assertEqual(syms, ["B-USDT-SWAP", "A-USDT-SWAP"])

    def test_invalid_basis_rejected(self):
        with self.assertRaises(ValueError):
            tv_universe.configure({"basis": "nope"})

    def test_missing_today_value_skipped(self):
        data = [
            {"symbol": "X-USDT-SWAP", "last": 1.0, "pct": 9.0, "pct_utc8": None,
             "pct_utc0": None, "quote_volume": 50e6},
            ticker("Y-USDT-SWAP", 3.0, 4.0),
        ]
        with patch.object(tv_universe.okx_client, "fetch_swap_tickers", return_value=data):
            tv_universe.configure({"basis": "utc8"})
            tv_universe.top_gainers([], force=True)
            self.assertEqual(tv_universe.snapshot()["symbols"], ["Y-USDT-SWAP"])

    def test_turnover_gate_still_applies_in_today_basis(self):
        data = [
            ticker("LOWQ-USDT-SWAP", 5.0, 40.0, qv=2e6),   # 今日暴涨但成交额不达标
            ticker("GOOD-USDT-SWAP", 4.0, 10.0, qv=50e6),
        ]
        with patch.object(tv_universe.okx_client, "fetch_swap_tickers", return_value=data):
            tv_universe.configure({"basis": "utc8"})
            tv_universe.top_gainers([], force=True)
            self.assertEqual(tv_universe.snapshot()["symbols"], ["GOOD-USDT-SWAP"])


if __name__ == "__main__":
    unittest.main()
