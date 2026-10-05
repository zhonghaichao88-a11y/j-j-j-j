"""NFI 进场条件两两组合（两个条件同一根K线都触发才进）× 大盘过滤（不过滤 / BTC 24h 涨 / BTC 24h 跌 / BTC 4h RSI>50 / <50），做多做空分开。
用特征数据（F_*：NFI 条件触发的每根K线 + 每个整点）。所有币。4 种出场。
过关同前：三段每段 ≥30 笔（同币不重叠）、PF ≥ 1.3、胜率 ≥ 60%、组合赚钱、前后两半 PF ≥ 1。"""
import os, sys, glob, itertools, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model import no_overlap, port, pf, EXN, SEGS
HERE = os.path.dirname(os.path.abspath(__file__)); DAYS = {'2022-23': 756, '2024-25': 359, '2025-26': 359}
P = []
for seg, d in SEGS.items():
    for f in glob.glob(f'/home/user/ext/nfisig/{d}/f/*.parquet'):
        x = pd.read_parquet(f, columns=['date', 'tag', 'ret_24h'] + [f'{a}{s}{k}' for a in 'yd' for s in 'LS' for k in EXN])
        x['coin'] = os.path.basename(f)[:-8]; x['seg'] = seg; P.append(x)
D = pd.concat(P, ignore_index=True)
D['t'] = pd.to_datetime(D.date, utc=True).dt.tz_localize(None).values.astype('datetime64[ms]').astype(np.int64)
btc = D[D.coin == 'BTC'].drop_duplicates(['seg', 't']).set_index(['seg', 't']).ret_24h.rename('btc24')
D = D.join(btc, on=['seg', 't']); D['btc24'] = D.groupby('seg').btc24.transform(lambda s: s.ffill())
D['tags'] = D.tag.fillna('').str.split().apply(lambda t: frozenset(x for x in t if x not in ('121', '603')))
REG = {'不过滤': np.ones(len(D), bool), 'BTC24h涨': (D.btc24 > 0).values, 'BTC24h跌': (D.btc24 < 0).values}
cnt = D.tags.explode().value_counts()
conds = [c for c in cnt.index if isinstance(c, str) and cnt[c] >= 60]
print('样本', len(D), '条件', len(conds), flush=True)
M = {c: D.tags.apply(lambda s, c=c: c in s).values for c in conds}
combos = [(a,) for a in conds] + [(a, b) for a, b in itertools.combinations(conds, 2) if (int(a) < 500) == (int(b) < 500)]
out = []
for cb in combos:
    m = np.logical_and.reduce([M[c] for c in cb])
    if m.sum() < 90: continue
    side = 'L' if int(cb[0]) < 500 else 'S'
    for rn, rm in REG.items():
        mm = m & rm
        if mm.sum() < 90: continue
        X0 = D[mm]
        for k in EXN:
            y, dc = f'y{side}{k}', f'd{side}{k}'; rs = []; ok = True
            for s in SEGS:
                X = X0[(X0.seg == s) & X0[y].notna()]
                if len(X) < 30: ok = False; rs.append(None); continue
                X = no_overlap(X, dc); Xs = X.sort_values('t'); h = len(Xs) // 2
                fin, dd = port(X, y, dc)
                r = dict(笔=len(X), 每天=len(X) / DAYS[s], 胜=(X[y] > 0).mean(), PF=pf(X[y]), 均=X[y].mean() * 100,
                         前=pf(Xs[y][:h]), 后=pf(Xs[y][h:]), 组合=fin, 撤=dd)
                rs.append(r); ok &= r['笔'] >= 30 and r['PF'] >= 1.3 and r['胜'] >= 0.6 and fin > 100 and r['前'] >= 1 and r['后'] >= 1
            g = [r for r in rs if r]
            out.append(dict(方向='做多' if side == 'L' else '做空', 条件='+'.join(cb), 大盘=rn, 出场=EXN[k], 过关=ok,
                            每天=round(np.mean([r['每天'] for r in g]), 2) if g else 0, 最差PF=round(min((r['PF'] for r in g), default=0), 2),
                            **{s: (f"{r['笔']}笔 胜{r['胜']:.0%} PF{r['PF']:.2f} 均{r['均']:+.2f}% 组合{r['组合']} 撤{r['撤']}%" if r else '不足30笔') for s, r in zip(SEGS, rs)}))
    if len(out) % 500 < 12: print(len(out), flush=True)
S = pd.DataFrame(out).sort_values(['过关', '每天'], ascending=False); S.to_csv(HERE + '/pairs_结果.csv', index=False)
pd.set_option('display.width', 400); pd.set_option('display.max_colwidth', 80)
for sd in ('做多', '做空'):
    s = S[(S.方向 == sd) & S.过关]
    print(f'\n===== {sd} 过关 {len(s)} 个（按每天单数排）'); print(s.head(30).to_string(index=False))
