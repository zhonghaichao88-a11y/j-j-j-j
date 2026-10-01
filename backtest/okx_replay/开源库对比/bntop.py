import glob, os, numpy as np, pandas as pd, types
import adx_bt as A
M = types.ModuleType('M'); _src = open('adx_more.py', encoding='utf-8').read(); exec(_src[:_src.index('out = []')], M.__dict__)
def npz(p): z = np.load(p); return {k: z[k] for k in ('ts', 'open', 'high', 'low', 'close')}
fr = {os.path.basename(p)[:-4]: npz(p) for p in glob.glob('/home/user/okx_data/bntop/*.npz')}
btc = M.daily(fr['BTCUSDT']); print('币数', len(fr))
ms = lambda x: int(pd.Timestamp(x, tz='UTC').timestamp() * 1000)
for lab, a, c in (('2024-06~12', '2024-06-01', '2025-01-01'), ('2025年', '2025-01-01', '2026-01-01'), ('2026-01~09', '2026-01-01', '2026-10-01'), ('全部2.33年', '2024-06-01', '2026-10-01')):
    T = A.run(fr, btc, fee=0.001, t0=ms(a), t1=ms(c), wallet=990, short=True); T = T[T.why != '结束']
    w = T.r > 0; g = T.groupby('why').r.agg(['size', 'mean'])
    print(lab, dict(笔数=len(T), 胜率=f'{w.mean()*100:.1f}%', PF=round(T.r[w].sum() / -T.r[~w].sum(), 3), 每笔=f'{T.r.mean()*100:+.3f}%',
          出场={k: f"{int(v['size'])}笔 {v['mean']*100:+.2f}%" for k, v in g.iterrows()}), flush=True)
