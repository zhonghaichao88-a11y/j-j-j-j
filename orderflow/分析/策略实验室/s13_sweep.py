import numpy as np, sim, feat
NAME = '扫昨日高低点后收回（1 小时级）'
RULES = '出处：流动性猎取（liquidity sweep / stop hunt）。整点：这 1 小时最高价刺破昨日最高、收盘回到昨日最高下面，且是今天第一次 → 做空；止损在这小时最高价上面 0.25 个振幅；目标 2R 或昨日中间价。昨日最低反过来做多。'
GRID = [{'tgt': t, 'hold': h} for t in ('2R', 'mid') for h in (48, 144)]


def prep(df):
    df = feat.add_basic(df)
    day, starts = feat.day_index(df)
    dh = df.h.groupby(day).max()
    dl = df.l.groupby(day).min()
    df['pdh'] = (day - 1).astype(np.int64)
    df['pdh'] = df.pdh.map(dh)
    df['pdl'] = (day - 1).astype(np.int64)
    df['pdl'] = df.pdl.map(dl)
    df['day'] = day
    return df


def trades(df, p):
    hc = np.flatnonzero(feat.hour_close(df))
    H, L, C = df.h1h.values, df.l1h.values, df.c.values
    pdh, pdl, day = df.pdh.values, df.pdl.values, df.day.values
    used = set()
    sig, side, sd, td = [], [], [], []
    for i in hc:
        if not (np.isfinite(pdh[i]) and np.isfinite(H[i])):
            continue
        rng = H[i] - L[i]
        mid = (pdh[i] + pdl[i]) / 2
        for s, cond in ((-1, H[i] > pdh[i] and C[i] < pdh[i]), (1, L[i] < pdl[i] and C[i] > pdl[i])):
            key = (day[i], s)
            if (day[i], s, 'seen') in used:
                continue
            if (s == -1 and H[i] > pdh[i]) or (s == 1 and L[i] < pdl[i]):
                used.add((day[i], s, 'seen'))          # 今天第一次碰到才算
                if not cond:
                    continue
                st = H[i] + 0.25 * rng if s == -1 else L[i] - 0.25 * rng
                s_ = s * (C[i] - st) / C[i]
                t_ = 2 * s_ if p['tgt'] == '2R' else s * (mid - C[i]) / C[i]
                if s_ > 0 and t_ > 0:
                    sig.append(i); side.append(s); sd.append(s_); td.append(t_)
    order = np.argsort(sig, kind='stable')
    return sim.run(df, np.array(sig)[order], np.array(side)[order], np.array(sd)[order], np.array(td)[order], hold=p['hold'], entry='market')
