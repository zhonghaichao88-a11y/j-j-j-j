"""超级趋势（多空+BTC过滤）严格口径（无前视）重测：成本 0 / 0.05% / 0.15%，外加不限仓位。"""
import types, numpy as np, pandas as pd
import adx_bt as A, st_bt as S
V = types.ModuleType('V'); src = open('st_verify.py', encoding='utf-8').read(); exec(src[:src.rindex('rows = []')], V.__dict__)
import glob, os
def npz(p): z = np.load(p); return {k: z[k] for k in ('ts', 'open', 'high', 'low', 'close')}
fr = {os.path.basename(p)[:-4]: npz(p) for p in glob.glob('/home/user/okx_data/bntop/*.npz')}
b = V.M.daily(fr['BTCUSDT'])
sets = V.sets + [('币安前100', {k: c for k, c in ((k, V.clean(v)) for k, v in fr.items()) if len(c['ts']) > 300}, b, (('2.33年', '2024-06-01', '2026-10-01'),))]
A.STRICT[0] = True
for name, f, btc, per in sets:
    for lab, a, c in per:
        res = []
        for fee in (0.0, 0.0005, 0.0015):
            T = S.run(f, btc, fee=fee, t0=V.ms(a), t1=V.ms(c), wallet=990); T = T[T.why != '结束']; w = T.r > 0
            res.append(f"成本{fee*100:.2f}%: PF {T.r[w].sum()/-T.r[~w].sum():.2f} 胜率{w.mean()*100:.0f}% {len(T)}笔")
        T = S.run(f, btc, fee=0.0015, t0=V.ms(a), t1=V.ms(c), wallet=10**9, max_open=10**6); T = T[T.why != '结束']; w = T.r > 0
        res.append(f"不限仓位0.15%: PF {T.r[w].sum()/-T.r[~w].sum():.2f}")
        print(name, lab, ' | '.join(res), flush=True)
print('DONE')
