"""指标分 5 档（每个币自己过去 30 天里的分位，不偷看）→ 之后 4h / 24h 的"超额涨跌"（减去同一小时所有币的平均，去掉大盘涨跌）。
分 2024 和 2025 以后两段，看方向是不是两段都一样。单位：基点（0.01%）。来回一次手续费+滑点约 14 基点。"""
import numpy as np, pandas as pd, json
F = pd.read_parquet('/home/user/ext/of_ind.parquet')
F = F[np.isfinite(F.fwd24)]
for k in ('fwd4', 'fwd24'):
    F[k + '_x'] = F[k] - F.groupby('t')[k].transform('mean')
F['per'] = np.where(F.t < 1735689600000, '2024', '2025+')
IND = ['perp_flow', 'spot_flow', 'spot_minus_perp', 'perp_flow_24h', 'oi_1h', 'oi_24h', 'funding', 'retail_ls_z', 'top_ls_z', 'premium_z', 'vol_x']
out = {}
for ind in IND:
    s = F[[ind, 'coin', 'fwd4_x', 'fwd24_x', 'per']].dropna()
    s['pct'] = s.groupby('coin')[ind].transform(lambda x: x.rolling(720, min_periods=200).rank(pct=True))
    s = s.dropna(subset=['pct'])
    s['q'] = np.minimum((s.pct * 5).astype(int), 4) + 1
    tab = s.groupby(['per', 'q'])[['fwd4_x', 'fwd24_x']].mean().mul(1e4).round(1)
    out[ind] = tab.reset_index().to_dict('records')
    print('==', ind); print(tab.unstack(0).to_string())
# 四象限：24 小时价格 × 24 小时持仓量
q = F[['ret1h', 'oi_24h', 'fwd24', 'fwd24_x', 'per', 'coin', 't']].copy()
g = F.groupby('coin')
q['p24'] = F.groupby('coin')['ret1h'].transform(lambda r: (1 + r).rolling(24).apply(np.prod, raw=True) - 1)
q = q.dropna()
q['quad'] = np.select([(q.p24 > .03) & (q.oi_24h > .05), (q.p24 > .03) & (q.oi_24h < -.05), (q.p24 < -.03) & (q.oi_24h > .05), (q.p24 < -.03) & (q.oi_24h < -.05)],
                      ['涨+持仓增', '涨+持仓减', '跌+持仓增', '跌+持仓减'], '其它')
qt = q.groupby(['quad', 'per']).agg(n=('fwd24', 'size'), fwd24=('fwd24', 'mean'), fwd24_x=('fwd24_x', 'mean'))
qt[['fwd24', 'fwd24_x']] *= 1e4
print(qt.round(1).to_string())
out['quad'] = qt.round(1).reset_index().to_dict('records')
json.dump(out, open('结果.json', 'w'), ensure_ascii=False, indent=1)
