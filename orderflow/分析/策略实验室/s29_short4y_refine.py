"""第八轮：在第七轮最稳的那个上加条件（看结果前写好）。
基础：多头摊平（24 小时跌超 5%、持仓量 24 小时涨超 5%、资金费率 > 0）→ 做空；看到多头被清洗就平（1 小时跌超 fr 且持仓量 1 小时降超 fo），最多 48 小时；止损 5%。
加一个条件 c：none / low 价格在 24 小时低点 2% 以内 / mls 全市场散户偏多 / tls 这个币大户偏空 / ls 这个币散户偏多 / fund2 资金费率 > 0.01%（多头付得更多）
清洗平仓门槛 (fr, fo)：(2%, 3%) / (1.5%, 2%) / (3%, 5%)
其他和第七轮一样：5 段、过关标准、按 5 段中位数排。"""
import json, numpy as np, pandas as pd
import data, report
import s28_short4y as B
GRID = [{'c': c, 'fr': fr, 'fo': fo} for c in ('none', 'low', 'mls', 'tls', 'ls', 'fund2') for fr, fo in ((0.02, 0.03), (0.015, 0.02), (0.03, 0.05))]


def market_table(root, coins):
    if root.endswith('long'):
        F = pd.read_parquet('/home/user/ext/of_ind.parquet', columns=['t', 'retail_ls_z'])
        M = F.groupby('t').median()
    else:
        data.ROOT = root; hs = []
        for c in coins:
            df = data.load(c); g = df.groupby(df.index // 3_600_000); ls = g.ls.last()
            hs.append(pd.DataFrame({'t': ls.index * 3_600_000, 'retail_ls_z': ((ls - ls.rolling(168).mean()) / ls.rolling(168).std()).values}))
        M = pd.concat(hs).groupby('t').median()
    M.index = M.index + 3_600_000
    return M.retail_ls_z


def run(df, p, coin):
    m = (df.r24h < -0.05) & (df.oi24h > 0.05) & (df.fund > 0)
    c = p['c']
    if c == 'low': m &= df.c <= df.l.rolling(288).min() * 1.02
    elif c == 'mls': m &= df.mls > 0
    elif c == 'tls': m &= df.tls_z < 0
    elif c == 'ls': m &= df.ls_z > 0
    elif c == 'fund2': m &= df.fund > 0.0001
    q = dict(e='E1', o=0.05, x='X2', sd=0.05)
    saved = df['r60'], df['oi60']
    # 清洗平仓门槛：把 B.run 里固定的 -2% / -3% 换成这组
    df2 = df.assign(r60=df.r60 * 0.02 / p['fr'], oi60=df.oi60 * 0.03 / p['fo'])
    return B.run(df2, m.fillna(False).values, q, coin)


if __name__ == '__main__':
    z = lambda s: (s - s.rolling(288 * 7, min_periods=288).mean()) / s.rolling(288 * 7, min_periods=288).std()
    old = open('/home/user/ext/oos/flush_old_coins.txt').read().split(); new = data.coins()
    res = {k: [] for k in range(len(GRID))}
    for root, coins in (('/home/user/ext/oos/f5', old), ('/home/user/ext/long', new)):
        M = market_table(root, coins)
        for c, df in B.load_set(root, coins):
            key = ((df.index.values + 300_000) // 3_600_000) * 3_600_000
            df['mls'] = M.reindex(key).values
            df['ls_z'], df['tls_z'] = z(df.ls), z(df.tls)
            for k, p in enumerate(GRID):
                res[k] += run(df, p, c)
            print(c, flush=True)
    pf = report.pf; rows = []
    for k, p in enumerate(GRID):
        T = pd.DataFrame(res[k], columns=['coin', 't', 'ret', 'why', 'bars'])
        segs = {}
        for nm, a, b in B.SEG:
            X = T[(T.t >= pd.Timestamp(a).value // 10**6) & (T.t < pd.Timestamp(b).value // 10**6)]
            segs[nm] = (len(X), round(pf(X.ret), 2) if len(X) >= 10 else None)
        good = [v[1] for v in segs.values() if v[1] is not None]
        cs = T.groupby('coin').ret.sum().sort_values(ascending=False)
        rows.append({'参数': json.dumps(p), '笔数': len(T), 'PF': round(pf(T.ret), 2), '每笔基点': round(T.ret.mean() * 1e4, 1),
                     '去前5币': round(pf(T[~T.coin.isin(cs.index[:5])].ret), 2), '分段中位': round(float(np.median(good)), 2), '不亏段数': f'{sum(v >= 1 for v in good)}/{len(good)}',
                     **{nm: f'{v[0]}/{v[1]}' for nm, v in segs.items()}})
        T.to_parquet(f'results/s29_{k}.parquet')
    D = pd.DataFrame(rows).sort_values('分段中位', ascending=False)
    open('results/s29_short4y_refine.md', 'w').write(__doc__ + '\n\n' + D.to_markdown(index=False))
    print(D.to_string(index=False))
