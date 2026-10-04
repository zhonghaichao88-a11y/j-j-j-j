"""用 1 分钟K线复核"跌破 24h 低做多 / 涨破 24h 高做空"（26 个币，2025-01 ~ 2026-08）。
成交那根 K 线里不知道先碰最低还是先碰止盈：保守=不算止盈，乐观=算。1 分钟上两者差得越少，结论越可信。"""
import sys, numpy as np, pandas as pd, glob, os
from numba import njit
sys.path.insert(0, '/home/user/ext/long/lab'); import report
MAKER, TAKER, SLIP, STOP_SLIP = 0.0002, 0.0005, 0.0002, 0.0005

@njit(cache=True)
def run(O, H, L, C, lvl, side, tp, sl, hold, W, opt):
    n = len(O); out_r = np.empty(n); out_i = np.empty(n, np.int64); m = 0; i = W
    while i < n - 1:
        px = lvl[i]; j0 = i + 1
        if not np.isfinite(px) or not ((L[j0] < px) if side > 0 else (H[j0] > px)):
            i += 1; continue
        e = min(O[j0], px) if side > 0 else max(O[j0], px)
        st = e * (1 - side * sl); tg = e * (1 + side * tp); end = min(j0 + hold - 1, n - 1)
        ex = -1.0; fee = TAKER; j = j0
        for j in range(j0, end + 1):
            if (L[j] <= st) if side > 0 else (H[j] >= st):
                ex = (st if j == j0 else (min(O[j], st) if side > 0 else max(O[j], st))) * (1 - side * STOP_SLIP); break
            if (j > j0 or opt) and ((H[j] > tg) if side > 0 else (L[j] < tg)):
                ex = tg; fee = MAKER; break
        if ex < 0:
            j = end; ex = C[j] * (1 - side * SLIP)
        out_r[m] = side * (ex / e - 1) - fee - (TAKER + SLIP); out_i[m] = j0; m += 1   # 吃单进场
        i = j
    return out_r[:m], out_i[:m]

rows = []
for f in sorted(glob.glob('/home/user/ext/m1/*.parquet')):
    c = os.path.basename(f)[:-8]
    d = pd.read_parquet(f)
    d5 = d.assign(g=d.ts // 300000).groupby('g').agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'))
    for tf, x, W, mult in (('1m', d, 1440, 5), ('5m', d5, 288, 1)):
        O, H, L, C = (x[k].values.astype(float) for k in 'ohlc')
        lo = pd.Series(L).rolling(W).min().values; hi = pd.Series(H).rolling(W).max().values
        for side, lvl in ((1, lo), (-1, hi)):
            for tp in (0.005, 0.01, 0.02):
                for sl in (0.02, 0.03, 0.05):
                    for hd in (12, 48, 288):
                        for opt in (False, True):
                            r, _ = run(O, H, L, C, lvl, side, tp, sl, hd * mult, W, opt)
                            rows.append((c, tf, side, tp, sl, hd, opt, len(r), r.sum(), r[r > 0].sum(), -r[r < 0].sum(), (r > 0).sum()))
R = pd.DataFrame(rows, columns=['coin', 'tf', 'side', 'tp', 'sl', 'hold', 'opt', 'n', 'sum', 'gp', 'gl', 'nw'])
G = R.groupby(['side', 'tp', 'sl', 'hold', 'tf', 'opt'])[['n', 'sum', 'gp', 'gl', 'nw']].sum()
G['pf'] = G.gp / G.gl; G['bp'] = G['sum'] / G.n * 1e4; G['win'] = G.nw / G.n
T = G.pf.unstack(['tf', 'opt']).round(2)
T.columns = [f'{a}{"乐观" if b else "保守"}' for a, b in T.columns]
pd.set_option('display.width', 200); pd.set_option('display.max_rows', 200)
print(T.to_string())
print('\n每笔基点（1分钟保守）:'); print(G.xs(('1m', False), level=('tf', 'opt')).bp.round(1).unstack('hold').to_string())
G.to_csv('s30/m1_grid.csv')
