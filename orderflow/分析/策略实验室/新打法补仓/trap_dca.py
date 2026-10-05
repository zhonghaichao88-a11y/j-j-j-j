"""多头摊平做空 + 补仓（用户的想法）：进场、"多头被清洗就平"、最多 48 小时都不变；
做空后价格继续涨，就在 +2% / +4% / ... 补空（均价抬高），止损放宽到"补满最后一笔后再涨 X%"。
补仓方案：不补（原版，止损 5%）/ A 3 笔 +2/+4 / B 3 笔 +3/+6 / C 3 笔 1:1:2 +3/+6 / D 4 笔 +2/+4/+6 / E 5 笔 +2..+8 / G 5 笔 1:1:2:2:4 / I 6 笔 +2..+12
止损：补满后再涨 3% / 5% / 10%
收益按这单最多能用的钱算（没补的部分算 0），资金费照算（空单在资金费为正时收钱）。
5 段（2022 / 2023 / 2024 / 2025 上 / 2025 下以后），过关：≥4 段 PF ≥ 1，组合（最多 10 单、每单 10%）比原版好，回撤不比原版差太多。"""
import sys, heapq, numpy as np, pandas as pd
from numba import njit
sys.path.insert(0, '/home/user/ext/long/lab')
import data, report
import s28_short4y as B
FEE, SLIP, SSLIP = B.FEE, B.SLIP, B.SSLIP
SCH = {'不补(原版)': ([0.0], [1.0]), 'A3等': ([0, .02, .04], [1, 1, 1]), 'B3等': ([0, .03, .06], [1, 1, 1]), 'C3加': ([0, .03, .06], [1, 1, 2]),
       'D4等': ([0, .02, .04, .06], [1] * 4), 'E5等': ([0, .02, .04, .06, .08], [1] * 5), 'G5加': ([0, .02, .04, .06, .08], [1, 1, 2, 2, 4]),
       'I6深': ([0, .02, .04, .06, .09, .12], [1, 1, 1, 2, 2, 3])}
EXTRA = [0.03, 0.05, 0.10]
CFG = [('不补(原版)', 0.05)] + [(s, ex) for s in SCH if s != '不补(原版)' for ex in EXTRA]


@njit(cache=True)
def run(O, H, L, C, flush, cf, sig, lv, wt, sl, maxh):
    n = len(O); W = wt.sum(); out_i = []; out_r = []; out_j = []; out_k = []; busy = -1
    for i in sig:
        if i <= busy or i + 1 >= n:
            continue
        j0 = i + 1; e0 = O[j0] * (1 - SLIP)
        qty = wt[0]; cost = wt[0] * e0; fund = 0.0; fills = [j0]; nxt = 1
        st = e0 * (1 + sl); end = min(j0 + maxh - 1, n - 1); ex = -1.0; j = j0
        for j in range(j0, end + 1):
            if H[j] >= st:
                ex = max(O[j], st) * (1 + SSLIP); break
            while nxt < len(lv):
                px = e0 * (1 + lv[nxt])
                if H[j] >= px:
                    f = max(O[j], px) * (1 - SLIP)
                    qty += wt[nxt]; cost += wt[nxt] * f; fills.append(j); nxt += 1
                else:
                    break
            if flush[j] and j > j0:
                ex = C[j] * (1 + SLIP); break
        if ex < 0:
            j = end; ex = C[j] * (1 + SLIP)
        for q in range(len(fills)):                    # 资金费：每一笔从成交到平仓，空单收 (cf 是累计费率)
            fund += wt[q] * (cf[j] - cf[fills[q]])
        avg = cost / qty
        ret = (qty * (-(ex / avg - 1)) - 2 * FEE * qty + fund) / W
        out_i.append(i); out_r.append(ret); out_j.append(j); out_k.append(nxt)
        busy = j
    return out_i, out_r, out_j, out_k


def main():
    z = lambda s: (s - s.rolling(288 * 7, min_periods=288).mean()) / s.rolling(288 * 7, min_periods=288).std()
    old = open('/home/user/ext/oos/flush_old_coins.txt').read().split(); new = data.coins()
    rows = []
    for root, coins in (('/home/user/ext/oos/f5', old), ('/home/user/ext/long', new)):
        for c, df in B.load_set(root, coins):
            df['ls_z'] = z(df.ls)
            m = ((df.r24h < -0.05) & (df.oi24h > 0.05) & (df.fund > 0) & (df.ls_z > 0)).fillna(False).values
            sig = np.flatnonzero(m).astype(np.int64)
            if not len(sig):
                continue
            O, H, L, C = (df[k].values.astype(float) for k in 'ohlc'); T = df.index.values
            flush = ((df.r60 < -0.02) & (df.oi60 < -0.03)).fillna(False).values
            fts, fr = df.attrs.get('fts', np.array([])), df.attrs.get('frate', np.array([]))
            cf = np.r_[0, np.cumsum(fr)][np.searchsorted(fts, T, 'right')] if len(fts) else np.zeros(len(T))
            for ci, (sc, ex) in enumerate(CFG):
                lv, wt = np.array(SCH[sc][0], float), np.array(SCH[sc][1], float)
                ii, rr, jj, kk = run(O, H, L, C, flush, cf.astype(float), sig, lv, wt, lv[-1] + ex, 576)
                for i, r, j, k in zip(ii, rr, jj, kk):
                    rows.append((ci, c, int(T[i]), int(T[i]) + 300_000, int(T[j]) + 300_000, r, k))
            print(c, flush=True)
    pd.DataFrame(rows, columns=['ci', 'coin', 't', 't_in', 't_out', 'ret', 'legs']).to_parquet('/home/user/ext/of/trap_dca.parquet')


if __name__ == '__main__':
    main()
