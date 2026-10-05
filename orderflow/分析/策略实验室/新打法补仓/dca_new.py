"""清洗接盘、多头摊平做空 + 分批补仓（和旧打法补仓同样的方案），事先定好规则再看结果。
进场：和原版一样的信号、一样的进场价（清洗接盘：信号K线收盘价挂单；多头摊平做空：下一根开盘价）。
  清洗接盘照程序：大盘过滤（BTC 200 天线上方）+ 持仓量 24 小时过滤。
出场换成补仓那套（原版出场作对照）：
  补仓方案 A~I（3~8 笔，和旧打法那次一样）× 均价止盈 1 / 2 / 3 / 5 / 8% × 止损（补满最后一笔后再亏 3 / 6 / 10%）× 最多拿 12 / 24 / 72 小时
数据：币安合约 5 分钟。2022-01 ~ 2026-09（2022-23：38 个币，2024 以后：111 个币）。
同一根 5 分钟K线里：先止损 → 再补仓 → 这根没补仓才看止盈；进场那根不算止盈（往坏处算）。
成本：挂单进 0.02%，市价进/补仓/出场 0.05% + 滑点 0.02%，止损再加 0.05%。资金费没算（原版回测算了，所以对照组用原版的结果）。
过关：5 段（2022 / 2023 / 2024 / 2025 上半 / 2025 下半以后）至少 4 段 PF ≥ 1，全部 PF 比原版出场高，组合回测（最多 10 单、每单 10%）比原版好。"""
import sys, os, numpy as np, pandas as pd
from numba import njit
sys.path.insert(0, '/home/user/ext/long/lab')
import data, sim as S0
import s00_flush as F0
MAKER, TAKER, SLIP, STOP_SLIP = 0.0002, 0.0005, 0.0002, 0.0005
SCH = {'A3等': ([0, .02, .04], [1, 1, 1]), 'B3等': ([0, .03, .06], [1, 1, 1]), 'C3加': ([0, .03, .06], [1, 1, 2]), 'D4等': ([0, .02, .04, .06], [1] * 4),
       'E5等': ([0, .02, .04, .06, .08], [1] * 5), 'F6等': ([0, .015, .03, .045, .06, .075], [1] * 6), 'G5加': ([0, .02, .04, .06, .08], [1, 1, 2, 2, 4]),
       'H8等': ([0, .01, .02, .03, .04, .05, .06, .07], [1] * 8), 'I6深': ([0, .02, .04, .06, .09, .12], [1, 1, 1, 2, 2, 3])}
TPS = [0.01, 0.02, 0.03, 0.05, 0.08]
EXTRA = [0.03, 0.06, 0.10]
HOLDS = [12, 24, 72]
CFG = [(s, tp, ex, h) for s in SCH for tp in TPS for ex in EXTRA for h in HOLDS]
SEG = [('2022', '2022-01-01', '2023-01-01'), ('2023', '2023-01-01', '2024-01-01'), ('2024', '2024-01-01', '2025-01-01'),
       ('2025上', '2025-01-01', '2025-07-01'), ('2025下+', '2025-07-01', '2026-10-01')]


@njit(cache=True)
def dca(o, h, l, c, ib, e0s, sides, fee0, lv, wt, tp, sl, hold):
    k = len(ib); n = len(o); W = wt.sum(); out = np.empty(k); t1 = np.empty(k, np.int64)
    for q in range(k):
        i = ib[q]; s = sides[q]; e0 = e0s[q]
        qty = wt[0]; cost = wt[0] * e0; fees = wt[0] * fee0; nxt = 1
        st = e0 * (1 - s * sl); end = min(i + hold, n); ex = -1.0; j = i
        while j < end:
            if (l[j] <= st) if s > 0 else (h[j] >= st):
                ex = (min(o[j], st) if s > 0 else max(o[j], st)) * (1 - s * STOP_SLIP); break
            added = False
            while nxt < len(lv):
                px = e0 * (1 - s * lv[nxt])
                if (l[j] <= px) if s > 0 else (h[j] >= px):
                    f = (min(o[j], px) if s > 0 else max(o[j], px)) * (1 + s * SLIP)
                    qty += wt[nxt]; cost += wt[nxt] * f; fees += wt[nxt] * TAKER; nxt += 1; added = True
                else:
                    break
            tg = cost / qty * (1 + s * tp)
            if j > i and not added and ((h[j] >= tg) if s > 0 else (l[j] <= tg)):
                ex = tg; break
            j += 1
        if ex < 0:
            j = end - 1; ex = c[j] * (1 - s * SLIP)
        out[q] = (qty * s * (ex / (cost / qty) - 1) - fees - qty * TAKER) / W; t1[q] = j
    return out, t1


def load_k(c, t=None):
    """两份数据（2022-23 和 2024 以后）都读进来拼在一起"""
    parts = []
    for root in ('/home/user/ext/oos/f5', '/home/user/ext/long'):
        f = f'{root}/k/{c}.parquet'
        if os.path.exists(f):
            parts.append(pd.read_parquet(f, columns=['ts', 'open', 'high', 'low', 'close']))
    if not parts:
        return None
    return pd.concat(parts).sort_values('ts').drop_duplicates('ts', keep='last').reset_index(drop=True)


def trades():
    """原版信号：清洗接盘（2024 以后用已有结果，2022-23 现算）、多头摊平做空"""
    os.chdir('/home/user/ext/oos')
    b = pd.read_parquet('old/BTCUSDT.parquet'); dd = b.c.groupby(b.ts // 86_400_000).last(); ma = dd.rolling(200).mean()
    BULL = ((dd > ma) & ma.notna()).shift(1).fillna(False).astype(bool)
    data.ROOT = '/home/user/ext/oos/f5'
    rows = []
    for c in open('flush_old_coins.txt').read().split():
        f = F0.prep(data.load(c))
        tr = pd.DataFrame(F0.trades(f, F0.GRID[0]), columns=S0.COLS)
        if len(tr):
            tr['oi24h'] = tr.t.map(pd.Series(f.oi.pct_change(288).values, index=f.index)); tr['bull'] = tr.t.map(lambda t: BULL.get(t // 86_400_000, False))
            rows.append(tr)
    FO = pd.concat(rows); FO = FO[FO.bull & (FO.oi24h <= 0.01668)]
    FN = pd.read_parquet('/home/user/ext/long/lab/results/flush_of_feats.parquet'); FN = FN[FN.bull & (FN.oi24h <= 0.01668)]
    FL = pd.concat([FO, FN])[['coin', 't', 't_in', 't_out', 'ret']].assign(kind='清洗接盘', side=1)
    SP = pd.read_parquet('/home/user/ext/long/lab/results/s29_pick.parquet')
    SH = SP.assign(t_in=SP.t + 300_000, t_out=SP.t + 300_000 + SP.bars * 300_000)[['coin', 't', 't_in', 't_out', 'ret']].assign(kind='多头摊平做空', side=-1)
    return pd.concat([FL, SH]).reset_index(drop=True)


if __name__ == '__main__':
    T = trades()
    print('原版单数', T.groupby('kind').size().to_dict(), flush=True)
    res = {ci: np.full(len(T), np.nan) for ci in range(len(CFG))}
    tout = {ci: np.zeros(len(T), np.int64) for ci in range(len(CFG))}
    for c, g in T.groupby('coin'):
        k = load_k(c, int(g.t.min()))
        if k is None:
            continue
        ts = k.ts.values; o, h, l, cl = (k[x].values.astype(float) for x in ('open', 'high', 'low', 'close'))
        ib = np.searchsorted(ts, g.t_in.values); isig = np.searchsorted(ts, g.t.values)
        ok = (ib < len(ts)) & (ts[np.minimum(ib, len(ts) - 1)] == g.t_in.values) & (isig < len(ts))
        g2 = g[ok]; ib = ib[ok]; isig = isig[ok]
        if not len(g2):
            continue
        fl = (g2.kind == '清洗接盘').values
        e0 = np.where(fl, np.minimum(cl[isig], o[ib]), o[ib] * (1 - SLIP))          # 清洗：挂单价（跳空按开盘）；做空：开盘价市价
        fee0 = np.where(fl, MAKER, TAKER)
        sides = g2.side.values.astype(float)
        for ci, (sc, tp, ex, hh) in enumerate(CFG):
            lv, wt = SCH[sc]
            r = np.empty(len(g2)); t1 = np.empty(len(g2), np.int64)
            for f0 in (MAKER, TAKER):
                m = fee0 == f0
                if m.any():
                    rr, jj = dca(o, h, l, cl, ib[m], e0[m], sides[m], f0, np.array(lv, float), np.array(wt, float), tp, lv[-1] + ex, hh * 12)
                    r[m] = rr; t1[m] = ts[np.minimum(jj, len(ts) - 1)] + 300_000
            res[ci][g2.index] = r; tout[ci][g2.index] = t1
    pd.to_pickle((T, res, tout), '/home/user/ext/of/dca_new.pkl')
    print('done', flush=True)
