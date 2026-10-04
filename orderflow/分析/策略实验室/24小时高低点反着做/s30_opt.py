"""用户方案：价格跌破过去 24 小时最低价 → 马上做多；涨破过去 24 小时最高价 → 马上做空。吃一小段就走。
不看未来：第 i 根收盘时算好"过去 24 小时(含第 i 根)最低/最高"，第 i+1 根里价格穿过它才成交（像挂在那个价上的单，跳空就按开盘价）。
成交那根如果也碰到止损，按止损算；成交那根不算止盈（不知道先后，往坏处算）。之后每根：先止损 → 止盈 → 到时间收盘平。
成本两种：挂单进场（0.02%）；吃单进场（0.05% + 滑点 0.02%）。出场：止盈挂单 0.02%，止损/到时间吃单 0.05% + 滑点（止损滑点 0.05%）。
参数网格只在 2024（训练）上挑，挑完原样看 2025 以后、新币、最近 6 个月、2022-23 老数据。"""
import sys, os, json, numpy as np, pandas as pd
from numba import njit
sys.path.insert(0, '/home/user/ext/long/lab')
import data, report
from multiprocessing import Pool
MAKER, TAKER, SLIP, STOP_SLIP = 0.0002, 0.0005, 0.0002, 0.0005
TPS = [0.005, 0.01, 0.02, 0.03]
SLS = [0.01, 0.02, 0.03, 0.05]
HOLDS = [12, 48, 288]          # 1 小时 / 4 小时 / 24 小时
W = 288
OPT = True          # 乐观：成交那根也算止盈（先碰最低再反弹）


@njit(cache=True)
def run(O, H, L, C, lvl, side, tp, sl, hold):
    n = len(O)
    out_i = np.empty(n, np.int64); out_j = np.empty(n, np.int64); out_r = np.empty(n); out_k = np.empty(n, np.int64)
    m = 0; i = W
    while i < n - 1:
        px = lvl[i]
        j0 = i + 1
        hit = (L[j0] < px) if side > 0 else (H[j0] > px)
        if not hit or not np.isfinite(px):
            i += 1
            continue
        e = min(O[j0], px) if side > 0 else max(O[j0], px)
        st = e * (1 - side * sl); tg = e * (1 + side * tp)
        end = min(j0 + hold - 1, n - 1)
        ex = -1.0; kind = 2; j = j0
        for j in range(j0, end + 1):
            if (L[j] <= st) if side > 0 else (H[j] >= st):
                base = min(O[j], st) if side > 0 else max(O[j], st)
                if j == j0:
                    base = st
                ex = base * (1 - side * STOP_SLIP); kind = 0
                break
            if (j > j0 or OPT) and ((H[j] > tg) if side > 0 else (L[j] < tg)):
                ex = tg; kind = 1
                break
        if ex < 0:
            j = end
            ex = C[j] * (1 - side * SLIP)
        fee_out = MAKER if kind == 1 else TAKER
        out_i[m] = j0; out_j[m] = j; out_r[m] = side * (ex / e - 1) - fee_out; out_k[m] = kind
        m += 1
        i = j          # 平仓那根收盘后才看下一次
    return out_i[:m], out_j[:m], out_r[:m], out_k[:m]


def coin(args):
    root, c = args
    data.ROOT = root
    d = data.load(c)
    O, H, L, C = (d[k].values.astype(float) for k in 'ohlc')
    T = d.index.values
    lo = pd.Series(L).rolling(W, min_periods=W).min().values
    hi = pd.Series(H).rolling(W, min_periods=W).max().values
    fts, fr = d.attrs['fts'], d.attrs['frate']
    rows = []
    for side, lvl in ((1, lo), (-1, hi)):
        for tp in TPS:
            for sl in SLS:
                for hd in HOLDS:
                    a, b, r, k = run(O, H, L, C, lvl, side, tp, sl, hd)
                    if len(a) == 0:
                        continue
                    if len(fts):
                        ia, ib = np.searchsorted(fts, T[a], 'right'), np.searchsorted(fts, T[b], 'right')
                        cs = np.r_[0, np.cumsum(fr)]
                        r = r - side * (cs[ib] - cs[ia])
                    rows.append(pd.DataFrame({'coin': c, 'side': side, 'tp': tp, 'sl': sl, 'hold': hd, 't': T[a], 't_in': T[a], 't_out': T[b], 'ret': r, 'why': k}))
    return pd.concat(rows) if rows else None


if __name__ == '__main__':
    jobs = [('/home/user/ext/long', c) for c in data.coins()] + [('/home/user/ext/oos/f5', c) for c in open('/home/user/ext/oos/flush_old_coins.txt').read().split()]
    with Pool(4) as p:
        parts = p.map(coin, jobs, chunksize=1)
    A = pd.concat([x.assign(old=(j[0] != '/home/user/ext/long')) for x, j in zip(parts, jobs) if x is not None])
    A.to_parquet('/home/user/ext/long/lab/s30/opt.parquet')
    print(len(A))
