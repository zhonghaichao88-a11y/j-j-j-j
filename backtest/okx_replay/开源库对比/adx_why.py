"""方案三各种出场方式的次数与平均盈亏（10仓、成本0.15%）。"""
import json, os, types, numpy as np, pandas as pd
import adx_bt as A
D = '/home/user/okx_data'
M = types.ModuleType('M'); _src = open('adx_more.py', encoding='utf-8').read(); exec(_src[:_src.index('out = []')], M.__dict__)
def ms(x): return int(pd.Timestamp(x, tz='UTC').timestamp() * 1000)
def npz(p): z = np.load(p); return {k: z[k] for k in ('ts', 'open', 'high', 'low', 'close')}
sets = []
s = json.load(open(f'{D}/universe_bnx.json'))
sets.append(('币安独有', {x: M.npz(f'{D}/{x}_bn1h.npz') for x in s if os.path.exists(f'{D}/{x}_bn1h.npz')}, M.btc_bnf(), '2024-09-29', '2026-09-01'))
s = json.load(open(f'{D}/universe_bnold.json')); fr = {x: M.npz(f'{D}/{x}_bnold1h.npz') for x in s if os.path.exists(f'{D}/{x}_bnold1h.npz')}
sets.append(('更早年份', fr, M.daily(fr['BTCUSDT']), '2022-01-01', '2024-10-01'))
s = json.load(open(f'{D}/okx64/universe.json')); fr = {x: npz(f'{D}/okx64/{x}_1h.npz') for x in s if os.path.exists(f'{D}/okx64/{x}_1h.npz')}
b = npz(f'{D}/okx64/BTC_1d.npz'); sets.append(('欧易新64', {k: v for k, v in fr.items() if len(v['ts']) > 300}, dict(ts=b['ts'], close=b['close']), '2024-09-29', '2026-10-01'))
rows = []; allT = []
for name, fr, btc, a, c in sets:
    T = A.run(fr, btc, fee=0.0015, t0=ms(a), t1=ms(c), wallet=990, short=True); T = T[T.why != '结束']; allT.append(T)
    for why, g in T.groupby('why'):
        rows.append(dict(数据=name, 出场=why, 次数=len(g), 占比=f'{len(g) / len(T) * 100:.1f}%', 平均=f'{g.r.mean() * 100:+.2f}%', 合计U=f'{g.r.sum() * 100:+.0f}'))
    rows.append(dict(数据=name, 出场='全部', 次数=len(T), 占比='100%', 平均=f'{T.r.mean() * 100:+.2f}%', 合计U=f'{T.r.sum() * 100:+.0f}'))
T = pd.concat(allT); sig = T[T.why == '信号']
print('反向信号出场：赚', (sig.r > 0).sum(), '亏', (sig.r <= 0).sum(), '亏的平均', f'{sig.r[sig.r <= 0].mean() * 100:.2f}%', '亏超过10%的', (sig.r < -.10).sum())
pd.set_option('display.width', 200); print(pd.DataFrame(rows).to_string(index=False))
