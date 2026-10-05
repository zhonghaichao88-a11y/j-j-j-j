import numpy as np
from numba import njit
TAKER, MAKER, SLIP, STOP_SLIP = 0.0005, 0.0002, 0.0002, 0.0005
EXITS = [(0.02, 0.05, 24 * 12), (0.03, 0.08, 48 * 12), (0.05, 0.11, 72 * 12), (0.015, 0.03, 12 * 12)]   # 止盈, 止损, 最多几根5分钟


@njit(cache=True)
def label(o, h, l, c, idx, side, tp, sl, hold):
    n = len(o); res = np.full(len(idx), np.nan); dur = np.full(len(idx), -1, np.int16)
    for q in range(len(idx)):
        i = idx[q] + 1
        if i >= n - 1: continue
        e = o[i] * (1 + side * SLIP); tg = e * (1 + side * tp); st = e * (1 - side * sl); ex = np.nan; fee = TAKER
        for j in range(i, min(n, i + hold)):
            if (l[j] <= st) if side > 0 else (h[j] >= st):
                ex = (min(o[j], st) if side > 0 else max(o[j], st)) * (1 - side * STOP_SLIP); fee += TAKER; dur[q] = j - i + 1; break
            if (h[j] >= tg) if side > 0 else (l[j] <= tg):
                ex = tg; fee += MAKER; dur[q] = j - i + 1; break
        if np.isnan(ex):
            if i + hold > n: continue                   # 数据不够看完这笔，不算
            ex = c[min(n, i + hold) - 1] * (1 - side * SLIP); fee += TAKER; dur[q] = hold
        res[q] = side * (ex / e - 1) - fee
    return res, dur


