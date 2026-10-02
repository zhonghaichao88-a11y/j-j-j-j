"""长数据（2024-01 ~ 2026-03）每个币 5 分钟一条：合约 / 现货主动买卖、持仓量、资金费率、多空比、Coinbase 溢价、BTC 带动、大背景。
只用当时及以前的数据算特征；未来收益单独一列。"""
import pandas as pd, numpy as np, os, glob

FUT = dict(l.split() for l in open('/home/user/ext/long/syms.txt'))


def rd(p):
    return pd.read_parquet(p) if os.path.exists(p) else None


def flow(d, n):
    b, q = d['taker_buy_quote_volume'], d['quote_volume']
    return (2 * b - q).rolling(n).sum() / q.rolling(n).sum()


def one(c, btc=None, cbp=None):
    k = rd(f'k/{c}.parquet')
    if k is None:
        return None
    k = k.sort_values('ts').drop_duplicates('ts').set_index('ts')
    k = k[~k.index.duplicated()]
    F = pd.DataFrame(index=k.index)
    c_ = k.close
    F['inst'] = c
    for n, s in (('r_15', 3), ('r_60', 12), ('r_240', 48), ('r_1d', 288)):
        F[n] = c_.pct_change(s)
    F['pf_60'], F['pf_240'] = flow(k, 12), flow(k, 48)               # 合约主动买卖
    F['vol_surge'] = k.quote_volume.rolling(12).sum() / k.quote_volume.rolling(288 * 7).mean() / 12
    # 现货
    s = rd(f'spot/{c}.parquet')
    if s is not None:
        s = s.sort_values('ts').drop_duplicates('ts').set_index('ts').reindex(k.index)
        F['sf_60'], F['sf_240'] = flow(s, 12), flow(s, 48)
        F['spot_share'] = s.quote_volume.rolling(48).sum() / (s.quote_volume.rolling(48).sum() + k.quote_volume.rolling(48).sum())
        F['spot_share_chg'] = F.spot_share - F.spot_share.rolling(288 * 7).mean()
        F['div_60'] = F.sf_60 - F.pf_60                                 # 现货比合约更想买 → 正
        F['div_240'] = F.sf_240 - F.pf_240
    # 持仓量 / 多空比
    m = rd(f'met/{c}.parquet')
    if m is not None:
        m['ts'] = (pd.to_datetime(m.create_time) - pd.Timestamp('1970-01-01')) // pd.Timedelta(milliseconds=1)
        m = m.sort_values('ts').drop_duplicates('ts').set_index('ts')
        oi = m.sum_open_interest_value.where(m.sum_open_interest_value > 0).reindex(k.index, method='ffill', tolerance=600_000)
        F['oi_60'], F['oi_240'] = oi.pct_change(12), oi.pct_change(48)
        F['oi_z'] = F.oi_60 / F.oi_60.rolling(288 * 7, min_periods=500).std()
        F['ls'] = m.count_long_short_ratio.reindex(k.index, method='ffill', tolerance=600_000)
        F['top_ls'] = m.sum_toptrader_long_short_ratio.reindex(k.index, method='ffill', tolerance=600_000)
        F['ls_z'] = (F.ls - F.ls.rolling(288 * 7, min_periods=500).mean()) / F.ls.rolling(288 * 7, min_periods=500).std()
    fr = rd(f'met/{c}_funding.parquet')
    if fr is not None:
        fr = fr.sort_values('calc_time').drop_duplicates('calc_time').set_index('calc_time').last_funding_rate
        F['funding'] = fr.reindex(k.index, method='ffill')
        F['fund_z'] = (F.funding - F.funding.rolling(288 * 30, min_periods=2000).mean()) / F.funding.rolling(288 * 30, min_periods=2000).std()
    # 大背景：日线、4 小时趋势
    F['ema_4h'] = c_ / c_.ewm(span=48 * 20).mean() - 1                   # 相对 20 根 4 小时均线
    F['ema_1d'] = c_ / c_.ewm(span=288 * 20).mean() - 1                  # 相对 20 日均线
    # 前一天 POC（用 5 分钟收盘价近似成交量分布），以及"没被碰过的 POC"距离
    day = k.index // 86_400_000
    g = pd.DataFrame({'d': day, 'c': c_.round(-int(np.floor(np.log10(c_.median() * 0.002)))), 'v': k.quote_volume})
    poc = g.groupby(['d', 'c']).v.sum().reset_index().sort_values('v').groupby('d').tail(1).set_index('d').c
    F['prev_poc'] = pd.Series(day - 1, index=k.index).map(poc)          # 只用前一天（隔天缺数据就不算）
    F['poc_dist'] = c_ / F.prev_poc - 1
    # BTC 带动
    if btc is not None and c != 'BTC':
        for col in ('r_60', 'pf_60', 'sf_60', 'ema_4h'):
            if col in btc:
                F['btc_' + col] = btc[col].reindex(k.index)
    # Coinbase 溢价
    if cbp is not None:
        F['cb_prem'] = cbp.reindex(k.index)
        F['cb_prem_chg'] = F.cb_prem - F.cb_prem.rolling(48).mean()
    for h, n in ((60, 12), (240, 48)):
        F[f'fwd_{h}'] = c_.shift(-n) / c_ - 1
    # 未来 4 小时最高 / 最低（算止损要用）
    F['fwd_lo_240'] = k.low[::-1].rolling(48).min()[::-1].shift(-1) / c_ - 1
    F['fwd_hi_240'] = k.high[::-1].rolling(48).max()[::-1].shift(-1) / c_ - 1
    F['q'] = pd.to_datetime(F.index, unit='ms').to_period('Q').astype(str)
    return F


if __name__ == '__main__':
    os.chdir('/home/user/ext/long')
    # Coinbase 溢价：Coinbase BTC-USD / 币安现货 BTCUSDT
    cbp = None
    cb, sp = rd('cb/BTC.parquet'), rd('spot/BTC.parquet')
    if cb is not None and sp is not None:
        cbc = cb.set_index('ts').close
        spc = sp.sort_values('ts').drop_duplicates('ts').set_index('ts').close
        cbp = (cbc / spc.reindex(cbc.index) - 1).rolling(12, min_periods=6).mean()
    btc = one('BTC', None, cbp)
    KEEP = ['inst', 'r_60', 'oi_60', 'sf_60', 'pf_60', 'fwd_60', 'fwd_240', 'q']
    os.makedirs('F2', exist_ok=True)

    def save(f, c):                                    # 每个币单独存，只留两个打法要用的列（110 个币一起放内存会爆）
        f = f.replace([np.inf, -np.inf], np.nan)
        out = f[[k for k in KEEP if k in f]].copy()
        for k in out.columns:
            if out[k].dtype == 'float64':
                out[k] = out[k].astype('float32')
        out.index.name = 'ts'
        out.reset_index().to_parquet(f'F2/{c}.parquet')
    save(btc, 'BTC')
    n = 1
    for c in FUT:
        if c == 'BTC':
            continue
        f = one(c, btc, cbp)
        if f is not None and len(f) > 288 * 60:
            save(f, c)
            n += 1
            print(c, len(f), flush=True)
    print('saved', n)
