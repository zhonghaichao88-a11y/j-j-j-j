"""给每笔成交配上"当时的环境"：大盘牛熊（BTC 日线收盘在 200 天均线上方 = 牛，前 100 天数据不够时用已有天数）、
币的成交额排名（币安永续 30 天平均成交额，在当时所有有数据的币里排第几）、
订单流（币安：持仓量 24 小时变化、最近一次资金费率、散户多空比 7 天 z 分数）。都只用当时已经知道的数据（前一小时收盘为止）。
输出：/home/user/ext/nfisig/attrs_hour.parquet（coin, hour, rank30, oi24, fund, lsz）和 btc_regime.parquet（day, bull）"""
import os, glob, numpy as np, pandas as pd
OUT = '/home/user/ext/nfisig'
# ---- 大盘：BTC 日线 vs 200 天均线（用前一天收盘，避免偷看）
z = np.load('/home/user/ext/oos/bn/BTCUSDT_bnold1h.npz'); a = pd.DataFrame({k: z[k] for k in z.files})
b = pd.read_parquet('/home/user/ext/long/k/BTC.parquet')[['ts', 'close']]
h = pd.concat([a[['ts', 'close']], b]).drop_duplicates('ts').sort_values('ts')
d = h.assign(day=pd.to_datetime(h.ts, unit='ms').dt.floor('D')).groupby('day').close.last()
ma = d.rolling(200, min_periods=100).mean()
bull = (d > ma).shift(1)                                     # 今天用昨天收盘的判断
pd.DataFrame({'day': bull.index, 'bull': bull.values}).dropna().to_parquet(f'{OUT}/btc_regime.parquet')
print('牛市天数占比', round(bull.mean(), 2), bull.index.min(), bull.index.max())
# ---- 每个币每小时：成交额排名、持仓量 24h 变化、资金费率、散户多空比 z
rows = []
for root in ('/home/user/ext/oos/f5', '/home/user/ext/long'):
    for f in sorted(glob.glob(f'{root}/k/*.parquet')):
        c = os.path.basename(f)[:-8]
        k = pd.read_parquet(f, columns=['ts', 'quote_volume']).dropna()
        k['hour'] = (k.ts // 3_600_000 * 3_600_000).astype(np.int64)
        hv = k.groupby('hour').quote_volume.sum()
        x = pd.DataFrame({'qv30': hv.rolling(24 * 30, min_periods=24 * 7).mean().shift(1)})
        mf = f'{root}/met/{c}.parquet'
        if os.path.exists(mf):
            m = pd.read_parquet(mf, columns=['create_time', 'sum_open_interest_value', 'count_long_short_ratio'])
            m['hour'] = (pd.to_datetime(m.create_time).values.astype('datetime64[ms]').astype(np.int64) // 3_600_000 * 3_600_000)
            mh = m.groupby('hour').last()
            oi = mh.sum_open_interest_value.astype(float); ls = mh.count_long_short_ratio.astype(float)
            x = x.join(pd.DataFrame({'oi24': (oi / oi.shift(24) - 1).shift(1),
                                     'lsz': ((ls - ls.rolling(168, min_periods=48).mean()) / ls.rolling(168, min_periods=48).std()).shift(1)}), how='left')
        ff = f'{root}/met/{c}_funding.parquet'
        if os.path.exists(ff):
            fu = pd.read_parquet(ff); fu['hour'] = (fu.calc_time.astype(np.int64) // 3_600_000 * 3_600_000)
            fu = fu.groupby('hour').last_funding_rate.last().astype(float)
            x = x.join(fu.rename('fund'), how='outer'); x['fund'] = x.fund.ffill()
        x['coin'] = c; x.index.name = 'hour'; rows.append(x.reset_index())
A = pd.concat(rows, ignore_index=True).drop_duplicates(['coin', 'hour'], keep='last')
A['rank30'] = A.groupby('hour').qv30.rank(ascending=False, method='min')
A = A[['coin', 'hour', 'rank30', 'oi24', 'fund', 'lsz']]
for col in ('rank30', 'oi24', 'fund', 'lsz'): A[col] = A[col].astype(np.float32)
A.to_parquet(f'{OUT}/attrs_hour.parquet', compression='zstd')
print(A.shape, A.coin.nunique(), A.describe().round(3).to_string())
