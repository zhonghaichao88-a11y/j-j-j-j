"""先校准：同样 102 个币的币安数据，5 分钟合成 1 小时，看 1 小时版和 5 分钟版结果差多少"""
import sys; sys.path.insert(0, '/home/user/ext/long/lab'); import data, pandas as pd, momo1h as M
rows = []
for c in data.coins():
    k = pd.read_parquet(f'/home/user/ext/long/k/{c}.parquet').sort_values('ts').drop_duplicates('ts')
    g = k.ts // 3_600_000
    h = pd.DataFrame({'ts': k.ts.groupby(g).first().values // 3_600_000 * 3_600_000, 'o': k.open.groupby(g).first().values, 'l': k.low.groupby(g).min().values,
                      'c': k.close.groupby(g).last().values, 'qv': k.quote_volume.groupby(g).sum().values})
    rows += [(c, t, r) for t, r in M.run(h)]
M.summary('币安 102 币 1小时版（5分钟版不算资金费是 PF 1.16）', rows)
