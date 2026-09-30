"""V7.7.0 方案二：参数、下单信号组装、持仓动作（加仓先挂整仓保护、一卖减半、卖点清仓、同根不重复）、
本地模拟、选币按成交额、状态缓存、大盘宽度。不连网。"""
import time
import unittest
from unittest.mock import patch
import numpy as np
import alpha_fast_v7 as v7
import alpha_engine as ae
import alpha_v7_scheme2 as s2


def frame(n=400, ms=300000, start=1_700_000_000_000, base=100.0):
    ts = np.arange(n, dtype=np.int64) * ms + start // ms * ms
    c = base + np.sin(np.arange(n) / 7.0)
    return dict(ts=ts, open=c, high=c + .5, low=c - .5, close=c, volume=np.ones(n))


def s2_data(entry=None, T=None, **extra):
    f = frame()
    T = T or int(f['ts'][-1]) + 300000
    return dict(frames={'5m': f}, ticker_last=float(f['close'][-1]), spread_bps=2.0, as_of_ms=T + 1000,
                scheme2=dict(T=T, entry=entry, action=None, notes=extra.pop('notes', []), **extra))


class ParamTests(unittest.TestCase):
    def test_scheme2_forces_settings_and_excludes_scheme1(self):
        p = v7.validate_params({'chan_scheme2': 1, 'strategy': 'ema_cross', 'base_tf': '1h'})
        self.assertEqual((p['strategy'], p['base_tf'], p['chan_mtf'], p['chan_exit_opposite']), ('chan_quant', '5m', 0, 0))
        with self.assertRaises(ValueError):
            v7.validate_params({'chan_scheme1': 1, 'chan_scheme2': 1})


class DecideTests(unittest.TestCase):
    def test_entry_builds_order_with_one_third_risk_and_far_tp(self):
        d = s2_data()
        px = d['ticker_last']; stop = px * 0.98
        d['scheme2']['entry'] = dict(side=1, stage='2', stop=stop, ref=px, breadth=0.6, small2big=True)
        out = v7.decide('ETH-USDT-SWAP', d, {'chan_scheme2': 1})
        fs = out['fast_strategy']
        self.assertEqual(out['signal'], 'LONG')
        self.assertAlmostEqual(fs['sl_price'], stop)
        self.assertAlmostEqual(fs['v7_risk_scale'], 1 / 3)
        self.assertAlmostEqual(out['tp'], min(20 * (px - stop) / px, 0.45))
        self.assertGreater(fs['max_seconds'], 300 * 86400)
        self.assertEqual(fs['bar_ts'], d['scheme2']['T'] - 300000)
        self.assertIn('|s2|', fs['signal_id'])
        meta = v7.position_meta(out)
        self.assertTrue(meta['v7_scheme2']); self.assertEqual(meta['s2_stages'], '2'); self.assertIsNone(meta['s2_sold1'])
        self.assertIn('小转大', out['reason'])

    def test_waits(self):
        self.assertIn('没有可开仓', v7.decide('X-USDT-SWAP', s2_data(notes=['大盘宽度 40% < 50%']), {'chan_scheme2': 1})['reason'])
        self.assertIn('读取失败', v7.decide('X-USDT-SWAP', s2_data(error='30分钟/日线K线读取失败'), {'chan_scheme2': 1})['reason'])
        d = s2_data(); px = d['ticker_last']
        d['scheme2']['entry'] = dict(side=1, stage='2', stop=px * 0.99, ref=px * 1.02)      # 已离开触发价太远
        self.assertIn('追价', v7.decide('X-USDT-SWAP', d, {'chan_scheme2': 1})['reason'])


class FakeLive:
    def __init__(self, contracts=10.0):
        self.calls = []; self.contracts = contracts
    def place_position_protection(self, symbol, side, sl, tp):
        self.calls.append(('protect', sl, tp)); return {'client_id': 'AXPOS1', 'sl': sl, 'tp': tp}
    def cancel_algo(self, symbol, algo_cl_ord_id=None):
        self.calls.append(('cancel', algo_cl_ord_id)); return {'ok': True}
    def amend_sl_only(self, symbol, side, sl, sl_id=None, wait_timeout=0):
        self.calls.append(('amend', sl, sl_id)); return {'verified': True}
    def add_to_position(self, symbol, side, notional, leverage):
        self.calls.append(('add', round(notional, 6))); return {'filled': 5.0, 'average': 110.0, 'notional_usdt': 550.0, 'order_id': 'o2', 'status': 'closed'}
    def positions(self): return [{'symbol': 'ETH/USDT:USDT', 'side': 'long', 'contracts': self.contracts}]
    def pos_mode(self): return 'long_short_mode'
    def reduce_only_close_qty(self, symbol, side, qty):
        self.calls.append(('reduce', qty)); return {'filled': qty, 'average': 120.0, 'fee': 0.1}


def position(**kw):
    p = dict(side='long', entry=100.0, filled=10.0, notional=1000.0, sl=95.0, tp=200.0, live=True, fast_version='v7',
             v7_scheme2=True, s2_stages='2', s2_sold1=None, s2_stop=95.0, tp_attach_clordid='AXTP', sl_attach_clordid='AXSL')
    p.update(kw); return p


class LiveManageTests(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(risk_pct=0.01, leverage=3, max_notional_pct=1.0)
        self.patches = [patch.object(ae, '_persist', lambda: None), patch.object(ae, 'ledger_record', lambda *a, **k: None),
                        patch.object(ae, '_activity', lambda *a, **k: None), patch.object(ae, '_risk_block', lambda cfg: ''),
                        patch.object(ae, 'ops_breaker_status', lambda: {'open': False}),
                        patch.object(ae, '_live_account_snapshot', lambda max_age=2.0: (10000.0, 8000.0)),
                        patch.object(ae.okx_client, 'get_ticker', lambda s: {'last': 110.0}),
                        patch.object(ae.config.trading, 'get_ccxt_symbol', lambda s: 'ETH/USDT:USDT')]
        for x in self.patches: x.start()

    def tearDown(self):
        for x in self.patches: x.stop()

    def pred(self, action, T=1000):
        return {'scheme2': {'T': T, 'action': action}}

    def test_add_protects_whole_position_before_adding(self):
        live = FakeLive(); p = position()
        with patch.object(ae, 'alpha_live', live), patch.dict(ae.STATE, {'live_trades': [], 'attribution': []}):
            r = ae._live_manage_scheme2('ETH-USDT-SWAP', p, self.pred(dict(type='add', stage='s', stop=95.0, ref=110.0, reason='类二买')), self.cfg)
        self.assertIsNone(r)
        kinds = [c[0] for c in live.calls]
        self.assertEqual(kinds[:3], ['protect', 'cancel', 'cancel'])        # 整仓保护生效后才撤首批保护
        self.assertEqual(kinds[-1], 'add')
        self.assertAlmostEqual(live.calls[-1][1], 10000 * 0.01 / 3 / ((110 - 95) / 110), places=4)
        self.assertEqual(p['s2_stages'], '2s'); self.assertEqual(p['filled'], 15.0)
        self.assertAlmostEqual(p['entry'], (100 * 10 + 110 * 5) / 15)
        self.assertEqual(p['s2_pos_algo'], 'AXPOS1'); self.assertEqual(p['s2_done_T'], 1000)
        # 同一根K线不重复执行
        with patch.object(ae, 'alpha_live', live):
            ae._live_manage_scheme2('ETH-USDT-SWAP', p, self.pred(dict(type='add', stage='s', stop=95.0, ref=110.0)), self.cfg)
        self.assertEqual(sum(1 for c in live.calls if c[0] == 'add'), 1)

    def test_add_skipped_when_breaker_or_price_beyond_stop(self):
        live = FakeLive(); p = position()
        with patch.object(ae, 'alpha_live', live), patch.object(ae, 'ops_breaker_status', lambda: {'open': True}):
            ae._live_manage_scheme2('ETH-USDT-SWAP', p, self.pred(dict(type='add', stage='s', stop=95.0, ref=110.0)), self.cfg)
        p2 = position()
        with patch.object(ae, 'alpha_live', live):
            ae._live_manage_scheme2('ETH-USDT-SWAP', p2, self.pred(dict(type='add', stage='s', stop=112.0, ref=110.0)), self.cfg)
        self.assertEqual(live.calls, [])
        self.assertEqual((p['s2_stages'], p2['s2_stages']), ('2', '2'))

    def test_reduce_half_then_close_reason(self):
        live = FakeLive(contracts=10.0); p = position(s2_pos_algo='AXPOS1', sl=95.0)
        with patch.object(ae, 'alpha_live', live), patch.dict(ae.STATE, {'live_trades': [], 'attribution': []}), \
                patch.object(ae, 'trade_attribution', lambda *a, **k: {'net_pnl': 1.0}):
            ae._live_manage_scheme2('ETH-USDT-SWAP', p, self.pred(dict(type='reduce_half', sold1=121.0, reason='1卖')), self.cfg)
            self.assertEqual(ae.STATE['live_trades'][-1]['action'], 'PARTIAL_CLOSE')
        self.assertIn(('reduce', 5.0), live.calls)
        self.assertEqual(p['s2_sold1'], 121.0); self.assertEqual(p['filled'], 5.0)
        with patch.object(ae, 'alpha_live', live):
            reason = ae._live_manage_scheme2('ETH-USDT-SWAP', p, self.pred(dict(type='close', reason='方案二：3卖，清仓'), T=2000), self.cfg)
        self.assertEqual(reason, '方案二：3卖，清仓')

    def test_mark_sold1_and_no_trail_arming(self):
        p = position()
        ae._live_manage_scheme2('ETH-USDT-SWAP', p, self.pred(dict(type='mark_sold1', price=130.0, reason='小转大')), self.cfg)
        self.assertEqual(p['s2_sold1'], 130.0)
        with patch.dict(ae.STATE, {'positions': {'ETH-USDT-SWAP': position()}}), \
                patch.object(ae, '_arm_native_trail', side_effect=AssertionError('不应挂移动止损')):
            ae._arm_native_exits_after_open('ETH-USDT-SWAP', self.cfg)


class PaperTests(unittest.TestCase):
    def test_paper_add_reduce_close(self):
        cfg = dict(risk_pct=0.01, fee_pct=0.0005, slippage_pct=0.0005)
        p = dict(side='long', entry=100.0, notional=1000.0, sl=95.0, v7_scheme2=True, s2_stages='2', s2_sold1=None)
        st = {'balance': 10000.0, 'paper_trades': [], 'positions': {'X': p}, 'consecutive_losses': 0}
        with patch.dict(ae.STATE, st), patch.object(ae, '_persist', lambda: None), patch.object(ae, '_activity', lambda *a, **k: None):
            ae._paper_manage_scheme2('X', p, {'scheme2': {'T': 1, 'action': dict(type='add', stage='s', stop=95.0)}}, cfg, 110.0, 0)
            self.assertEqual(p['s2_stages'], '2s'); self.assertGreater(p['notional'], 1000.0)
            n = p['notional']
            ae._paper_manage_scheme2('X', p, {'scheme2': {'T': 2, 'action': dict(type='reduce_half', sold1=120.0)}}, cfg, 120.0, 0)
            self.assertAlmostEqual(p['notional'], n / 2); self.assertEqual(p['s2_sold1'], 120.0)
            ae._paper_manage_scheme2('X', p, {'scheme2': {'T': 3, 'action': dict(type='close', reason='3卖')}}, cfg, 125.0, 0)
            self.assertNotIn('X', ae.STATE['positions'])
            self.assertEqual(ae.STATE['paper_trades'][-1]['reason'], '3卖')

    def test_paper_stop_at_first_buy_low(self):
        cfg = dict(risk_pct=0.01, fee_pct=0.0005, slippage_pct=0.0005)
        p = dict(side='long', entry=100.0, notional=1000.0, sl=95.0, v7_scheme2=True, s2_stages='2', s2_sold1=None)
        with patch.dict(ae.STATE, {'balance': 10000.0, 'paper_trades': [], 'positions': {'X': p}, 'consecutive_losses': 0}), \
                patch.object(ae, '_persist', lambda: None), patch.object(ae, '_activity', lambda *a, **k: None):
            ae._paper_manage_scheme2('X', p, {}, cfg, 94.0, 0)
            self.assertIn('止损', ae.STATE['paper_trades'][-1]['reason'])


class EvaluateTests(unittest.TestCase):
    def test_first_call_replays_without_orders_and_same_bar_is_cached(self):
        s2.reset_state()
        fr = {'5m': frame(600), '30m': frame(300, 1800000), '1d': frame(120, 86400000)}
        r1 = s2.evaluate('TEST-USDT-SWAP', fr, None, 0.6)
        self.assertIsNone(r1['entry']); self.assertIsNone(r1['action'])
        self.assertTrue(any('首次处理' in n for n in r1['notes']))
        with patch.object(s2, 'Level', side_effect=AssertionError('同一根K线不应重算')):
            r2 = s2.evaluate('TEST-USDT-SWAP', fr, None, 0.6)
        self.assertEqual(r2['T'], r1['T'])
        self.assertEqual(s2._load()['TEST-USDT-SWAP']['last_T'], r1['T'])

    def test_market_breadth_cached_per_day(self):
        calls = []
        def fake_frame(ex, cs, tf, count=0, now_ms=None):
            calls.append(cs); c = np.linspace(1, 2, 80) if cs.startswith('UP') else np.linspace(2, 1, 80)
            return dict(ts=np.arange(80), close=c)
        s2._BREADTH.update(day=None, value=None, coins=0)
        with patch('alpha_v7_feed.frame', fake_frame):
            v, n = s2.market_breadth(None, ['UP%d' % i for i in range(7)] + ['DN%d' % i for i in range(3)], 86400000 * 5)
            self.assertEqual((round(v, 2), n), (0.7, 10))
            s2.market_breadth(None, ['UP0'], 86400000 * 5 + 1000)
        self.assertEqual(len(calls), 10)


class UniverseTests(unittest.TestCase):
    def test_volume_ranking_when_scheme2_on(self):
        import tv_universe as tu
        rows = [dict(symbol='A-USDT-SWAP', pct=5.0, quote_volume=2e7, last=1), dict(symbol='B-USDT-SWAP', pct=-3.0, quote_volume=9e8, last=1),
                dict(symbol='C-USDT-SWAP', pct=1.0, quote_volume=5e8, last=1)]
        with patch.dict(tu.S, {'cfg': dict(tu.DEFAULT_CFG)}), patch.object(tu.okx_client, 'fetch_swap_tickers', lambda: rows), \
                patch.object(tu, '_scheme2_on', lambda: True):
            tu._refresh()
            self.assertEqual(tu.S['symbols'][:3], ['B-USDT-SWAP', 'C-USDT-SWAP', 'A-USDT-SWAP'])
        with patch.dict(tu.S, {'cfg': dict(tu.DEFAULT_CFG)}), patch.object(tu.okx_client, 'fetch_swap_tickers', lambda: rows), \
                patch.object(tu, '_scheme2_on', lambda: False):
            tu._refresh()
            self.assertEqual(tu.S['symbols'][:2], ['A-USDT-SWAP', 'C-USDT-SWAP'])


if __name__ == '__main__':
    unittest.main()
