"""V7.8.0 方案三：ADX 动量多空 + BTC 日线 EMA50 大盘过滤。一个开关自动套用全部设置。不连网。"""
import unittest
from unittest.mock import patch
import numpy as np
import alpha_v7_scheme3 as S3
import alpha_fast_v7 as v7
import alpha_engine as ae
import tv_universe as tu

H, D = S3.H, S3.D


def walk(n, drift, seed=1, end=1_800_000_000_000):
    rng = np.random.default_rng(seed)
    end = end // H * H
    ts = end - (n - 1 - np.arange(n, dtype=np.int64)) * H
    c = 100 * np.exp(np.cumsum(drift + rng.normal(0, .01, n)))
    o = np.r_[c[0], c[:-1]]
    return dict(ts=ts, open=o, high=np.maximum(o, c) * 1.003, low=np.minimum(o, c) * .997, close=c, volume=np.ones(n))


def cut_at(f, flags):
    """截到最后一个信号为真的那根（让它成为最后一根已收盘K线）。"""
    i = int(np.flatnonzero(flags)[-1])
    return {k: v[:i + 1] for k, v in f.items()}


def btc(n, up, end_ms):
    ts = (end_ms // D - 1 - np.arange(n)[::-1]) * D
    c = np.linspace(100, 200, n) if up else np.linspace(200, 100, n)
    return dict(ts=ts.astype(np.int64), close=c)


class Scheme3Tests(unittest.TestCase):
    def test_one_switch_applies_everything(self):
        p = v7.validate_params({'chan_scheme3': 1, 'chan_scheme1': 1, 'chan_scheme2': 1, 'base_tf': '5m', 'runner': 1, 'close_confirm': 1})
        self.assertEqual(p['base_tf'], '1h')
        self.assertEqual((p['chan_scheme1'], p['chan_scheme2'], p['runner'], p['close_confirm'], p['chan_mtf']), (0, 0, 0, 0, 0))

    def test_long_signal_only_when_btc_above_ema50(self):
        f = walk(1500, .002)
        ent, _ = S3.signals(f); g = cut_at(f, ent); now = int(g['ts'][-1]) + H
        self.assertEqual(S3.evaluate(g, btc(300, True, now), now)['side'], 1)
        self.assertEqual(S3.evaluate(g, btc(300, False, now), now)['side'], 0)
        self.assertEqual(S3.evaluate(g, btc(40, True, now), now)['side'], 0)          # 日线不足 51 根

    def test_short_is_mirror_and_only_when_btc_below(self):
        f = walk(1500, -.002, seed=3)
        sent, _ = S3.signals(S3.mirror(f)); g = cut_at(f, sent); now = int(g['ts'][-1]) + H
        self.assertEqual(S3.evaluate(g, btc(300, False, now), now)['side'], -1)
        self.assertEqual(S3.evaluate(g, btc(300, True, now), now)['side'], 0)

    def test_unclosed_bar_is_ignored(self):
        f = walk(1500, .002)
        ent, _ = S3.signals(f); g = cut_at(f, ent); now = int(g['ts'][-1]) + H
        # 当前未收盘的那根不参与：把 now 提前 1 毫秒，最后一根不算已收盘
        ev = S3.evaluate(g, btc(300, True, now), now - 1)
        self.assertNotEqual(ev.get('bar_ts'), int(g['ts'][-1]))

    def test_targets_match_backtest(self):
        tp, sl = S3.targets(100.0, 1)
        self.assertAlmostEqual(tp, 100 * 1.001 * 1.01 / .999); self.assertAlmostEqual(sl, 75.0)
        tp, sl = S3.targets(100.0, -1)
        self.assertAlmostEqual(tp, 100 * .999 * .99 / 1.001); self.assertAlmostEqual(sl, 125.0)

    def test_exit_due_from_entry_bar(self):
        f = walk(1500, -.002, seed=5)
        _, ex = S3.signals(f); i = int(np.flatnonzero(ex)[-1])
        g = {k: v[:i + 1] for k, v in f.items()}; now = int(g['ts'][-1]) + H
        self.assertTrue(S3.exit_due(g, 1, int(g['ts'][i]), now))
        self.assertFalse(S3.exit_due(g, 1, int(g['ts'][i]) + H, now))              # 进场在出场信号之后

    def test_decide_builds_order(self):
        f = walk(1500, .002)
        ent, _ = S3.signals(f); g = cut_at(f, ent); now = int(g['ts'][-1]) + H + 1000
        px = float(g['close'][-1])
        data = dict(frames={'1h': g}, ticker_last=px, spread_bps=2.0, missing=[], as_of_ms=now, btc_1d=btc(300, True, now))
        out = v7.decide('ETH-USDT-SWAP', data, {'chan_scheme3': 1})
        fs = out['fast_strategy']
        self.assertEqual(out['signal'], 'LONG')
        self.assertTrue(fs['v7_scheme3']); self.assertEqual(fs['v7_base_tf'], '1h')
        self.assertAlmostEqual(out['sl'], .25); self.assertAlmostEqual(out['tp'], 1.001 * 1.01 / .999 - 1)
        meta = v7.position_meta(out)
        self.assertTrue(meta['v7_scheme3']); self.assertEqual(meta['s3_bar_ts'], int(g['ts'][-1]))

    def test_engine_limits_and_size(self):
        cfg = dict(ae.DEFAULT, max_positions=4, max_same_side=2, cooldown_minutes=15, leverage=3)
        with patch.object(v7, 'get_runtime_params', return_value={'chan_scheme3': 1}):
            c = ae._scheme3_cfg(cfg)
            self.assertEqual((c['max_positions'], c['max_same_side'], c['cooldown_minutes']), (10, 0, 0))
        with patch.object(v7, 'get_runtime_params', return_value={'chan_scheme3': 0}):
            self.assertIs(ae._scheme3_cfg(cfg), cfg)
        self.assertAlmostEqual(ae._scheme3_notional(1000, 1000, cfg), 100.0)          # 权益 10%
        self.assertAlmostEqual(ae._scheme3_notional(1000, 20, cfg), 48.0)             # 可用不够时按 可用×杠杆×80%

    def test_entry_guard_window_and_targets(self):
        bar = 1_800_000_000_000 // H * H
        pred = dict(signal='LONG', fast_strategy=dict(engine_version='v7', v7_scheme3=dict(bar_ts=bar), v7_base_tf='1h',
                                                      bar_ts=bar, signal_id=f'X|v7|s3|{bar}|LONG'))
        with patch.dict(ae.STATE, {'v7_attempted': {}}):
            self.assertTrue(ae._v7_entry_guard('X', pred, 50.0, (bar + H + 60000) / 1000))
            self.assertAlmostEqual(pred['sl'], .25); self.assertAlmostEqual(pred['fast_strategy']['sl_price'], 37.5)
            p2 = dict(pred, fast_strategy=dict(pred['fast_strategy'], signal_id='Y'))
            self.assertTrue(ae._v7_entry_guard('X', p2, 50.0, (bar + H + 50 * 60000) / 1000))           # 同一小时内都可进场
            p3 = dict(pred, fast_strategy=dict(pred['fast_strategy'], signal_id='Z'))
            self.assertFalse(ae._v7_entry_guard('X', p3, 50.0, (bar + 2 * H + 60000) / 1000))           # 下一根已收盘 → 过期

    def test_universe_top100_crypto_by_volume(self):
        rows = [dict(symbol=f'C{i}-USDT-SWAP', pct=-1 if i % 2 else 1, quote_volume=(1e9 if i < 60 else 5e6) - i, last=1) for i in range(150)]   # 后面的成交额低于 1000 万也要选进前 100
        rows.append(dict(symbol='QQQ-USDT-SWAP', pct=1, quote_volume=2e9, last=1))
        with patch.dict(tu.S, {'cfg': dict(tu.DEFAULT_CFG), 'symbols': [], 'by_volume': ''}), \
             patch.object(tu.okx_client, 'fetch_swap_tickers', return_value=rows), \
             patch.object(tu, '_is_crypto', side_effect=lambda s: not s.startswith('QQQ')), \
             patch.object(v7, 'get_runtime_params', return_value={'chan_scheme3': 1}):
            tu._refresh()
            self.assertEqual(len(tu.S['symbols']), 100)
            self.assertNotIn('QQQ-USDT-SWAP', tu.S['symbols'])
            self.assertEqual(tu.S['symbols'][0], 'C0-USDT-SWAP'); self.assertEqual(tu.S['by_volume'], 's3')


if __name__ == '__main__':
    unittest.main()


class Scheme3MakerTests(unittest.TestCase):
    """方案三：先挂限价(maker)，价格跑开或超时没成交 → 市价补上（不漏单）；其他策略价格跑开仍不追。"""
    def _run(self, runaway):
        from test_v71_execution import executor
        from unittest.mock import Mock
        a, e = executor()
        e.fetch_ticker.return_value = {'bid': 99, 'ask': 101, 'last': 102}
        e.fetch_order.return_value = {'status': 'open', 'filled': 0}
        a.open = Mock(return_value={'filled': 1})
        r = a.open_maker('BTC-USDT-SWAP', 'long', 100, .012, .25, 3, client_order_id='AXtest', ttl=30,
                         fallback_to_market=True, ref_price=100, fallback_on_runaway=runaway)
        return r, a

    def test_runaway_falls_back_to_market_for_scheme3(self):
        r, a = self._run(True)
        self.assertEqual(r, {'filled': 1}); self.assertEqual(a.open.call_args.kwargs['client_order_id'], 'AXtestM1')

    def test_runaway_not_chased_by_default(self):
        r, a = self._run(False)
        self.assertEqual(r['maker_status'], 'ran_away'); a.open.assert_not_called()


def _executor_with_fallback():
    """test_v71_execution 的离线执行器，加上 _fallback_protection（不连网、不下真单）。"""
    import ast, types, time as _t, uuid
    from unittest.mock import Mock
    from pathlib import Path
    from typing import Optional
    from test_v7_repairs import load_functions
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / 'alpha_live.py').read_text())
    methods = {'_fee_cost', '_normalize_order', '_cancel_confirm_entry', 'open_maker', '_entry_protection', 'open', '_fallback_protection'}
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AlphaLiveExecutor')
    klass.body = [n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name in methods]
    ns = {'Optional': Optional, 'uuid': uuid, 'time': types.SimpleNamespace(time=_t.time, sleep=lambda _: None), 'logger': Mock(),
          'config': types.SimpleNamespace(trading=types.SimpleNamespace(margin_mode='cross')), 'symbol_to_ccxt': lambda s, t: 'BTC/USDT:USDT'}
    load_functions('alpha_live.py', ['_safe_num'], ns)
    exec(compile(ast.Module(body=[klass], type_ignores=[]), 'alpha_live.py', 'exec'), ns)
    a = ns['AlphaLiveExecutor'](); a.PREFIX = 'AX'; a.TAG = 'ALPHAX'
    ex = Mock(); ex.fetch_ticker.return_value = {'bid': 99, 'ask': 101, 'last': 100}; ex.price_to_precision.side_effect = lambda s, p: str(p)
    ex.create_order.return_value = {'id': 'order1'}
    ex.fetch_order.return_value = {'status': 'closed', 'filled': 4, 'average': 100}
    a._ensure = lambda: ex; a.market = lambda _: {'contractSize': .1, 'info': {'tickSz': '.01'}}; a._ensure_leverage = Mock()
    a.account = lambda: {'free': 1000, 'total': 1000}; a.pos_mode = lambda: 'net_mode'; a._contracts = lambda *args: 10
    a.order_by_client_id = Mock(return_value={}); a.close = Mock(return_value={'flat_confirmed': True}); a.cancel_algo = Mock()
    a.amend_protection = Mock(return_value={'verified': False, 'error': 'OKX 附加TP/SL尚未出现或ID不匹配: AXa,AXb'})
    return a, ex


class ProtectionFallbackTests(unittest.TestCase):
    """限价单成交后附带 TP/SL 没出现：单独补挂止盈+止损（不平仓、不开熔断）；补挂失败才平仓。"""
    def test_missing_attached_is_replaced_by_separate_orders(self):
        from unittest.mock import Mock
        a, ex = _executor_with_fallback()
        a.place_native_sl = Mock(); a.place_native_tp = Mock()
        r = a.open_maker('BTC-USDT-SWAP', 'long', 100, .012, .25, 3, client_order_id='AXtest', ttl=30, fallback_to_market=True)
        self.assertEqual(r['filled'], 4); a.close.assert_not_called()
        self.assertEqual(a.place_native_sl.call_args.args[2], 4); self.assertEqual(a.place_native_tp.call_args.args[2], 4)
        self.assertAlmostEqual(a.place_native_tp.call_args.args[3], 101.2); self.assertAlmostEqual(a.place_native_sl.call_args.args[3], 75.0)
        self.assertEqual(r['tp_attach_clordid'], a.place_native_tp.call_args.kwargs['client_id'])
        self.assertEqual(r['sl_attach_clordid'], a.place_native_sl.call_args.kwargs['client_id'])
        self.assertNotEqual(r['tp_attach_clordid'], r['sl_attach_clordid'])

    def test_fallback_failure_flattens_and_marks_terminal(self):
        from unittest.mock import Mock
        a, ex = _executor_with_fallback()
        a.place_native_sl = Mock(side_effect=RuntimeError('拒单')); a.place_native_tp = Mock()
        with self.assertRaises(RuntimeError) as c:
            a.open_maker('BTC-USDT-SWAP', 'long', 100, .012, .25, 3, client_order_id='AXtest', ttl=30, fallback_to_market=True)
        a.close.assert_called_once(); self.assertTrue(getattr(c.exception, 'flat_terminal', False))

    def test_other_failures_still_flatten(self):
        from unittest.mock import Mock
        a, ex = _executor_with_fallback()
        a.amend_protection = Mock(return_value={'verified': False, 'error': '改价失败'}); a.place_native_sl = Mock(); a.place_native_tp = Mock()
        with self.assertRaises(RuntimeError):
            a.open_maker('BTC-USDT-SWAP', 'long', 100, .012, .25, 3, client_order_id='AXtest', ttl=30, fallback_to_market=True)
        a.place_native_sl.assert_not_called(); a.close.assert_called_once()
