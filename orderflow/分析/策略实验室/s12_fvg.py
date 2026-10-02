import numpy as np, sim, feat
NAME = '公允价值缺口（FVG）回补'
RULES = '出处：聪明钱概念（SMC）的 FVG。1 小时K线：第 k 根最低价 > 第 k-2 根最高价（中间第 k-1 根是实体 ≥1.5 倍平时的大阳线），缺口 ≥ G → 在缺口上沿挂买单 24 小时内有效；止损在缺口下沿下面 0.25 个缺口；止盈 RR 倍止损；最多拿 24 小时。空头反过来。'
GRID = [{'G': g, 'RR': r} for g in (.003, .006) for r in (1.5, 2.5)]


def prep(df):
    hb = feat.hourly(df)
    hb['body'] = (hb.c - hb.o).abs()
    hb['avgb'] = hb.body.rolling(24, min_periods=12).mean().shift(1)
    df.attrs['hb'] = hb
    return df


def trades(df, p):
    hb = df.attrs['hb']
    o, h, l, c, end, body, avgb = (hb[k].values for k in ('o', 'h', 'l', 'c', 'end', 'body', 'avgb'))
    sig, side, lpx, sd, td = [], [], [], [], []
    for k in range(2, len(hb)):
        big = body[k - 1] >= 1.5 * avgb[k - 1] if np.isfinite(avgb[k - 1]) else False
        if not big:
            continue
        if l[k] > h[k - 2] and c[k - 1] > o[k - 1]:
            gap = l[k] - h[k - 2]
            if gap / c[k] >= p['G']:
                st = h[k - 2] - 0.25 * gap
                s_ = (l[k] - st) / l[k]
                sig.append(end[k]); side.append(1); lpx.append(l[k]); sd.append(s_); td.append(p['RR'] * s_)
        elif h[k] < l[k - 2] and c[k - 1] < o[k - 1]:
            gap = l[k - 2] - h[k]
            if gap / c[k] >= p['G']:
                st = l[k - 2] + 0.25 * gap
                s_ = (st - h[k]) / h[k]
                sig.append(end[k]); side.append(-1); lpx.append(h[k]); sd.append(s_); td.append(p['RR'] * s_)
    return sim.run(df, sig, side, sd, td, hold=288, entry='limit', lpx=lpx, lbars=288, cooldown=12)
