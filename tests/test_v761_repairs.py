import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from alpha_v7_pine import run, compile_source, save, PineError
from pine_v5.runtime import Runtime
from pine_v5.live_contract import live_capability, json_safe
import alpha_fast_v7 as v
from test_v72_api import APITests


def frame(n=6):
    return dict(ts=np.arange(n)*300000,open=np.full(n,100.),high=np.full(n,101.),low=np.full(n,99.),close=np.full(n,100.),volume=np.ones(n))

class RepairTests(unittest.TestCase):
    def test_zero_pyramiding_stays_one(self):
        r=run('strategy("p",pyramiding=0)\nstrategy.entry("L",strategy.long,qty=1)',frame())
        self.assertEqual(r['broker']['positions']['L']['qty'],1)
    def test_pyramiding_across_ids(self):
        r=run('strategy("p",pyramiding=1)\nif bar_index==0\n    strategy.entry("A",strategy.long)\nif bar_index==2\n    strategy.entry("B",strategy.long)',frame())
        self.assertEqual(list(r['broker']['positions']),['A'])
    def test_pending_same_id_replaced(self):
        r=Runtime(compile_source('strategy("p")\nstrategy.entry("L",strategy.long,limit=50)'),frame());r.run()
        self.assertEqual(len(r.broker.pending_entries),1)
        self.assertFalse(r.broker.positions)
    def test_reverse_different_id(self):
        r=run('strategy("p")\nif bar_index==0\n    strategy.entry("A",strategy.long)\nif bar_index==2\n    strategy.entry("B",strategy.short)',frame())
        self.assertEqual(list(r['broker']['positions']),['B'])
        self.assertEqual(r['broker']['positions']['B']['side'],-1)
    def test_partial_metadata_and_remaining(self):
        r=run('strategy("p")\nif bar_index==0\n    strategy.entry("A",strategy.long,qty=10)\nif bar_index==2\n    strategy.close("A",qty_percent=50)',frame())
        self.assertEqual(r['broker']['positions']['A']['qty'],5)
        self.assertEqual(next(e for e in r['events'] if e['kind']=='close')['qty_percent'],50)
    def test_trail_waits_activation(self):
        src='strategy("p")\nif bar_index==0\n    strategy.entry("L",strategy.long)\nstrategy.exit("X",from_entry="L",trail_points=10,trail_offset=2)'
        rt=Runtime(compile_source(src),frame());rt.broker.tick_size=1;rt.run()
        self.assertIsNone(rt.broker.exit_orders['X']['cur_stop'])
        self.assertFalse(rt.broker.exit_orders['X']['activated'])
    def test_trail_activation_and_distance(self):
        f=frame(3);f['high'][2]=112;f['close'][2]=111;f['low'][2]=100
        rt=Runtime(compile_source('strategy("p")\nif bar_index==0\n    strategy.entry("L",strategy.long)\nstrategy.exit("X",from_entry="L",trail_points=10,trail_offset=2)'),f)
        rt.broker.tick_size=1;rt.run()
        self.assertEqual(rt.broker.exit_orders['X']['cur_stop'],110)
    def test_stop_gap_not_optimistic(self):
        f=frame(4);f['open'][2]=80;f['low'][2]=79;f['high'][2]=81;f['close'][2]=80
        r=run('strategy("p")\nif bar_index==0\n    strategy.entry("L",strategy.long)\nstrategy.exit("X",from_entry="L",stop=90)',f)
        self.assertEqual(r['broker']['closed_trades'][0]['exit_price'],80)
    def test_repeated_exit_updates_limit(self):
        src='strategy("p")\nif bar_index==0\n    strategy.entry("L",strategy.long)\nstrategy.exit("X",from_entry="L",stop=90,limit=bar_index>1 ? 100 : 120)'
        r=run(src,frame());self.assertEqual(r['broker']['closed_trades'][0]['exit_bar'],3)
    def test_unsupported_flags_rejected(self):
        for flag in ('calc_on_every_tick','process_orders_on_close'):
            with self.assertRaises(PineError):run(f'strategy("p",{flag}=true)\nstrategy.entry("L",strategy.long)',frame())
    def test_live_rejects_complex_orders_in_parameters(self):
        with tempfile.TemporaryDirectory() as tmp,patch('alpha_v7_pine.ROOT',Path(tmp)):
            for statement in ('strategy.entry("L",strategy.long,limit=50)', 'strategy.close("L",qty_percent=50)', 'strategy.exit("X",from_entry="L",stop=90)', 'plot(strategy.position_size)'):
                ident=save('strategy("p")\n'+statement)
                with self.assertRaises(ValueError):v.validate_params({'strategy':'pine_import','pine_id':ident})
    def test_exit_na_waits_without_crashing(self):
        r=run('strategy("p")\nstrategy.exit("X",from_entry="L",stop=na)',frame())
        self.assertFalse(r['events'])
    def test_simple_live_supported(self):
        self.assertTrue(live_capability(compile_source('strategy("p")\nif close>open\n    strategy.entry("L",strategy.long)'))['supported'])
    def test_incompatible_existing_position_not_full_closed(self):
        with tempfile.TemporaryDirectory() as tmp,patch('alpha_v7_pine.ROOT',Path(tmp)):
            ident=save('strategy("p")\nstrategy.close("L",qty_percent=50)')
            p=dict(entry=100,side='long',opened_at=0,v7_max_seconds=100000,v7_pine_id=ident,v7_pine_entry_id='L',base_sl_pct=.1,sl=90)
            plan=v.exit_plan(p,100,1800,{'5m':frame()})
            self.assertFalse(plan['close']);self.assertIn('部分平仓',p['v7_pine_error'])

class RepairAPITests(APITests):
    def test_table_http_json(self):
        r=self.client.post('/tv/api/pine',json={'source':'indicator("p")\nvar t=table.new(position.top_right,2,2)\ntable.cell(t,0,0,"中文")','rows':self.rows()})
        self.assertEqual(r.status_code,200,r.text)
        data=r.json();self.assertEqual(data['draw_objects']['1']['cells']['0,0']['text'],'中文')
        self.assertEqual(len(data['timestamps']),data['bars'])
    def test_preview_reveals_live_limit(self):
        r=self.client.post('/tv/api/pine',json={'source':'strategy("p")\nstrategy.entry("L",strategy.long,limit=1)','rows':self.rows()})
        self.assertEqual(r.status_code,200,r.text)
        self.assertFalse(r.json()['live_capability']['supported'])
