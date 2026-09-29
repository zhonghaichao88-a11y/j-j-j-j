"""V6.1 structure targets, protection switches and exchange acknowledgements."""
import ast
import copy
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT)); sys.path.insert(0,str(Path(__file__).parent))
import alpha_fast_v6 as v6
from test_v6 import extract

def live_methods():
    tree=ast.parse((ROOT/'alpha_live.py').read_text())
    names={'_entry_protection','amend_sl_only','amend_protection'}
    nodes=[n for cls in tree.body if isinstance(cls,ast.ClassDef) for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in names]
    namespace={'symbol_to_ccxt':lambda s,t:s}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'live-extract','exec'),namespace)
    return namespace

class Tests61(unittest.TestCase):
    def pos(self,short=False):
        return dict(side='short' if short else 'long',entry=100,sl=105 if short else 95,
                    tp=90 if short else 110,base_sl_pct=.05,base_tp_pct=.1,opened_at=0,
                    v6_max_seconds=10000,v6_exit_policy='adaptive',v6_round_cost=.0022,v6_protect_at_r=.8)

    def test_nearby_pivot_capped(self):
        f={'high':np.array([101,102,108,105,103,104,103]),'low':np.array([99,98,97,98,99,98,99])}
        t,why=v6.reachable_target({'5m':f,'15m':f},100,1,115,2)
        self.assertAlmostEqual(t,107.6)
        self.assertIn('内侧',why)

    def test_never_extend_target(self):
        f={'high':np.array([101,102,108,105,103,104,103]),'low':np.array([99,98,97,98,99,98,99])}
        self.assertEqual(v6.reachable_target({'5m':f,'15m':f},100,1,105,2)[0],105)

    def test_short_pivot(self):
        f={'high':np.array([101,102,103,102,101,102,101]),'low':np.array([99,98,92,95,97,96,97])}
        self.assertAlmostEqual(v6.reachable_target({'5m':f,'15m':f},100,-1,85,2)[0],92.4)

    def test_protect_long_and_short(self):
        for short,px in [(False,104.5),(True,95.5)]:
            p=self.pos(short);d=-1 if short else 1
            stop=v6.exit_plan(p,px,300,trailing=True)['stop']
            self.assertGreater(d*(stop-100),.22)
            self.assertGreater(d*(px-stop),0)
            self.assertIsNone(v6.exit_plan(p,px,300,trailing=False)['stop'])

    def test_near_target_lock(self):
        p=self.pos();stop=v6.exit_plan(p,108,300,trailing=True)['stop']
        self.assertGreaterEqual(stop,104.4)
        p['sl']=109
        self.assertIsNone(v6.exit_plan(p,108,300,trailing=True)['stop'])

    def test_no_early_tighten(self):
        self.assertIsNone(v6.exit_plan(self.pos(),101,300,trailing=True)['stop'])

    def test_partial_cost_floor(self):
        p=self.pos();p['v6_round_cost']=.004;p['base_tp_pct']=.008
        self.assertEqual(v6.partial_target_pct(p,.5),.006)
        self.assertGreater(v6.partial_floor_pct(p,'TP1'),.004)
        p.pop('v6_exit_policy')
        self.assertEqual(v6.partial_floor_pct(p,'TP1'),.0005)

    def test_execution_structure_preserved(self):
        fn=live_methods()['_entry_protection'];ex=Mock();ex.price_to_precision.side_effect=lambda s,p:str(p)
        protection=dict(tp=110,sl=95,reference=100,cost=.0022)
        self.assertEqual(fn(None,ex,'X','long',101,.1,.05,protection),(110,95))
        with self.assertRaises(ValueError):fn(None,ex,'X','long',103,.1,.05,protection)
        self.assertEqual(fn(None,ex,'X','short',100,.1,.05,None),(90,105))

    def amend_mock(self):
        p=Mock();ex=Mock();p._ensure.return_value=ex;p._inst_id.return_value='X'
        ex.price_to_precision.side_effect=lambda s,x:str(x)
        ex.request.return_value={'data':[{'sCode':'0'}]}
        p._algo_client_id.side_effect=lambda x:x.get('algoClOrdId','')
        row=dict(algoClOrdId='s',algoId='1',slTriggerPx='95',slTriggerPxType='last')
        p._wait_for_attached_protection.return_value=[row]
        return p,ex,row

    def test_amend_requires_actual_new_price(self):
        fn=live_methods()['amend_sl_only'];p,ex,row=self.amend_mock()
        p.protection_status_sl_only.return_value={'verified':True,'orders':[row]}
        self.assertFalse(fn(p,'X','long',101,'s')['verified'])
        self.assertEqual(ex.request.call_args.args[3]['newSlTriggerPxType'],'last')
        p.protection_status_sl_only.return_value={'verified':True,'orders':[{**row,'slTriggerPx':'101'}]}
        self.assertTrue(fn(p,'X','long',101,'s')['verified'])

    def test_amend_requires_trigger_type(self):
        fn=live_methods()['amend_sl_only'];p,ex,row=self.amend_mock()
        p.protection_status_sl_only.return_value={'verified':True,'orders':[{**row,'slTriggerPx':'101','slTriggerPxType':'mark'}]}
        self.assertFalse(fn(p,'X','long',101,'s')['verified'])

    def test_old_stop_type_preserved(self):
        fn=live_methods()['amend_sl_only'];p,ex,row=self.amend_mock();row['slTriggerPxType']='mark'
        p.protection_status_sl_only.return_value={'verified':True,'orders':[{**row,'slTriggerPx':'101'}]}
        self.assertTrue(fn(p,'X','long',101,'s')['verified'])
        self.assertEqual(ex.request.call_args.args[3]['newSlTriggerPxType'],'mark')

    def test_pair_confirmation(self):
        fn=live_methods()['amend_protection'];p,ex,row=self.amend_mock()
        tp=dict(algoClOrdId='t',algoId='2',tpTriggerPx='110',tpTriggerPxType='last')
        p._wait_for_attached_protection.return_value=[tp,row]
        p.protection_status.return_value={'verified':True,'orders':[tp,row]}
        self.assertFalse(fn(p,'X','long',110,101,['t','s'])['verified'])
        p.protection_status.return_value={'verified':True,'orders':[tp,{**row,'slTriggerPx':'101'}]}
        self.assertTrue(fn(p,'X','long',110,101,['t','s'])['verified'])

    def test_live_native_trail_no_local_amend(self):
        p=self.pos();p.update(sl_attach_clordid='s',v6_engine='V6_TREND')
        c=Mock();c.get_ticker.return_value={'last':104.5};a=Mock()
        n=extract(['_live_manage_v6'],extra={'okx_client':c,'alpha_live':a,'gate_enabled':lambda k:True})
        n['_live_manage_v6']('X',p,{},{})
        self.assertEqual(p['sl'],95);a.amend_sl_only.assert_not_called()

if __name__=='__main__':unittest.main(verbosity=2)
