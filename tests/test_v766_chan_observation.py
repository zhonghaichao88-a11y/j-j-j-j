"""V7.6.6 离线测试（D 归属）：背驰观察图层 group 修正。

契约 §8：背驰观察 overlay 的 group=='缠论'，且能通过前端 v72Layers 过滤被保留；
旧 bug 名 '缠论观察（不下单）' 不得再出现（该组名不在前端图层键里，会被 v72Draw 过滤丢弃）。
确定性：monkeypatch alpha_v7_analysis.chan 注入受控 observations，不依赖随机行情是否刚好走出背驰。
不联网、不下单。
"""
import unittest
import numpy as np
import alpha_v7_analysis as A

# 与前端 v7_analysis.js v72Layers 默认键保持一致
V72_LAYER_KEYS = {'结构', '流动性', '供需', '趋势', '缠论', '动量', '时段'}


def frame(n=260):
 rng = np.random.default_rng(32)
 c = 100 + np.cumsum(rng.normal(.025, .6, n))
 o = np.r_[c[0], c[:-1]]
 return dict(ts=np.arange(n)*300000, open=o, close=c,
             high=np.maximum(o, c)+.3, low=np.minimum(o, c)-.3,
             volume=rng.uniform(10, 100, n))


class ChanObservationGroupTests(unittest.TestCase):
 def _run_with_observation(self):
  f = frame()
  obs = dict(label='盘整背驰观察', side=1, ts=int(f['ts'][-1]),
              price=100.0, known_at=int(f['ts'][-1]), level=0)
  fake = dict(fractals=[], strokes=[], segments=[], zones=[], signals=[],
              levels=[], observations=[obs], signal_status={}, rule='CX-74')
  orig = A.chan
  A.chan = lambda *a, **k: fake
  try:
   return A.analyze(f, {'chan_level': 1})
  finally:
   A.chan = orig

 def test_observation_overlay_group_is_chanlun(self):
  r = self._run_with_observation()
  obs_layers = [o for o in r['overlays'] if o.get('state') == '观察（不下单）']
  self.assertTrue(obs_layers, '应至少产出一条背驰观察 overlay')
  for o in obs_layers:
   self.assertEqual(o['group'], '缠论', '背驰观察 group 必须为 缠论，否则被前端过滤丢弃')
   self.assertEqual(o['label'], '盘整背驰观察')
   self.assertEqual(o['type'], 'point')

 def test_observation_semantic_state_marked_no_order(self):
  r = self._run_with_observation()
  obs_layers = [o for o in r['overlays'] if o.get('state') == '观察（不下单）']
  self.assertTrue(obs_layers)
  for o in obs_layers:
   # 不下单语义通过 state 表达（前端渲染灰色、不进入交易模式图层）
   self.assertEqual(o['state'], '观察（不下单）')

 def test_no_legacy_buggy_group_name(self):
  r = self._run_with_observation()
  bad = [o for o in r['overlays'] if o.get('group') == '缠论观察（不下单）']
  self.assertEqual(bad, [], '旧 bug 组名不得再出现')

 def test_observation_passes_frontend_layer_filter(self):
  r = self._run_with_observation()
  # 复刻前端 v72Draw 的过滤：rows = overlays.filter(x => v72Layers[x.group])
  retained = [o for o in r['overlays'] if o.get('group') in V72_LAYER_KEYS]
  obs_ids = {(o['label'], o['ts']) for o in r['overlays'] if o.get('state') == '观察（不下单）'}
  kept_ids = {(o['label'], o['ts']) for o in retained if o.get('state') == '观察（不下单）'}
  self.assertEqual(obs_ids, kept_ids, '背驰观察必须通过 v72Layers 过滤被保留绘制')


if __name__ == '__main__':
 unittest.main()
