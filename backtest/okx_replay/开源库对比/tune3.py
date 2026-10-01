"""方案三改良（严格口径、无前视）：止损 25/10/5/3%，最长持仓 不限/24h/12h，止盈 1%/2%。
选参数段：更早年份137币 2022、2023；验证段（没参与挑选）：2024前三季、币安独有416两年、币安当前前100(2024-06~2026-09)、欧易新64两年。成本单边0.15%，固定每笔100U，最多10仓。"""
import json, os, types, sys, itertools, numpy as np, pandas as pd
from multiprocessing import Pool
import adx_bt as A
A.STRICT[0] = True
D = '/home/user/okx_data'
M = types.ModuleType('M'); _src = open('adx_more.py', encoding='utf-8').read(); exec(_src[:_src.index('out = []')], M.__dict__)
def npz(p): z = np.load(p); return {k: z[k] for k in ('ts', 'open', 'high', 'low', 'close')}
ms = lambda x: int(pd.Timestamp(x, tz='UTC').timestamp() * 1000)
def load():
    S = {}
    s = json.load(open(f'{D}/universe_bnold.json')); fr = {x: M.npz(f'{D}/{x}_bnold1h.npz') for x in s if os.path.exists(f'{D}/{x}_bnold1h.npz')}
    S['更早137'] = (fr, M.daily(fr['BTCUSDT']))
    s = json.load(open(f'{D}/universe_bnx.json')); S['币安独有416'] = ({x: M.npz(f'{D}/{x}_bn1h.npz') for x in s if os.path.exists(f'{D}/{x}_bn1h.npz')}, M.btc_bnf())
    import glob
    fr = {os.path.basename(p)[:-4]: npz(p) for p in glob.glob(f'{D}/bntop/*.npz')}; S['币安前100'] = (fr, M.daily(fr['BTCUSDT']))
    s = json.load(open(f'{D}/okx64/universe.json')); fr = {x: npz(f'{D}/okx64/{x}_1h.npz') for x in s if os.path.exists(f'{D}/okx64/{x}_1h.npz')}
    b = npz(f'{D}/okx64/BTC_1d.npz'); S['欧易新64'] = ({k: v for k, v in fr.items() if len(v['ts']) > 300}, dict(ts=b['ts'], close=b['close']))
    return S
SEG = [('选', '更早137', '2022年', '2022-01-01', '2023-01-01'), ('选', '更早137', '2023年', '2023-01-01', '2024-01-01'),
       ('验', '更早137', '2024前三季', '2024-01-01', '2024-10-01'), ('验', '币安独有416', '两年', '2024-09-29', '2026-09-01'),
       ('验', '币安前100', '2.33年', '2024-06-01', '2026-10-01'), ('验', '欧易新64', '两年', '2024-09-29', '2026-10-01')]
GRID = list(itertools.product((0.25, 0.10, 0.05, 0.03), (0, 24, 12), (0.01, 0.02)))
DATA = None
def job(args):
    sl, mh, roi = args
    out = []
    for kind, ds, lab, a, c in SEG:
        fr, btc = DATA[ds]
        T = A.run(fr, btc, fee=0.0015, roi=roi, sl=sl, t0=ms(a), t1=ms(c), wallet=990, short=True, max_hold=mh); T = T[T.why != '结束']
        w = T.r > 0; pf = T.r[w].sum() / max(-T.r[~w].sum(), 1e-9)
        out.append(dict(止损=sl, 最长持仓=mh or '不限', 止盈=roi, 段=f'{kind}:{ds}{lab}', 笔数=len(T), 胜率=round(w.mean() * 100, 1), PF=round(pf, 2), 合计U=round(T.r.sum() * 100)))
    return out
if __name__ == '__main__':
    DATA = load()
    rows = []
    with Pool(4) as p:
        for r in p.imap_unordered(job, GRID):
            rows += r; print(r[0]['止损'], r[0]['最长持仓'], r[0]['止盈'], [x['PF'] for x in r], flush=True)
    R = pd.DataFrame(rows); R.to_csv('tune3.csv', index=False); print('DONE')
