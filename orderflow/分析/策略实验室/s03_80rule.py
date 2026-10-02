import numpy as np, sim, feat
NAME = '市场轮廓 80% 规则'
RULES = '出处：Market Profile 80% rule。UTC 当天开盘价在昨天价值区（70% 成交量）外面，之后连续 conf 个 30 分钟收盘回到价值区里 → 往价值区另一边做（目标：另一边沿 edge 或 POC）；止损在进来那一边外面 0.1 个价值区宽度；最晚当天结束平仓。'
GRID = [{'tgt': 'edge', 'conf': 2}, {'tgt': 'poc', 'conf': 2}, {'tgt': 'edge', 'conf': 1}, {'tgt': 'poc', 'conf': 1}]


def prep(df):
    df.attrs['prof'] = feat.day_profiles(df)
    return df


def trades(df, p):
    prof = df.attrs['prof']
    day, starts = feat.day_index(df)
    O, C = df.o.values, df.c.values
    ends = np.r_[starts[1:], len(day)]
    sig, side, sd, td, hold = [], [], [], [], []
    for a, e in zip(starts, ends):
        pv = prof.get(int(day[a]) - 1)
        if pv is None or e - a < 200:
            continue
        poc, vah, val = pv
        if O[a] > vah:
            s = -1
        elif O[a] < val:
            s = 1
        else:
            continue
        cnt = 0
        for b in range((e - a) // 6):
            i = a + 6 * b + 5
            cnt = cnt + 1 if val < C[i] < vah else 0
            if cnt >= p['conf']:
                if e - 1 - i < 24:
                    break
                w = vah - val
                tg = (val if s == -1 else vah) if p['tgt'] == 'edge' else poc
                st = vah + 0.1 * w if s == -1 else val - 0.1 * w
                t_ = s * (tg - C[i]) / C[i]
                s_ = s * (C[i] - st) / C[i]
                if t_ > 0.002 and s_ > 0:
                    sig.append(i); side.append(s); sd.append(s_); td.append(t_); hold.append(e - 1 - i)
                break
    return sim.run(df, sig, side, sd, td, hold=hold, entry='market')
