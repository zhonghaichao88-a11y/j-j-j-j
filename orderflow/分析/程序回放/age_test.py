"""清洗接盘：按"币上线多久"过滤（上线时间 = 币安合约日K线第一天），配合"前一天成交额排名前 N"（模拟程序按成交额选币）。"""
import sys, os, numpy as np, pandas as pd; sys.argv = ['x']
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compare as C
Q = pd.read_parquet('/home/user/ext/more/daily_qv.parquet'); Q['day'] = (Q.ts // 86400000 * 86400000).astype(np.int64)
Q = Q.groupby(['day', 'coin']).quote_volume.sum().reset_index(); Q['rank'] = Q.groupby('day').quote_volume.rank(ascending=False)
first = Q.groupby('coin').day.min()
P, _ = C.program('flush'); P = P[P.t_in >= C.ms('2021-12-01')].copy()
P['day'] = (P.t_in // 86400000 * 86400000 - 86400000).astype(np.int64); P = P.merge(Q[['day', 'coin', 'rank']], on=['day', 'coin'], how='left')
P['age'] = (P.t_in - P.coin.map(first)) / 86_400_000
out = []
for n in (150, None):
    for a in (0, 90, 180, 365, 730):
        x = P[(P.age >= a) & ((P['rank'] <= n) if n else True)]
        out.append(f'【{"成交额前 150" if n else "不限排名"}，上线满 {a} 天】{x.coin.nunique()} 币 ' + C.stats(x))
        out.append('    三段：' + ' | '.join(C.stats(x[(x.t_in >= C.ms(s)) & (x.t_in < C.ms(e))]).split(' 每笔')[0] for _, s, e in C.SEG))
        out.append('    分年：' + ' | '.join(f'{y}:' + C.stats(g).split(' 每笔')[0].split('笔 ')[1] for y, g in x.groupby(pd.to_datetime(x.t_in, unit='ms').dt.year)))
txt = '\n'.join(out); print(txt); open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '清洗接盘_上线时间.md'), 'w').write('```\n' + txt + '\n```\n')
