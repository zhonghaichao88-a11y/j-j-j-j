"""方案三的赚钱是不是靠“10个仓位满了跳过哪些信号”：
A 不限仓位（所有信号都做），B 10仓按字母顺序（原测试），C 10仓随机顺序（5次）。成本0.15%。"""
import json, os, types, random, sys
import numpy as np, pandas as pd
import adx_bt as A
D = '/home/user/okx_data'
M = types.ModuleType('M'); _src = open('adx_more.py', encoding='utf-8').read(); exec(_src[:_src.index('out = []')], M.__dict__)
def ms(x): return int(pd.Timestamp(x, tz='UTC').timestamp() * 1000)
def st(T, stake=100):
    r = T.r.values; return dict(笔数=len(r), 胜率=f'{(r > 0).mean()*100:.0f}%', 每笔=f'{r.mean()*100:+.3f}%', 合计U=f'{(r*stake).sum():+.0f}')
def npz(p): z = np.load(p); return {k: z[k] for k in ('ts', 'open', 'high', 'low', 'close')}
sets = {}
s = json.load(open(f'{D}/universe_bnx.json'))
sets['币安独有'] = ({x: M.npz(f'{D}/{x}_bn1h.npz') for x in s if os.path.exists(f'{D}/{x}_bn1h.npz')}, M.btc_bnf(), (('第一年','2024-09-29','2025-09-29'),('第二年','2025-09-29','2026-09-01')))
s = json.load(open(f'{D}/universe_bnold.json')); fr = {x: M.npz(f'{D}/{x}_bnold1h.npz') for x in s if os.path.exists(f'{D}/{x}_bnold1h.npz')}
sets['更早年份'] = (fr, M.daily(fr['BTCUSDT']), (('2022年','2022-01-01','2023-01-01'),('2023年','2023-01-01','2024-01-01'),('2024前三季','2024-01-01','2024-10-01')))
s = json.load(open(f'{D}/okx64/universe.json')); fr = {x: npz(f'{D}/okx64/{x}_1h.npz') for x in s if os.path.exists(f'{D}/okx64/{x}_1h.npz')}
b = npz(f'{D}/okx64/BTC_1d.npz'); sets['欧易新64'] = ({k: v for k, v in fr.items() if len(v['ts']) > 300}, dict(ts=b['ts'], close=b['close']), (('第一年','2024-09-29','2025-09-29'),('第二年','2025-09-29','2026-10-01')))
rows = []
for name, (fr, btc, per) in sets.items():
    for lab, a, c in per:
        T = A.run(fr, btc, fee=0.0015, t0=ms(a), t1=ms(c), wallet=10**9, max_open=10**6, short=True)
        rows.append(dict(数据=name, 段=lab, 方式='不限仓位(全部信号)', **st(T))); print(rows[-1], flush=True)
        T = A.run(fr, btc, fee=0.0015, t0=ms(a), t1=ms(c), wallet=990, short=True)
        rows.append(dict(数据=name, 段=lab, 方式='10仓·字母顺序', **st(T))); print(rows[-1], flush=True)
        for seed in range(5):
            keys = list(fr); random.Random(seed).shuffle(keys)
            f2 = {f'{i:04d}_{k}': fr[k] for i, k in enumerate(keys)}
            T = A.run(f2, btc, fee=0.0015, t0=ms(a), t1=ms(c), wallet=990, short=True)
            rows.append(dict(数据=name, 段=lab, 方式=f'10仓·随机顺序{seed+1}', **st(T))); print(rows[-1], flush=True)
R = pd.DataFrame(rows); R.to_csv('adx_slot.csv', index=False)
pd.set_option('display.width', 250); print(R.to_string(index=False)); print('DONE')
