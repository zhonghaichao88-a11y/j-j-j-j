import numpy as np, pandas as pd, sim
NAME = 'CME 缺口回补（BTC、ETH）'
RULES = '出处：交易圈常见"CME 缺口必补"。CME 周五 16:00（芝加哥时间）收盘价 vs 周日 17:00 开盘时的价格，缺口超过 g → 往回补方向做，目标=周五收盘价，止损=k 倍缺口，最多拿 days 天。'
GRID = [{'g': g, 'k': k, 'days': 3} for g in (.005, .01, .02) for k in (1, 2)]


def prep(df):
    return df


def trades(df, p):
    if df.attrs['name'] not in ('BTC', 'ETH'):
        return []
    T, C = df.index.values, df.c.values
    sig, side, sd, td = [], [], [], []
    for fri in pd.date_range('2024-01-05', '2026-03-27', freq='W-FRI'):
        fc = pd.Timestamp(f'{fri.date()} 16:00', tz='America/Chicago').tz_convert('UTC')
        so = pd.Timestamp(f'{(fri + pd.Timedelta(days=2)).date()} 17:00', tz='America/Chicago').tz_convert('UTC')
        tf, ts = int(fc.value // 10**6) - 300_000, int(so.value // 10**6) - 300_000
        i_f, i_s = np.searchsorted(T, tf), np.searchsorted(T, ts)
        if i_f >= len(T) or i_s >= len(T) or T[i_f] != tf or T[i_s] != ts:
            continue
        gap = C[i_s] / C[i_f] - 1
        if abs(gap) >= p['g']:
            t_ = abs(C[i_f] / C[i_s] - 1)
            sig.append(i_s); side.append(-1 if gap > 0 else 1); td.append(t_); sd.append(p['k'] * t_)
    return sim.run(df, sig, side, sd, td, hold=p['days'] * 288, entry='market')
