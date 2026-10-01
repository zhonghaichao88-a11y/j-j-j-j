import glob, os, json, numpy as np, pandas as pd, types
import adx_bt as A
M = types.ModuleType('M'); _src = open('adx_more.py', encoding='utf-8').read(); exec(_src[:_src.index('out = []')], M.__dict__)
def npz(p): z = np.load(p); return {k: z[k] for k in ('ts', 'open', 'high', 'low', 'close')}
ms = lambda x: int(pd.Timestamp(x, tz='UTC').timestamp() * 1000)
fr = {os.path.basename(p)[:-4]: npz(p) for p in glob.glob('/home/user/okx_data/bntop/*.npz')}
btc = M.daily(fr['BTCUSDT'])
def rep(name, T):
    T = T[T.why != '结束']; w = T.r > 0
    print(f"{name:30s} 笔数 {len(T):6d} 胜率 {w.mean()*100:.1f}% PF {T.r[w].sum()/-T.r[~w].sum():.2f} 每笔 {T.r.mean()*100:+.3f}% 合计 {T.r.sum()*100:+.0f}U", flush=True)
for strict in (False, True):
    A.STRICT[0] = strict
    rep(f"币安前100 {'严格(无前视)' if strict else '原来'}", A.run(fr, btc, fee=0.001, t0=ms('2024-06-01'), t1=ms('2026-10-01'), wallet=990, short=True))
s = json.load(open('/home/user/okx_data/universe_bnx.json')); fb = {x: M.npz(f'/home/user/okx_data/{x}_bn1h.npz') for x in s if os.path.exists(f'/home/user/okx_data/{x}_bn1h.npz')}
for strict in (False, True):
    A.STRICT[0] = strict
    rep(f"币安独有416 {'严格(无前视)' if strict else '原来'}", A.run(fb, M.btc_bnf(), fee=0.0015, t0=ms('2024-09-29'), t1=ms('2026-09-01'), wallet=990, short=True))
