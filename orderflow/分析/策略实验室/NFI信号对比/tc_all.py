"""NFI 头部币抄跌（141~145）放开到所有币：头部币 vs 非头部币分开看，三段数据，4 种出场，程序规则组合回测。"""
import os, sys, glob, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model import no_overlap, port, pf, EXN, SEGS
TOP = set(open('/home/user/ext/nfisig/top.txt').read().split())
TAGS = set(os.environ.get('TAGS', '141 142 143 144 145').split())
parts = []
for seg, d in SEGS.items():
    for f in glob.glob(f'/home/user/ext/nfisig/{d}/f/*.parquet'):
        x = pd.read_parquet(f, columns=['date', 'tag'] + [f'{a}L{k}' for a in 'yd' for k in EXN])
        x = x[x.tag.fillna('').str.split().apply(lambda t: bool(TAGS & set(t)))]
        x['coin'] = os.path.basename(f)[:-8]; x['seg'] = seg; parts.append(x)
D = pd.concat(parts, ignore_index=True)
D['t'] = pd.to_datetime(D.date, utc=True).dt.tz_localize(None).values.astype('datetime64[ms]').astype(np.int64)
D['头部'] = D.coin.isin(TOP)
days = {'2022-23': 756, '2024-25': 359, '2025-26': 359}
for k in EXN:
    print(f'\n== {EXN[k]}')
    for seg in SEGS:
        for nm, X in (('头部币', D[(D.seg == seg) & D.头部]), ('非头部', D[(D.seg == seg) & ~D.头部]), ('全部', D[D.seg == seg])):
            X = X[X[f'yL{k}'].notna()]
            if len(X) < 5: print(f'  {seg} {nm}: 太少'); continue
            X = no_overlap(X, f'dL{k}'); fin, dd = port(X, f'yL{k}', f'dL{k}')
            print(f'  {seg} {nm} {X.coin.nunique()}币: {len(X)}笔 每天{len(X) / days[seg]:.2f} 胜{(X[f"yL{k}"] > 0).mean():.0%} PF{pf(X[f"yL{k}"]):.2f} 平均{X[f"yL{k}"].mean() * 100:+.2f}% | 组合 100→{fin} 回撤{dd}%')
