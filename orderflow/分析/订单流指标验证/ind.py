"""单个订单流指标 → 之后 4 / 24 小时涨跌（每小时整点看一次，不偷看）。
币安 2024-01 ~ 2026-09，加密币合约。指标按"每个币自己过去 30 天的分位"分 5 档，避免大币小币尺度不同。"""
import sys, numpy as np, pandas as pd
sys.path.insert(0, '/home/user/j-j-j-j/orderflow/分析/策略实验室')
import data as D

def hourly(c):
    df = D.load(c)
    g = df.groupby(df.index // 3_600_000)
    h = pd.DataFrame({'o': g.o.first(), 'c': g.c.last(), 'h': g.h.max(), 'l': g.l.min(), 'qv': g.qv.sum(), 'bq': g.bq.sum(),
                      'sqv': g.sqv.sum(min_count=1), 'sbq': g.sbq.sum(min_count=1), 'sc': g.sc.last(),
                      'oi': g.oi.last(), 'ls': g.ls.last(), 'tls': g.tls.last(), 'fund': g.fund.last()})
    h.index = h.index * 3_600_000
    return h

rows = []
for c in D.coins():
    try:
        h = hourly(c)
    except Exception as e:
        print(c, e); continue
    if len(h) < 2000: continue
    f = pd.DataFrame(index=h.index)
    f['ret1h'] = h.c.pct_change()
    f['perp_flow'] = (2 * h.bq - h.qv) / h.qv
    f['spot_flow'] = (2 * h.sbq - h.sqv) / h.sqv
    f['spot_minus_perp'] = f.spot_flow - f.perp_flow
    f['perp_flow_24h'] = (2 * h.bq - h.qv).rolling(24).sum() / h.qv.rolling(24).sum()
    f['oi_1h'] = h.oi.pct_change()
    f['oi_24h'] = h.oi.pct_change(24)
    f['funding'] = h.fund
    z = lambda s: (s - s.rolling(168).mean()) / s.rolling(168).std()
    f['retail_ls_z'] = z(h.ls)
    f['top_ls_z'] = z(h.tls)
    f['premium_z'] = z(h.c / h.sc - 1)
    f['vol_x'] = h.qv / h.qv.rolling(168).mean()
    f['fwd4'] = h.c.shift(-4) / h.c - 1
    f['fwd24'] = h.c.shift(-24) / h.c - 1
    f['coin'] = c; f['t'] = h.index
    rows.append(f.iloc[200:-24])
    print(c, len(f), flush=True)
F = pd.concat(rows)
F.to_parquet('/home/user/ext/of_ind.parquet')
print(len(F))
