"""V7.6.14：最大持仓/单笔风险/杠杆在运行中可改，并对之后的新开仓立即生效。"""
import unittest
from unittest.mock import patch
import alpha_engine as ae


class RunningLimitTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {'max_positions': 5, 'risk_pct': 0.02, 'leverage': 3, 'other': 1}
        self.p1 = patch.object(ae, 'RUNNING_CFG', self.cfg); self.p1.start()
        self.p2 = patch.dict(ae.STATE, {'running': True}); self.p2.start()
        self.p3 = patch.object(ae, '_activity', lambda *a, **k: None); self.p3.start()

    def tearDown(self):
        self.p3.stop(); self.p2.stop(); self.p1.stop()

    def test_update_mutates_the_loop_config_object(self):
        out = ae.update_running_limits(max_positions=2, risk_pct=0.005, leverage=None)
        self.assertEqual(out, {'max_positions': 2, 'risk_pct': 0.005, 'leverage': 3})
        self.assertEqual(self.cfg['max_positions'], 2); self.assertEqual(self.cfg['other'], 1)   # 交易循环拿到的就是这个对象

    def test_out_of_range_is_rejected_without_partial_change(self):
        with self.assertRaises(ValueError): ae.update_running_limits(max_positions=3, risk_pct=0.5)
        self.assertEqual(self.cfg['max_positions'], 5)

    def test_not_running_does_nothing(self):
        ae.STATE['running'] = False
        self.assertIsNone(ae.update_running_limits(max_positions=1)); self.assertEqual(self.cfg['max_positions'], 5)


class ApiTests(unittest.TestCase):
    def test_tv_endpoint_and_params_show_running_values(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import tv_bridge
        app = FastAPI(); app.include_router(tv_bridge.router); c = TestClient(app)
        cfg = {'max_positions': 5, 'risk_pct': 0.02, 'leverage': 3}
        with patch.object(ae, 'RUNNING_CFG', cfg), patch.dict(ae.STATE, {'running': True}), patch.object(ae, '_activity', lambda *a, **k: None):
            r = c.post('/tv/api/limits', json={'max_positions': 3, 'risk_pct': 0.01}).json()
            self.assertEqual(r['limits'], {'max_positions': 3, 'risk_pct': 0.01, 'leverage': 3})
            self.assertEqual(c.get('/tv/api/params').json()['limits']['max_positions'], 3)
            self.assertEqual(c.post('/tv/api/limits', json={'max_positions': 20}).status_code, 400)
        with patch.dict(ae.STATE, {'running': False}):
            r = c.post('/tv/api/limits', json={'max_positions': 3}).json()
            self.assertIsNone(r['limits']); self.assertIn('下次启动', r['message'])


if __name__ == '__main__':
    unittest.main()
