import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import test_v72_api

from alpha_v7_chan import centers, select_signal, signal_status, trade_signals
from alpha_v7_orderflow import FlowState, PublicStream, _STATES, snapshot
from alpha_v7_pine import PineError, compile_source, load_profile, run, run_saved, save
from test_v73_chan import units
from test_v73_pine import frame


class ChanCompletionTests(unittest.TestCase):
    def test_candles_to_confirmed_signal_to_protected_trade(self):
        import alpha_fast_v7 as strategy
        from alpha_v7_analysis import chan
        prices = [100, 110, 104, 112, 105, 115, 111, 117, 112, 119, 113, 118, 110, 116, 108]
        c = np.r_[np.full(300, 100.), np.interp(np.arange(113), np.arange(len(prices))*8, prices)]
        f = dict(ts=np.arange(len(c))*300000, open=c, close=c, high=c+.1, low=c-.1, volume=np.ones(len(c))*100)
        full = chan(f, signal_level=0)
        events = [e for e in full['signals'] if e['label']=='3买']
        self.assertTrue(events)
        event = events[0];end = int(event['known_at']//300000)+1
        current = {k:v[:end] for k,v in f.items()}
        with patch.object(strategy, '_RUNTIME', {}):
            pred = strategy.decide('SYNTHETIC', dict(frames={'5m':current}, ticker_last=float(c[end-1]),
                spread_bps=0, missing=[], as_of_ms=end*300000), dict(strategy='chan_quant', chan_level=0,
                chan_mtf=0, dynamic_tp=0, max_stop=.5, min_risk_cost=.1, min_target_cost=.1, min_net_rr=.1))
        self.assertEqual(pred['signal'], 'LONG', pred['reason'])
        self.assertLess(pred['fast_strategy']['v7_strategy_stop'], event['invalidation'])
        self.assertEqual(strategy.position_meta(pred)['v7_chan_signal']['id'], event['id'])

    def test_touching_center_is_not_third_point(self):
        for mirror in (False, True):
            prices = [0, 10, 4, 12, 5, 15, 10, 17, 12, 19]
            us = units([30-x for x in prices] if mirror else prices)
            zs, _, _ = centers(us, 0)
            signals, _ = trade_signals(us, zs, np.ones(20), np.arange(20)*300000)
            self.assertFalse(any(s['unit_index'] == 5 and s['label'].startswith('3') for s in signals))

    def event(self, label='2买', **kwargs):
        return dict(id=label, label=label, side=1 if label.endswith('买') else -1,
                    ts=100, known_at=300, invalidation=10, **kwargs)

    def test_invalidation_before_confirmation_stays_invalid_after_reclaim(self):
        event = self.event()
        status = signal_status([event], [100, 200, 300, 400], [10, 9, 11, 12])
        self.assertTrue(status[event['id']]['invalid_at_confirmation'])
        self.assertEqual(status[event['id']]['invalidated_at'], 200)
        self.assertIsNone(select_signal(dict(signals=[event], signal_status=status), 300, {'chan_buy2': 1}))

    def test_short_invalidation_and_future_status_does_not_rewrite_entry(self):
        event = self.event('2卖')
        status = signal_status([event], [100, 200, 300, 400], [10, 9, 8, 11])
        self.assertFalse(status[event['id']]['invalid_at_confirmation'])
        self.assertEqual(select_signal(dict(signals=[event], signal_status=status), 300, {'chan_sell2': 1}), event)

    def test_disabled_last_signal_does_not_hide_enabled_event(self):
        enabled, disabled = self.event('2买'), self.event('1买')
        result = dict(signals=[enabled, disabled])
        self.assertEqual(select_signal(result, 300, {'chan_buy2': 1}), enabled)

    def test_conflicting_simultaneous_directions_wait(self):
        result = dict(signals=[self.event('2买'), self.event('3卖')])
        self.assertIsNone(select_signal(result, 300, {'chan_buy2': 1, 'chan_sell3': 1}))

    def test_highest_enabled_point_priority_and_no_old_signal(self):
        events = [self.event('3买'), self.event('2买')]
        self.assertEqual(select_signal(dict(signals=events), 300, {'chan_buy2': 1, 'chan_buy3': 1}), events[0])
        self.assertIsNone(select_signal(dict(signals=events), 400, {'chan_buy2': 1, 'chan_buy3': 1}))


class PineCompletionTests(unittest.TestCase):
    def values(self, body, n=10):
        return run('indicator("test")\n'+body, frame(n))['plots'][0]['values']

    def test_else_if_and_scoped_shadowing(self):
        body = ('x = 7\ny = 0\nif bar_index < 2\n    x = 90\n    y := x\n'
                'else if bar_index < 4\n    y := 2\nelse if bar_index < 6\n    y := 3\n'
                'else\n    y := x\nplot(y)')
        self.assertEqual(self.values(body), [90, 90, 2, 2, 3, 3, 7, 7, 7, 7])

    def test_local_var_initializes_only_once(self):
        body = 'y=0\nif bar_index>1\n    var x=0\n    x:=x+1\n    y:=x\nplot(y)'
        self.assertEqual(self.values(body, 6), [0, 0, 1, 2, 3, 4])

    def test_scope_and_assignment_errors(self):
        invalid = ['if true\n    x=1\nplot(x)', 'x=x+1', 'x=1\nx=2',
                   'if true\n    plot(close)', 'x:=1',
                   'if true\n    x=1\nelse\n    x=2\nelse\n    x=3', 'if true\nplot(close)',
                   'if true\n    x=1\nelse']
        for body in invalid:
            with self.subTest(body=body), self.assertRaises(PineError):
                compile_source('indicator("x")\n'+body)
        # A local variable declared purely inside a block is valid; only
        # referencing it outside the block is an error (first case above).
        compile_source('indicator("x")\nif true\n  x=1')

    def test_keyword_ta_arguments(self):
        values = self.values('plot(ta.sma(length=3,source=close))')
        self.assertAlmostEqual(values[-1], np.mean(frame(10)['close'][-3:]))

    def test_arity_and_duplicate_parameters_fail_closed(self):
        invalid = ['plot(ta.sma(close,3,99))', 'plot(ta.ema(close))',
                   'plot(ta.sma(close,source=close,length=3))',
                   'plot(close,title="a",title="b")', 'plot(series=close,"name")',
                   'strategy.entry("L",strategy.long,100)', 'plot(close,"x",color.red)']
        for body in invalid:
            with self.subTest(body=body), self.assertRaises(PineError):
                compile_source('strategy("x")\n'+body)
        # V8 broker supports partial close via qty.
        compile_source('strategy("x")\nstrategy.close("L",qty=1)')

    def test_barssince_and_valuewhen(self):
        self.assertEqual(self.values('plot(ta.barssince(bar_index==2))', 6), [None, None, 0, 1, 2, 3])
        self.assertEqual(self.values('plot(ta.valuewhen(bar_index%3==0,bar_index,1))', 8), [None, None, None, 0, 0, 0, 3, 3])

    def test_new_functions_prefix_invariance(self):
        source = ('indicator("new")\nplot(ta.valuewhen(close>104,close,1))\n'
                  'plot(ta.stoch(close,high,low,5))\nplot(ta.linreg(close,8,0))\n'
                  'plot(ta.barssince(close>104))')
        full = run(source, frame(100))
        for n in (10, 25, 60):
            short = run(source, frame(n))
            for a, b in zip(short['plots'], full['plots']):
                self.assertEqual(a['values'], b['values'][:n])

    def test_linreg_linear_input(self):
        f = frame(10);f['close'] = np.arange(10)*2+10.
        values = run('indicator("r")\nplot(ta.linreg(close,5,1))', f)['plots'][0]['values']
        self.assertAlmostEqual(values[-1], 26)

    def test_cumulative_average_and_math_functions(self):
        f=frame(8);f['close']=np.arange(1,9,dtype=float)
        source=('indicator("math")\nplot(ta.cum(close))\nplot(ta.avg(close,2))\n'
                'plot(math.sign(close-4))\nplot(math.log10(close))\nplot(math.tanh(0))')
        plots=run(source,f)['plots']
        self.assertEqual(plots[0]['values'],[1,3,6,10,15,21,28,36])
        self.assertEqual(plots[1]['values'][-1],5.)
        self.assertEqual(plots[2]['values'],[-1,-1,-1,0,1,1,1,1])
        self.assertAlmostEqual(plots[3]['values'][-1],np.log10(8))
        self.assertEqual(plots[4]['values'],[0.]*8)

    def test_vwma_variance_and_absolute_deviation(self):
        f=frame(8);f['close']=np.arange(1,9,dtype=float);f['volume']=np.arange(1,9,dtype=float)
        result=run('indicator("stats")\nplot(ta.vwma(close,3))\nplot(ta.variance(close,3))\nplot(ta.dev(close,3))',f)
        self.assertAlmostEqual(result['plots'][0]['values'][-1],(6*6+7*7+8*8)/21)
        self.assertAlmostEqual(result['plots'][1]['values'][-1],np.var([6,7,8]))
        self.assertAlmostEqual(result['plots'][2]['values'][-1],2/3)

    def test_rising_falling_are_strict_and_prefix_causal(self):
        f=frame(12);f['close']=np.array([1,2,3,3,2,1,2,3,4,3,2,1.],float)
        source='indicator("slope")\nplot(ta.rising(close,2))\nplot(ta.falling(close,2))'
        full=run(source,f)
        expected_rise=[0.,0.,1.,0.,0.,0.,0.,1.,1.,0.,0.,0.]
        self.assertEqual(full['plots'][0]['values'],expected_rise)
        for n in (5,8,11):
            short=run(source,{k:v[:n] for k,v in f.items()})
            self.assertEqual(short['plots'][0]['values'],expected_rise[:n])
            self.assertEqual(short['plots'][1]['values'],full['plots'][1]['values'][:n])

    def test_pivothigh_appears_only_when_right_side_confirms(self):
        f=frame(9);f['high']=np.array([1,2,3,5,9,6,4,3,2.],float);f['low']=np.array([4,3,4,5,6,1,4,5,6.],float)
        result=run('indicator("pivot")\nplot(ta.pivothigh(2,2))\nplot(ta.pivotlow(source=low,leftbars=1,rightbars=1))',f)
        self.assertIsNone(result['plots'][0]['values'][5])
        self.assertEqual(result['plots'][0]['values'][6],9.)
        self.assertEqual(result['plots'][1]['values'][6],1.)

    def test_dmi_and_supertrend_tuple_outputs_are_finite(self):
        f=frame(80)
        source=('indicator("trend")\n[p,m,a]=ta.dmi(14,14)\n[st,d]=ta.supertrend(3,10)\n'
                'plot(p)\nplot(m)\nplot(a)\nplot(st)\nplot(d)')
        result=run(source,f)
        self.assertTrue(all(np.isfinite(plot['values'][-1]) for plot in result['plots']))
        self.assertIn(result['plots'][4]['values'][-1],(-1.,1.))
        for n in (30,50,70):
            short=run(source,{k:v[:n] for k,v in f.items()})
            for a,b in zip(short['plots'],result['plots']):self.assertEqual(a['values'],b['values'][:n])

    def test_negative_plot_offset_does_not_backdate_strategy_events(self):
        f=frame(12);f['high']=np.array([1,2,3,5,9,6,4,3,2,3,4,5.],float)
        source=('strategy("offset")\nif bar_index==5\n    strategy.entry("L",strategy.long)\n'
                'if bar_index==7\n    strategy.close_all()\nplot(ta.pivothigh(2,2),offset=-2)')
        result=run(source,f)
        self.assertEqual([(e['kind'],e['bar'],e['known_at']) for e in result['events']],
                         [('entry',5,int(f['ts'][5])),('close_all',7,int(f['ts'][7]))])
        self.assertEqual(result['plots'][0]['values'][4],9.)
        self.assertIsNone(result['plots'][0]['values'][6])

    def test_strategy_exit_absolute_stop_limit_and_conservative_same_bar_fill(self):
        f=frame(4);f['open']=np.array([100,100,100,100.]);f['close']=np.array([100,100,100,100.])
        f['high']=np.array([101,121,101,101.]);f['low']=np.array([99,89,99,99.])
        source=('strategy("exit")\nif bar_index==0\n    strategy.entry("L",strategy.long)\n'
                'strategy.exit("bracket",from_entry="L",stop=90,limit=120)')
        events=run(source,f)['events']
        closes=[e for e in events if e['kind']=='close']
        self.assertEqual(len(closes),1)
        self.assertEqual((closes[0]['bar'],closes[0]['fill'],closes[0]['fill_type']), (1,90.,'stop'))
        # V8 broker supports partial exit quantity together with a stop.
        compile_source('strategy("x")\nstrategy.exit("x",from_entry="L",qty=1,stop=90)')

    def test_user_functions_keep_series_history_and_independent_call_sites(self):
        f=frame(20);f['close']=np.arange(20,dtype=float);f['high']=np.arange(20,dtype=float)*2
        source=('//@version=5\nindicator("functions")\nma(series float src, simple int n) => ta.ema(src,n)\n'
                'plot(ma(close,3))\nplot(ma(high,5))')
        result=run(source,f)
        expected_close=run('indicator("expected")\nplot(ta.ema(close,3))',f)['plots'][0]['values']
        expected_high=run('indicator("expected")\nplot(ta.ema(high,5))',f)['plots'][0]['values']
        self.assertEqual(result['plots'][0]['values'],expected_close)
        self.assertEqual(result['plots'][1]['values'],expected_high)
        self.assertEqual(compile_source(source)['source'],source)
        for n in (6,11,17):
            partial=run(source,{k:v[:n] for k,v in f.items()})
            for left,right in zip(partial['plots'],result['plots']):self.assertEqual(left['values'],right['values'][:n])

    def test_function_single_indented_expression_and_invalid_global_reference(self):
        source='indicator("function")\ndouble(x) =>\n    x * 2\nplot(double(close))'
        self.assertEqual(self.values('f(x) => x*2\nplot(f(close))',4),[x*2 for x in frame(4)['close']])
        self.assertEqual(run(source,frame(3))['plots'][0]['values'],[x*2 for x in frame(3)['close']])
        with self.assertRaises(PineError):compile_source('indicator("x")\nf(x)=>missing+ x\nplot(f(close))')

    def test_packaged_mtf_user_function_strategy_example_runs(self):
        source=Path(__file__).parents[1].joinpath('examples/V76_PINE_MTF_FUNCTION.pine').read_text()
        f=frame(300);result=run(source,f,timeframe='5m',symbol='BTC-USDT-SWAP')
        self.assertEqual((result['kind'],len(result['plots'])),('strategy',2))
        self.assertTrue(all(e['kind'] in ('entry','close') for e in result['events']))
        for n in (120,240):
            partial=run(source,{k:v[:n] for k,v in f.items()},timeframe='5m',symbol='BTC-USDT-SWAP')
            for a,b in zip(partial['plots'],result['plots']):self.assertEqual(a['values'],b['values'][:n])

    def test_arrays_and_for_loops_mutate_and_recompute_each_iteration(self):
        f=frame(6);f['close']=np.arange(1,7,dtype=float)
        source=('//@version=6\nindicator("arrays")\nvar array<float> xs=array.new<float>()\n'
                'array.push(xs,close)\ntotal=0.0\nfor i=0 to array.size(xs)-1\n'
                '    total += array.get(xs,i)\nplot(total)\nplot(array.avg(xs))')
        plots=run(source,f)['plots']
        self.assertEqual(plots[0]['values'],[1.,3.,6.,10.,15.,21.])
        self.assertEqual(plots[1]['values'],[1.,1.5,2.,2.5,3.,3.5])
        for n in (2,4):
            partial=run(source,{k:v[:n] for k,v in f.items()})
            for a,b in zip(partial['plots'],plots):self.assertEqual(a['values'],b['values'][:n])

    def test_array_bounds_and_loop_step_validation_fail_closed(self):
        f=frame(2)
        with self.assertRaises(PineError):run('indicator("bounds")\na=array.from(1,2)\nplot(array.get(a,2))',f)
        with self.assertRaises(PineError):run('indicator("step")\nfor i=0 to 2 by 0\n    plot(close)',f)
        with self.assertRaises(PineError):compile_source('indicator("break")\nbreak')

    def test_request_security_resamples_confirmed_higher_timeframe_without_lookahead(self):
        f=frame(12);f['close']=np.arange(1,13,dtype=float);f['open']=f['close'];f['high']=f['close']+.2;f['low']=f['close']-.2
        source=('//@version=5\nindicator("MTF")\nhtf=request.security(syminfo.tickerid,"15",ta.sma(close,2))\nplot(htf)')
        result=run(source,f,timeframe='5m',symbol='BTC-USDT-SWAP')['plots'][0]['values']
        self.assertEqual(result[:5],[None]*5)
        self.assertAlmostEqual(result[5],4.5)
        self.assertEqual(result[5:8],[result[5]]*3)
        self.assertAlmostEqual(result[8],7.5)
        for n in (5,8,11):
            prefix=run(source,{k:v[:n] for k,v in f.items()},timeframe='5m',symbol='BTC-USDT-SWAP')['plots'][0]['values']
            self.assertEqual(prefix,result[:n])

    def test_request_security_gaps_on_symbol_and_lookahead_guards(self):
        f=frame(10)
        base='//@version=5\nindicator("MTF")\nplot(request.security(syminfo.tickerid,"15",close,{gaps},{lookahead}))'
        off=run(base.format(gaps='barmerge.gaps_off',lookahead='barmerge.lookahead_off'),f,timeframe='5m',symbol='BTC-USDT-SWAP')['plots'][0]['values']
        on=run(base.format(gaps='barmerge.gaps_on',lookahead='barmerge.lookahead_off'),f,timeframe='5m',symbol='BTC-USDT-SWAP')['plots'][0]['values']
        self.assertTrue(np.isfinite(off[2]));self.assertIsNone(on[1]);self.assertIsNone(on[3])
        with self.assertRaises(PineError):run(base.format(gaps='barmerge.gaps_off',lookahead='barmerge.lookahead_on'),f,timeframe='5m',symbol='BTC-USDT-SWAP')
        source='indicator("MTF")\nplot(request.security("ETH-USDT-SWAP","15",close))'
        with self.assertRaises(PineError):run(source,f,timeframe='5m',symbol='BTC-USDT-SWAP')

    def test_indicator_overload_and_numeric_domain_checks(self):
        source=('indicator("overload")\nplot(ta.highest(5))\nplot(ta.lowest(length=5))\n'
                'plot(ta.pivothigh(high,2,2))')
        self.assertEqual(len(run(source,frame(20))['plots']),3)
        for expr in ('ta.dmi(0,14)','ta.supertrend(3,0)','ta.pivothigh(0,2)','ta.vwma(close,0)'):
            with self.subTest(expr=expr),self.assertRaises(PineError):run('indicator("x")\nplot('+expr+')',frame(20))

    def test_inputs_reject_unknown_and_options(self):
        source = 'indicator("i")\nn=input.int(3,options=[3,5])\nplot(ta.sma(close,n))'
        for inputs in ({'typo': 3}, {'n': 4}, {'n': True}, {'n': float('nan')}):
            with self.subTest(inputs=inputs), self.assertRaises(PineError):
                run(source, frame(), inputs)

    def test_source_input_and_metadata(self):
        source = 'indicator("i")\ns=input.source(close,title="Price")\nplot(s)'
        result = run(source, frame(), {'s': 'high'})
        self.assertEqual(result['plots'][0]['values'], frame()['high'].tolist())
        self.assertEqual(result['input_specs'][0]['value'], 'high')

    def test_saved_input_profile_is_immutable_and_legacy_loads(self):
        source = 'strategy("i")\nn=input.int(3)\nif bar_index==n\n    strategy.entry("L",strategy.long)'
        with tempfile.TemporaryDirectory() as temp, patch('alpha_v7_pine.ROOT', Path(temp)):
            first, second, legacy = save(source, {'n': 4}), save(source, {'n': 8}), save(source)
            self.assertNotEqual(first, second)
            self.assertEqual(load_profile(first), (source, {'n': 4}))
            self.assertEqual(run_saved(first, frame())['events'][0]['bar'], 4)
            self.assertEqual(run_saved(second, frame())['events'][0]['bar'], 8)
            self.assertEqual(run_saved(legacy, frame())['events'][0]['bar'], 3)
            Path(temp, first+'.json').write_text('{}')
            with self.assertRaises(PineError):load_profile(first)


class FlowCompletionTests(unittest.TestCase):
    def setUp(self):
        _STATES.clear()

    def book(self, ts=100000):
        return dict(timestamp=ts, bids=[[99, 100], [98, 1], [97, 1], [96, 1], [95, 1]],
                    asks=[[101, 1], [102, 1], [103, 1], [104, 1], [105, 1]])

    def test_same_snapshot_does_not_manufacture_wall_persistence(self):
        state = FlowState()
        for now in (100, 101, 104, 107):state.depth(self.book(), .01, now)
        self.assertFalse(state.snapshot(107)['book_confirmed'])
        self.assertEqual(state.walls[('bid', 99)]['count'], 1)

    def test_malformed_book_invalidates_previous_snapshot(self):
        for bids in ([[98, 1], [99, 1]], [[99, 1], [99, 2]], [[99, float('nan')]], [[99]]):
            state = FlowState();state.depth(self.book(), .01, 100)
            book = self.book(101000);book['bids'] = bids
            with self.assertRaises((ValueError, IndexError)):state.depth(book, .01, 101)
            self.assertIsNone(state.snapshot(101)['book'])
            self.assertFalse(state.walls)

    def test_nonfinite_contract_or_notional_rejected(self):
        state = FlowState()
        with self.assertRaises(ValueError):state.depth(self.book(), float('nan'), 100)
        row = dict(id='x', timestamp=100000, price=1e300, amount=1e300, side='buy')
        self.assertFalse(state.trade(row, 1, 100))
        self.assertIsNone(state.snapshot(100)['observed_cvd_usdt'])

    def test_subscribe_ack_does_not_reset_rest_history(self):
        stream = PublicStream();key = ('okx', 'T');stream.targets['T'] = (key, .01, 100)
        state = _STATES.setdefault(key, FlowState());state.cvd = 5;state.started = 99
        stream.message(json.dumps(dict(event='subscribe', arg=dict(channel='trades', instId='T'))), 100)
        self.assertEqual(state.cvd, 5)
        self.assertEqual(state.source, 'REST抽样')

    def test_each_ws_channel_has_independent_exchange_timestamp(self):
        stream = PublicStream();key = ('okx', 'T');stream.targets['T'] = (key, .01, 100)
        stream.message(json.dumps(dict(arg=dict(channel='trades', instId='T'), data=[
            dict(tradeId='1', ts='99000', px='100', sz='2', side='buy')])), 100)
        state = _STATES[key]
        self.assertTrue(state.snapshot(100)['channel_status']['trades'])
        self.assertFalse(state.snapshot(100)['channel_status']['books5'])
        book = self.book();book['ts'] = str(book.pop('timestamp'))
        stream.message(json.dumps(dict(arg=dict(channel='books5', instId='T'), data=[book])), 100)
        self.assertTrue(all(state.snapshot(100)['channel_status'].values()))
        self.assertFalse(state.snapshot(120)['channel_status']['trades'])

    def test_partial_ws_outage_uses_rest_and_new_observation(self):
        exchange = Mock();exchange.id = 'okx'
        exchange.market.return_value = dict(linear=True, quote='USDT', contractSize=.01)
        exchange.fetch_order_book.return_value = self.book(120000)
        exchange.fetch_trades.return_value = [dict(id=str(i), timestamp=120000, price=100, amount=2, side='buy') for i in range(6)]
        exchange.fetch_funding_rate.return_value = {'fundingRate': .0001}
        exchange.fetch_open_interest.return_value = {'openInterestAmount': 300}
        state = _STATES.setdefault(('okx', 'T'), FlowState())
        state.source = 'WebSocket逐笔+5档盘口';state.ws_at = 120
        state.ws_channels = {'trades': 120, 'books5': 100};state.cvd = 5000;state.started = 90
        result = snapshot(exchange, 'T', 120)
        self.assertTrue(result['fresh']);self.assertEqual(result['observed_cvd_usdt'], 12)
        self.assertTrue(result['coverage'].startswith('REST'))


class ProfileAPITests(test_v72_api.APITests):
    def test_preview_saved_profile_and_live_use_same_inputs(self):
        import alpha_fast_v7 as strategy
        source = 'strategy("profile")\nn=input.int(287)\nif bar_index==n\n    strategy.entry("L",strategy.long)'
        rows = self.rows()
        with tempfile.TemporaryDirectory() as temp, patch('alpha_v7_pine.ROOT', Path(temp)), patch.object(strategy, '_RUNTIME', {}):
            response = self.client.post('/tv/api/pine', json=dict(source=source, rows=rows, inputs={'n':286}, save=True))
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json();identifier = result['script_id']
            restored = self.client.get('/tv/api/pine/'+identifier)
            self.assertEqual(restored.json()['inputs'], {'n':286})
            f = {k:np.array([r['timestamp' if k=='ts' else k] for r in rows[-288:]]) for k in ('ts','open','high','low','close','volume')}
            self.assertEqual(run_saved(identifier, f)['events'], result['events'])
            pred = strategy.decide('TEST', dict(frames={'5m':f}, ticker_last=float(f['close'][-1]), spread_bps=0,
                missing=[], as_of_ms=int(f['ts'][-1])+300000), dict(strategy='pine_import', pine_id=identifier))
            self.assertEqual(pred['signal'], 'FLAT')
            self.assertIn('未触发', pred['reason'])

    def test_unknown_input_api_rejected(self):
        response = self.client.post('/tv/api/pine', json=dict(source='indicator("i")\nplot(close)', rows=self.rows(), inputs={'typo':1}, save=True))
        self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
