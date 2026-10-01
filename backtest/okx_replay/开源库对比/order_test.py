"""方案三在币安前100上：仓位满时“谁先开”对结果的影响。"""
import glob, os, json, random, numpy as np, pandas as pd, types
import adx_bt as A
M = types.ModuleType('M'); _src = open('adx_more.py', encoding='utf-8').read(); exec(_src[:_src.index('out = []')], M.__dict__)
def npz(p): z = np.load(p); return {k: z[k] for k in ('ts', 'open', 'high', 'low', 'close')}
fr = {os.path.basename(p)[:-4]: npz(p) for p in glob.glob('/home/user/okx_data/bntop/*.npz')}
btc = M.daily(fr['BTCUSDT'])
ms = lambda x: int(pd.Timestamp(x, tz='UTC').timestamp() * 1000)
vol_rank = [s for s, _ in json.load(open('/home/user/doubao/top100.json'))]
# 每个币过去的波动（1小时收盘涨跌幅的标准差，用整段数据；只用来做排序对照）
volat = {s: np.nanstd(np.diff(np.log(f['close']))) for s, f in fr.items()}
orders = {'按成交额（大币优先，同豆包）': vol_rank, '按成交额反过来（小币优先）': vol_rank[::-1], '按字母（我原来的）': sorted(fr),
          '按波动从大到小': sorted(fr, key=lambda s: -volat[s]), '按波动从小到大': sorted(fr, key=lambda s: volat[s])}
for k in range(3):
    o = sorted(fr); random.Random(k).shuffle(o); orders[f'随机{k+1}'] = o
for name, order in orders.items():
    f2 = {f'{i:03d}_{s}': fr[s] for i, s in enumerate(order) if s in fr}
    T = A.run(f2, btc, fee=0.001, t0=ms('2024-06-01'), t1=ms('2026-10-01'), wallet=990, short=True); T = T[T.why != '结束']
    w = T.r > 0
    print(f"{name:20s} 笔数 {len(T):6d} 胜率 {w.mean()*100:.1f}% PF {T.r[w].sum() / -T.r[~w].sum():.2f} 每笔 {T.r.mean()*100:+.3f}% 合计 {T.r.sum()*100:+.0f}U", flush=True)
