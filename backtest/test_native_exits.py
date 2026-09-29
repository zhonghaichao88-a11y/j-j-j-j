"""Offline integration tests for exchange-hosted native exits."""
import sys, unittest
from pathlib import Path
from unittest.mock import patch, Mock
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(Path(__file__).parent))
import alpha_engine as eng


def make_position(version):
    p={"side":"long","entry":100.0,"tp":110.0,"sl":95.0,
       "tp_attach_clordid":"oldtp","sl_attach_clordid":"oldsl",
       "base_tp_pct":0.10,"base_sl_pct":0.05,"filled":10.0,"live":True,
       "strategy_mode":"FAST","fast_engine":"TREND_BREAK"}
    if version in ("v6","v62"):
        p.update(fast_version="v6",v6_exit_policy="adaptive",v6_round_cost=0.0022)
        if version=="v62": p["v62"]=True
    else:
        p["fast_version"]=version
    return p


class NativeExitIntegration(unittest.TestCase):
    def setUp(self):
        eng.STATE["positions"].clear()
        eng.STATE["gate_switches"]=dict(eng.DEFAULT_GATE_SWITCHES)

    def test_native_order_payloads(self):
        executor=eng.alpha_live
        fake=Mock()
        fake.price_to_precision.side_effect=lambda s,p:str(p)
        fake.create_order.side_effect=[{"id":"tp"},{"id":"sl"},{"id":"trail"}]
        executor._exchange=fake
        executor._ensure=Mock(return_value=fake)
        with patch.object(executor,"pos_mode",return_value="long_short_mode"), \
             patch.object(executor,"market",return_value={}), \
             patch.object(executor,"protection_status_set",return_value={"verified":True}):
            tp=executor.place_native_tp("BTC-USDT-SWAP","long",3,110,"tpcid")
            sl=executor.place_native_sl("BTC-USDT-SWAP","long",10,95,"slcid",trigger_type="mark")
            tr=executor.place_native_trailing("BTC-USDT-SWAP","long",10,0.02,102,"trcid")
        self.assertEqual((tp["client_id"],sl["client_id"],tr["client_id"]),("tpcid","slcid","trcid"))
        tp_args=fake.create_order.call_args_list[0].args
        sl_args=fake.create_order.call_args_list[1].args
        tr_args=fake.create_order.call_args_list[2].args
        self.assertEqual(tp_args[:3],("BTC/USDT:USDT","conditional","sell"))
        self.assertEqual(sl_args[:3],("BTC/USDT:USDT","conditional","sell"))
        self.assertEqual(tr_args[:3],("BTC/USDT:USDT","move_order_stop","sell"))
        trail_params=fake.create_order.call_args_list[2].args[5]
        self.assertEqual(trail_params["trailingPercent"],"2")
        self.assertEqual(trail_params["activePx"],"102.0")

    def test_ccxt_native_algo_request_mapping(self):
        import ccxt
        ex=ccxt.okx()
        market={"symbol":"BTC/USDT:USDT","id":"BTC-USDT-SWAP","spot":False,"margin":False,"contract":True,
                "swap":True,"future":False,"option":False,"base":"BTC","quote":"USDT","settle":"USDT",
                "baseId":"BTC","quoteId":"USDT","settleId":"USDT","type":"swap","linear":True,"inverse":False,
                "taker":0.0005,"maker":0.0002,"precision":{"amount":0.1,"price":0.1,"cost":1},
                "limits":{"leverage":{"max":100,"min":1},"amount":{"min":0.1,"max":None},
                          "price":{"min":None,"max":None},"cost":{"min":1,"max":None}},
                "info":{"lotSz":"0.1","ctVal":"0.01"}}
        ex.markets={"BTC/USDT:USDT":market}; ex.markets_by_id={"BTC-USDT-SWAP":[market]}
        base={"tdMode":"cross","hedged":True,"posSide":"long","algoClOrdId":"nativecid"}
        tp=ex.create_order_request("BTC/USDT:USDT","conditional","sell",10,None,
            dict(base,takeProfitPrice=110.0,tpOrdPx="-1",tpTriggerPxType="last"))
        sl=ex.create_order_request("BTC/USDT:USDT","conditional","sell",10,None,
            dict(base,stopLossPrice=95.0,slOrdPx="-1",slTriggerPxType="mark"))
        trail=ex.create_order_request("BTC/USDT:USDT","move_order_stop","sell",10,None,
            dict(base,trailingPercent="2",activePx="102"))
        self.assertEqual((tp["ordType"],tp["tpTriggerPx"],tp["tpOrdPx"]),("conditional","110","-1"))
        self.assertEqual((sl["ordType"],sl["slTriggerPx"],sl["slOrdPx"]),("conditional","95","-1"))
        self.assertEqual((trail["ordType"],trail["callbackRatio"],trail["activePx"]),("move_order_stop","0.02","102"))

    def test_arm_and_disarm_trail_all_versions(self):
        versions=("v3","v4","v5","v6","v62")
        for version in versions:
            symbol=f"{version}TEST"
            eng.STATE["positions"][symbol]=make_position(version)
            p=eng.STATE["positions"][symbol]
            with patch.object(eng,"_current_position_contracts",return_value=10.0), \
                 patch.object(eng.alpha_live,"place_native_trailing",return_value={"client_id":"newtrail"}), \
                 patch.object(eng,"_cancel_active_algo") as cxl, \
                 patch.object(eng.alpha_live,"protection_status_set",return_value={"verified":True}), \
                 patch.object(eng,"_persist"):
                self.assertTrue(eng._arm_native_trail(symbol,p,eng.DEFAULT))
                # 新模型：固定SL保留兜底，移动单叠加在 native_trail_id，arm 时不撤任何单。
                self.assertEqual(p["sl_attach_clordid"],"oldsl")
                self.assertEqual(p["native_trail_id"],"newtrail")
                self.assertTrue(p["native_trail_armed"])
                cxl.assert_not_called()
            with patch.object(eng,"_current_position_contracts",return_value=10.0), \
                 patch.object(eng.alpha_live,"place_native_sl",return_value={"client_id":"fixedsl"}) as pssl, \
                 patch.object(eng,"_cancel_active_algo") as cxl2, \
                 patch.object(eng.alpha_live,"protection_status_set",return_value={"verified":True}), \
                 patch.object(eng,"_persist"):
                self.assertTrue(eng._disarm_native_trail(symbol,p))
                # 固定SL一直都在，disarm 只撤移动单，不重挂固定SL。
                self.assertEqual(p["sl_attach_clordid"],"oldsl")
                self.assertFalse(p["native_trail_armed"])
                cxl2.assert_called_once()
                pssl.assert_not_called()

    def test_arm_and_disarm_partial_all_versions(self):
        versions=("v3","v4","v5","v6","v62")
        for version in versions:
            symbol=f"{version}P"
            eng.STATE["positions"][symbol]=make_position(version)
            p=eng.STATE["positions"][symbol]
            cids=["tp1id","tp2id","finalid"]
            with patch.object(eng,"_current_position_contracts",return_value=10.0), \
                 patch.object(eng.alpha_live,"split_contracts",return_value=[3.0,3.0,4.0]), \
                 patch.object(eng.alpha_live,"place_native_tp",side_effect=[{"client_id":x} for x in cids]), \
                 patch.object(eng,"_cancel_active_algo"), \
                 patch.object(eng.alpha_live,"protection_status_set",return_value={"verified":True}), \
                 patch.object(eng,"_persist"):
                self.assertTrue(eng._arm_native_partial(symbol,p,eng.DEFAULT))
                self.assertEqual(p["partial_tp_order_ids"],cids)
                self.assertEqual(p["tp_attach_clordid"],"finalid")
            with patch.object(eng,"_current_position_contracts",return_value=10.0), \
                 patch.object(eng.alpha_live,"place_native_tp",return_value={"client_id":"fullid"}), \
                 patch.object(eng,"_cancel_active_algo"), \
                 patch.object(eng.alpha_live,"protection_status_set",return_value={"verified":True}), \
                 patch.object(eng,"_persist"):
                self.assertTrue(eng._disarm_native_partial(symbol,p))
                self.assertEqual(p["tp_attach_clordid"],"fullid")
                self.assertFalse(p["native_partial_armed"])

    def test_switch_change_applies_to_live_positions(self):
        eng.STATE["positions"]["BTC"]=make_position("v6")
        with patch.object(eng,"_apply_exit_switch") as apply_switch, \
             patch.object(eng,"_persist"):
            result=eng.set_gate_switches({"partial_tp":True})
        self.assertTrue(result["partial_tp"])
        apply_switch.assert_called_once_with("partial_tp",True)


if __name__=="__main__":
    unittest.main(verbosity=2)
