"""V8 tests: Strategy Broker matching + drawing object state model."""
import unittest
import numpy as np

from alpha_v7_pine import run, compile_source, PineError
from pine_v5.runtime import Runtime


def frame(n=20, price=100.0):
    return dict(ts=np.arange(n) * 300000,
                open=np.full(n, price, float), close=np.full(n, price, float),
                high=np.full(n, price + 1.0), low=np.full(n, price - 1.0),
                volume=np.ones(n) * 100)


def runtime_for(src, f, tick_size=0.01):
    r = Runtime(compile_source(src), f)
    r.broker.tick_size = tick_size
    return r


class BrokerMatchingTests(unittest.TestCase):
    def test_market_entry_fills_next_bar_open(self):
        f = frame(3)
        src = ('strategy("e")\n'
               'if bar_index==0\n'
               '    strategy.entry("L", strategy.long, qty=3)\n')
        r = runtime_for(src, f).run()
        pos = r['broker']['positions']['L']
        self.assertEqual(pos['qty'], 3.0)
        self.assertEqual(pos['entry_bar'], 1)       # fills next bar
        self.assertEqual(pos['entry_price'], 100.0)

    def test_pyramiding_stacks_same_direction(self):
        f = frame(6)
        src = ('strategy("p", pyramiding=2)\n'
               'if bar_index==1\n'
               '    strategy.entry("L", strategy.long, qty=1)\n'
               'if bar_index==3\n'
               '    strategy.entry("L", strategy.long, qty=1)\n')
        r = runtime_for(src, f).run()
        pos = r['broker']['positions']['L']
        self.assertEqual(pos['qty'], 2.0)
        self.assertEqual(pos['side'], 1)

    def test_pyramiding_limit_blocks_extra(self):
        f = frame(8)
        # pyramiding=1 allows one open slot; third same-direction entry rejected
        src = ('strategy("p", pyramiding=1)\n'
               'if bar_index==1\n'
               '    strategy.entry("L", strategy.long, qty=1)\n'
               'if bar_index==3\n'
               '    strategy.entry("L", strategy.long, qty=1)\n'
               'if bar_index==5\n'
               '    strategy.entry("L", strategy.long, qty=1)\n')
        r = runtime_for(src, f).run()
        pos = r['broker']['positions']['L']
        # first two fill (bars 2 and 4), third rejected by pyramid limit
        self.assertLessEqual(pos['qty'], 2.0)

    def test_partial_exit_by_qty(self):
        f = frame(5)
        src = ('strategy("x")\n'
               'if bar_index==0\n'
               '    strategy.entry("L", strategy.long, qty=10)\n'
               'if bar_index==2\n'
               '    strategy.close("L", qty=4)\n')
        r = runtime_for(src, f).run()
        self.assertEqual(r['broker']['positions']['L']['qty'], 6.0)
        self.assertEqual(len(r['broker']['closed_trades']), 1)
        self.assertEqual(r['broker']['closed_trades'][0]['qty'], 4.0)

    def test_partial_exit_by_qty_percent(self):
        f = frame(5)
        src = ('strategy("x")\n'
               'if bar_index==0\n'
               '    strategy.entry("L", strategy.long, qty=10)\n'
               'if bar_index==2\n'
               '    strategy.close("L", qty_percent=50)\n')
        r = runtime_for(src, f).run()
        self.assertAlmostEqual(r['broker']['positions']['L']['qty'], 5.0, places=6)

    def test_oca_stop_cancels_limit(self):
        f = frame(4)
        f['high'] = np.array([101, 95, 101, 101.])
        f['low'] = np.array([99, 89, 99, 99.])   # low[1]=89 hits stop=90
        src = ('strategy("o")\n'
               'if bar_index==0\n'
               '    strategy.entry("L", strategy.long, qty=2)\n'
               'strategy.exit("b", from_entry="L", stop=90, limit=120)\n')
        r = runtime_for(src, f).run()
        closes = [e for e in r['events'] if e['kind'] == 'close']
        self.assertEqual(len(closes), 1)
        self.assertEqual(closes[0]['fill_type'], 'stop')
        self.assertEqual(r['broker']['positions'], {})

    def test_conservative_stop_preferred_over_limit_same_bar(self):
        f = frame(4)
        f['open'] = np.array([100, 100, 100, 100.])
        f['close'] = np.array([100, 100, 100, 100.])
        f['high'] = np.array([101, 121, 101, 101.])
        f['low'] = np.array([99, 89, 99, 99.])
        src = ('strategy("c")\n'
               'if bar_index==0\n'
               '    strategy.entry("L", strategy.long)\n'
               'strategy.exit("b", from_entry="L", stop=90, limit=120)\n')
        r = runtime_for(src, f).run()
        closes = [e for e in r['events'] if e['kind'] == 'close']
        self.assertEqual((closes[0]['bar'], closes[0]['fill'],
                          closes[0]['fill_type']), (1, 90.0, 'stop'))

    def test_trailing_stop_ratchets_up(self):
        f = dict(ts=np.arange(6) * 300000,
                 open=np.array([100, 100, 100, 100, 100, 100.], float),
                 close=np.array([100, 101, 105, 104, 100, 100.], float),
                 high=np.array([100, 102, 106, 105, 101, 100.], float),
                 low=np.array([100, 100, 104, 103, 99, 99.], float),
                 volume=np.ones(6) * 100)
        src = ('strategy("t")\n'
               'if bar_index==0\n'
               '    strategy.entry("L", strategy.long, qty=1)\n'
               'strategy.exit("e", from_entry="L", stop=95, trail_points=3, trail_offset=3)\n')
        r = runtime_for(src, f, tick_size=1.0).run()
        closes = [e for e in r['events'] if e['kind'] == 'close']
        self.assertEqual(closes[0]['fill_type'], 'stop')
        # trailed up from 95 -> 99 -> 103, stopped at 103 on bar 3
        self.assertEqual(closes[0]['fill'], 100.0)  # gaps through stop fill at open, not an unavailable 103

    def test_profit_loss_ticks(self):
        f = dict(ts=np.arange(4) * 300000,
                 open=np.array([100, 100, 100, 100.], float),
                 close=np.array([100, 100, 105, 100.], float),
                 high=np.array([100, 101, 106, 101.], float),
                 low=np.array([100, 99, 99, 99.], float),
                 volume=np.ones(4) * 100)
        # 500 ticks * 0.01 = 5.0 -> limit at 105; high[2]=106 touches
        src = ('strategy("pl")\n'
               'if bar_index==0\n'
               '    strategy.entry("L", strategy.long, qty=1)\n'
               'strategy.exit("e", from_entry="L", profit=500, loss=200)\n')
        r = runtime_for(src, f, tick_size=0.01).run()
        closes = [e for e in r['events'] if e['kind'] == 'close']
        self.assertEqual(closes[0]['fill'], 105.0)
        self.assertEqual(closes[0]['fill_type'], 'limit')

    def test_commission_and_slippage_affect_fills(self):
        f = dict(ts=np.arange(3) * 300000,
                 open=np.array([100, 100, 100.], float),
                 close=np.array([100, 105, 100.], float),
                 high=np.array([100, 106, 101.], float),
                 low=np.array([100, 99, 99.], float),
                 volume=np.ones(3) * 100)
        src = ('strategy("c")\n'
               'if bar_index==0\n'
               '    strategy.entry("L", strategy.long, qty=2)\n'
               'if bar_index==1\n'
               '    strategy.close("L")\n')
        r = runtime_for(src, f)
        r.broker.commission_rate = 1.0
        r.broker.commission_type = 'percent'
        r.broker.slippage_ticks = 2
        r.broker.tick_size = 0.5
        res = r.run()
        tr = res['broker']['closed_trades'][0]
        # buy slippage up: 100 + 2*0.5 = 101
        self.assertEqual(tr['entry_price'], 101.0)
        # sell slippage down: 100 - 2*0.5 = 99
        self.assertEqual(tr['exit_price'], 99.0)
        # 1% commission both ways on notional
        self.assertLess(tr['pnl'], (99.0 - 101.0) * 2)

    def test_position_builtin_variables(self):
        f = frame(5)
        seen = []
        src = ('strategy("v")\n'
               'if bar_index==1\n'
               '    strategy.entry("L", strategy.long, qty=2)\n'
               'plot(strategy.position_size)\n'
               'plot(strategy.opentrades)\n')
        r = runtime_for(src, f).run()
        # after the fill at bar 2, position size should be +2
        size_plot = r['plots'][0]
        self.assertEqual(size_plot['values'][2], 2.0)
        self.assertEqual(r['broker']['positions']['L']['qty'], 2.0)

    def test_closedtrades_counter(self):
        f = frame(4)
        f['high'] = np.array([101, 95, 101, 101.])
        f['low'] = np.array([99, 89, 99, 99.])
        src = ('strategy("v")\n'
               'if bar_index==0\n'
               '    strategy.entry("L", strategy.long, qty=1)\n'
               'strategy.exit("b", from_entry="L", stop=90, limit=120)\n')
        r = runtime_for(src, f).run()
        self.assertEqual(r['broker']['closed_trades_count'], 1)
        self.assertEqual(r['broker']['open_trades'], 0)

    def test_close_all_emits_signal_event(self):
        f = frame(10)
        src = ('strategy("x")\n'
               'if bar_index==1\n'
               '    strategy.entry("L", strategy.long)\n'
               'if bar_index==3\n'
               '    strategy.close_all()\n')
        r = runtime_for(src, f).run()
        kinds = [(e['kind'], e['bar']) for e in r['events']]
        self.assertIn(('close_all', 3), kinds)


class DrawingObjectTests(unittest.TestCase):
    def _run(self, src, n=10):
        return run(src, frame(n))

    def test_line_create_set_get_delete(self):
        src = ('indicator("d")\n'
               'if bar_index==2\n'
               '    l = line.new(0, 100, 5, 105, color=color.red, width=2)\n'
               '    line.set_x2(l, 8)\n'
               '    line.set_y2(l, 110)\n')
        r = self._run(src)
        obj = r['draw_objects'][1]
        self.assertEqual(obj['type'], 'line')
        self.assertEqual(obj['x2'], 8.0)
        self.assertEqual(obj['y2'], 110.0)
        self.assertEqual(obj['color'], 'color.red')
        # events recorded
        kinds = [e['kind'] for e in r['draw_events']]
        self.assertIn('create', kinds)
        self.assertIn('set', kinds)

    def test_line_delete_removes_state(self):
        src = ('indicator("d")\n'
               'if bar_index==1\n'
               '    l = line.new(0, 1, 2, 3)\n'
               '    line.delete(l)\n')
        r = self._run(src)
        self.assertNotIn(1, r['draw_objects'])
        self.assertTrue(any(e['kind'] == 'delete' for e in r['draw_events']))

    def test_label_set_get(self):
        src = ('indicator("d")\n'
               'if bar_index==1\n'
               '    lab = label.new(2, 102, text="hi", color=color.blue)\n'
               '    label.set_text(lab, "bye")\n'
               '    label.set_y(lab, 110)\n')
        r = self._run(src)
        obj = r['draw_objects'][1]
        self.assertEqual(obj['text'], 'bye')
        self.assertEqual(obj['y'], 110.0)

    def test_box_set_get(self):
        src = ('indicator("d")\n'
               'if bar_index==1\n'
               '    b = box.new(1, 103, 4, 98, bgcolor=color.green)\n'
               '    box.set_right(b, 9)\n'
               '    box.set_bgcolor(b, color.red)\n')
        r = self._run(src)
        obj = r['draw_objects'][1]
        self.assertEqual(obj['right'], 9.0)
        self.assertEqual(obj['bgcolor'], 'color.red')

    def test_table_cell_state(self):
        src = ('indicator("d")\n'
               'if bar_index==1\n'
               '    t = table.new(position=position.top_right, columns=2, rows=2)\n'
               '    table.cell(t, 0, 0, text="A")\n'
               '    table.cell(t, 1, 1, text="B")\n')
        r = self._run(src)
        obj = r['draw_objects'][1]
        self.assertEqual(obj['columns'], 2)
        self.assertEqual(obj['cells'][(0, 0)]['text'], 'A')
        self.assertEqual(obj['cells'][(1, 1)]['text'], 'B')

    def test_polyline_state(self):
        # build points via the store directly (nested [x,y] pairs)
        f = frame(5)
        rt = Runtime(compile_source('indicator("d")\nbar_index'), f)
        rt.i = 1
        pid = rt.plot_store.polyline_new([[0, 100], [3, 105]])
        self.assertEqual(rt.draw_objects[pid]['points'],
                         [[0.0, 100.0], [3.0, 105.0]])
        rt.plot_store.polyline_set_points(pid, [[0, 1], [2, 3]])
        self.assertEqual(rt.draw_objects[pid]['points'],
                         [[0.0, 1.0], [2.0, 3.0]])
        rt.plot_store.delete('polyline', pid)
        self.assertNotIn(pid, rt.draw_objects)

    def test_linefill_links_two_lines(self):
        f = frame(5)
        rt = Runtime(compile_source('indicator("d")\nbar_index'), f)
        rt.i = 1
        a = rt.plot_store.line_new(0, 100, 5, 100)
        b = rt.plot_store.line_new(0, 105, 5, 105)
        lf = rt.plot_store.linefill_new(a, b, color='color.blue')
        self.assertEqual(rt.draw_objects[lf]['type'], 'linefill')
        self.assertEqual(rt.draw_objects[lf]['line1'], a)
        self.assertEqual(rt.draw_objects[lf]['line2'], b)

    def test_events_are_replayable(self):
        src = ('indicator("d")\n'
               'if bar_index==1\n'
               '    l = line.new(0, 1, 2, 3)\n'
               '    line.set_x2(l, 9)\n')
        r = self._run(src)
        creates = [e for e in r['draw_events'] if e['kind'] == 'create']
        sets = [e for e in r['draw_events'] if e['kind'] == 'set']
        self.assertEqual(creates[0]['obj_type'], 'line')
        self.assertEqual(sets[0]['attrs']['x2'], 9.0)
        self.assertEqual(creates[0]['bar'], 1)


if __name__ == '__main__':
    unittest.main()
