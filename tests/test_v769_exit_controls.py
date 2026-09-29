"""V7.6.9: 缠论3类点目标、缠论趋势仓、参数落盘、/tv 全局出场开关。"""
import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import alpha_fast_v7 as v7

BAR=300000


def frames(n=300):
    rng=np.random.default_rng(3);c=100+np.cumsum(rng.normal(0,.05,n));o=np.r_[c[0],c[:-1]]
    return {'5m':dict(ts=np.arange(n,dtype=np.int64)*BAR,open=o,close=c,high=np.maximum(o,c)+.05,low=np.minimum(o,c)-.05,volume=np.ones(n))}


def run(kind):
    fr=frames();f=fr['5m'];ts=int(f['ts'][-1]);px=float(f['close'][-1])
    label={'T3':'3买','T1':'1买'}[kind]
    event=dict(id='e',label=label,bsp_class=int(label[0]),kind=kind,side=1,known_at=ts,ts=ts-2*BAR,price=px-1,invalidation=px-1.2,evidence={},zone_id=None)
    fake=dict(candidates={},regime='测试',values={},
              events=[dict(label='LH',price=px+1.0)],  # 最近的小波段高点就在上方
              chan=dict(signals=[event],signal_status={},strokes=[],segments=[],zones=[]))
    with patch.object(v7,'analyze',return_value=fake):
        return v7.decide('X',dict(frames=fr,ticker_last=px,spread_bps=2,as_of_ms=ts+BAR+1000),
                         dict(strategy='chan_quant',chan_level=0,chan_mtf=0,chan_buy1=1,runner=1,max_stop=.05))


class ChanExitTests(unittest.TestCase):
    def test_third_point_not_cut_to_minor_swing(self):
        out=run('T3')
        self.assertEqual(out['signal'],'LONG',out['reason'])
        self.assertIn('缠论3类点',out['fast_strategy']['target_reason'])

    def test_non_third_point_still_uses_structure_limit(self):
        out=run('T1')
        self.assertEqual(out['signal'],'FLAT')
        self.assertIn('目标空间不足',out['reason'])

    def test_chan_runner_follows_switch(self):
        self.assertEqual(run('T3')['fast_strategy']['v7_exit_config']['runner'],1)


class ParamPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory();self.path=Path(self.dir.name)/'p.json'
        self.env=patch.dict(os.environ,{'ALPHA_V7_PARAMS_FILE':str(self.path)});self.env.start()
        self.old=dict(v7._RUNTIME)

    def tearDown(self):
        v7._RUNTIME=self.old;self.env.stop();self.dir.cleanup()

    def test_saved_params_survive_restart(self):
        v7.set_runtime_params(dict(strategy='chan_quant',chan_level=0,chan_buy1=1,max_hold_bars=48))
        v7._RUNTIME={}
        p=v7.load_saved_params()
        self.assertEqual((p['strategy'],p['chan_level'],p['chan_buy1'],p['max_hold_bars']),('chan_quant',0,1,48))
        self.assertEqual(v7.get_runtime_params()['chan_buy1'],1)

    def test_bad_or_stale_file_falls_back_to_defaults(self):
        self.path.write_text('{broken')
        self.assertEqual(v7.load_saved_params(),{});self.assertEqual(v7.get_runtime_params()['chan_level'],v7.PARAMS['chan_level'])
        self.path.write_text(json.dumps(dict(chan_level=9)))
        self.assertEqual(v7.load_saved_params(),{})
        self.path.write_text(json.dumps(dict(chan_level=0,removed_old_key=1)))
        self.assertEqual(v7.load_saved_params()['chan_level'],0)


class ExitSwitchApiTests(unittest.TestCase):
    def test_get_and_set_two_global_switches_only(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import alpha_engine,tv_bridge
        app=FastAPI();app.include_router(tv_bridge.router);client=TestClient(app)
        state={'trend_trail':True,'partial_tp':False};calls=[]
        with patch.object(alpha_engine,'gate_enabled',side_effect=lambda k:state[k]), \
             patch.object(alpha_engine,'set_gate_switches',side_effect=lambda v:(calls.append(v),state.update(v))):
            self.assertEqual(client.get('/tv/api/exit-switches').json()['switches'],state)
            r=client.post('/tv/api/exit-switches',json={'partial_tp':True}).json()
            self.assertEqual(calls,[{'partial_tp':True}]);self.assertTrue(r['switches']['partial_tp'])
            self.assertEqual(client.post('/tv/api/exit-switches',json={'model_ready':False}).status_code,200)
            self.assertEqual(calls[-1],{})  # 其他风控开关不能从这里改


if __name__=='__main__':
    unittest.main()
