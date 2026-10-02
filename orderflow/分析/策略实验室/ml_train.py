"""滚动训练（每个季度重新训练一次，只用之前的数据）：
2024 年起步 → 预测 2025Q1；再把 2025Q1 加进训练 → 预测 2025Q2 …… 一直到 2026Q1。考试期全部是模型没见过的数据。
目标：拿 H 小时的收益（按波动标准化）。模型给出的分数排在训练期最高 q% → 做多，最低 q% → 做空。
交易用统一的模拟器（手续费、滑点、资金费率、止损都算）。"""
import sys, json, numpy as np, pandas as pd, lightgbm as lgb
from scipy.stats import spearmanr
import data, sim, feat, report

H = int(sys.argv[1]) if len(sys.argv) > 1 else 12
Q = float(sys.argv[2]) if len(sys.argv) > 2 else 0.01
SIDE = sys.argv[3] if len(sys.argv) > 3 else 'both'
X = pd.read_parquet('/home/user/ext/long/lab/ml_hourly.parquet')
FEATS = ['r1h', 'r4h', 'r24h', 'r3d', 'atr1h', 'pos24', 'vsurge', 'pf1h', 'pf4h', 'pf24h', 'sf1h', 'sf4h', 'div1h',
         'spot_share_chg', 'oi1h', 'oi4h', 'oi24h', 'fund', 'fund_z', 'ls_z', 'tls_z', 'prem_z', 'hour', 'dow',
         'btc_r1h', 'btc_r4h', 'btc_r24h', 'btc_pf1h', 'btc_oi1h', 'rel24h']
ycol = f'y{H}h'
X['yn'] = (X[ycol] / (X.atr1h * np.sqrt(H))).clip(-5, 5)
X = X[X.atr1h > 0]
qs = pd.date_range('2025-01-01', '2026-04-01', freq='QS')
preds = []
imp = None
for a, b in zip(qs[:-1], qs[1:]):
    ta, tb = int(a.value // 10**6), int(b.value // 10**6)
    tr = X[(X.ts < ta - H * 3_600_000) & X.yn.notna()]
    te = X[(X.ts >= ta) & (X.ts < tb)]
    m = lgb.LGBMRegressor(n_estimators=400, learning_rate=0.03, num_leaves=31, min_child_samples=1000, subsample=0.7,
                          subsample_freq=1, colsample_bytree=0.7, reg_lambda=5.0, verbose=-1)
    m.fit(tr[FEATS], tr.yn)
    ptr = m.predict(tr[FEATS])
    hi, lo = np.quantile(ptr, 1 - Q), np.quantile(ptr, Q)
    p = m.predict(te[FEATS])
    ok = te[ycol].notna()
    ic = spearmanr(p[ok.values], te.yn[ok].values).correlation
    print(f'{a.date()} 训练 {len(tr)} 行，考试 {len(te)} 行，排序相关 IC {ic:+.3f}', flush=True)
    t = te[['ts', 'coin', 'row']].copy()
    t['pred'], t['hi'], t['lo'] = p, hi, lo
    preds.append(t)
    g = pd.Series(m.feature_importances_, index=FEATS)
    imp = g if imp is None else imp + g
P = pd.concat(preds)
P['side'] = np.where(P.pred >= P.hi, 1, np.where(P.pred <= P.lo, -1, 0))
if SIDE == 'long':
    P.loc[P.side < 0, 'side'] = 0
if SIDE == 'short':
    P.loc[P.side > 0, 'side'] = 0
S = P[P.side != 0]
rows = []
for c, g in S.groupby('coin'):
    df = feat.add_basic(data.load(c))
    sig = g.row.values.astype(int)
    rows += sim.run(df, sig, g.side.values, feat.vol_stop(df, H * 12)[sig], hold=H * 12, entry='market')
T = pd.DataFrame(rows, columns=sim.COLS)
T['q'] = pd.to_datetime(T.t, unit='ms').dt.to_period('Q').astype(str)
out = {'H': H, 'Q': Q, 'side': SIDE, '考试全部': report.stats(T),
       '多': report.stats(T[T.side > 0]), '空': report.stats(T[T.side < 0]),
       '新币': report.stats(T[T.coin.isin(data.NEW)]), '原46币': report.stats(T[~T.coin.isin(data.NEW)]),
       '各季度每笔基点': {k: round(v * 1e4, 1) for k, v in T.groupby('q').ret.mean().items()},
       '组合': report.portfolio(T),
       '重要特征': (imp.sort_values(ascending=False).head(10) / imp.sum()).round(3).to_dict()}
print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
with open(f'/home/user/ext/long/lab/results/ml_H{H}_Q{Q}_{SIDE}.json', 'w') as f:
    json.dump(out, f, ensure_ascii=False, indent=1, default=str)
P.to_parquet(f'/home/user/ext/long/lab/results/ml_pred_H{H}.parquet')
