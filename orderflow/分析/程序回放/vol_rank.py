"""每天的 24 小时成交额排名（币安 U 本位全部 USDT 合约，日K线），用来模拟程序"按成交额选前 N 个币"：
清洗接盘只算开仓那天排在前 N 名的币，N = 50 / 100 / 150 / 200 / 全部。"""
import os, sys, io, re, zipfile, httpx, pandas as pd, numpy as np
from concurrent.futures import ThreadPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
OUT = '/home/user/ext/more/daily_qv.parquet'
if not os.path.exists(OUT):
    import more_flush as MF
    syms = [p.rstrip('/').split('/')[-1] for p in MF.listing('data/futures/um/monthly/klines/')]
    syms = [s for s in syms if s.endswith('USDT')]
    print('合约', len(syms), flush=True)
    def one(s):
        rs = [MF.get(f'{MF.DV}/data/futures/um/monthly/klines/{s}/1d/{s}-1d-{m}.zip') for m in MF.MONTHS]
        parts = [MF.unzip_csv(r, MF.KC) for r in rs if r]
        if not parts: return None
        d = pd.concat(parts)[['ts', 'quote_volume']]; d['coin'] = MF.base_of(s)[1]; return d
    with ThreadPoolExecutor(16) as ex:
        R = [x for x in ex.map(one, syms) if x is not None]
    pd.concat(R).to_parquet(OUT)
Q = pd.read_parquet(OUT); Q['day'] = (Q.ts // 86_400_000 * 86_400_000).astype(np.int64)
Q = Q.groupby(['day', 'coin']).quote_volume.sum().reset_index()
Q['rank'] = Q.groupby('day').quote_volume.rank(ascending=False)
sys.argv = ['x']
import compare as C
P, _ = C.program('flush'); P = P[P.t_in >= C.ms('2021-12-01')].copy()
P['day'] = (P.t_in // 86_400_000 * 86_400_000 - 86_400_000).astype(np.int64)        # 用前一天的成交额排名（程序选币时只知道过去）
P = P.merge(Q[['day', 'coin', 'rank']], on=['day', 'coin'], how='left')
out = [f'清洗接盘程序回放 {P.coin.nunique()} 个币 {len(P)} 笔；有排名的 {P["rank"].notna().sum()} 笔', '']
for n in (30, 50, 100, 150, 200, 300, None):
    x = P if n is None else P[P['rank'] <= n]
    out.append(f'【前一天成交额排名 {"全部" if n is None else "前 " + str(n)}】{x.coin.nunique()} 个币：' + C.stats(x))
    for nm, a, b in C.SEG:
        y = x[(x.t_in >= C.ms(a)) & (x.t_in < C.ms(b))]
        if len(y): out.append(f'   {nm}: ' + C.stats(y))
    for yy, g in x.groupby(pd.to_datetime(x.t_in, unit='ms').dt.year): out.append(f'     {yy}: ' + C.stats(g))
txt = '\n'.join(out); print(txt); open(os.path.join(HERE, '清洗接盘_按成交额排名.md'), 'w').write('```\n' + txt + '\n```\n')
