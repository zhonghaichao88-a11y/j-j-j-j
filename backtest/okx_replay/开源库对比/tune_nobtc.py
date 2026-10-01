"""方案三去掉 BTC 过滤，改用每个币自己的方向 + 进场指标确认（严格口径、无前视）。
方向：不过滤 / 自身1h EMA200 / 自身日线EMA50（已收盘日线）/ 自身超级趋势(10,3)
进场：不加 / RSI(14) 不超买超卖（多<70、空>30）/ MACD柱同向 / 两个都加
选组合：更早137币 2022、2023；验证：2024前三季、币安独有416、币安前100、欧易新64。成本 0 与 0.15%。"""
import itertools, numpy as np, pandas as pd, talib
from multiprocessing import Pool
import adx_bt as A, st_bt as S, tune3 as U
A.STRICT[0] = True
H = A.H; DAY = A.D
def daily_ema_side(f):
    ts = np.asarray(f['ts'], np.int64); c = np.asarray(f['close'], float)
    d = pd.DataFrame({'d': ts // DAY, 'c': c}).groupby('d').c.last()
    e = d.ewm(span=50, adjust=False).mean(); up = (d > e)
    day_close = (ts + H) // DAY - 1                          # 信号时刻能用的最近已收盘日线
    u = up.reindex(day_close).to_numpy()
    return np.where(pd.isna(u), False, u).astype(bool), np.where(pd.isna(u), False, ~u.astype(bool)).astype(bool)
def make_filter(direction, entry):
    def flt(f):
        h, l, c = (np.asarray(f[k], float) for k in ('high', 'low', 'close')); n = len(c)
        lo = np.ones(n, bool); so = np.ones(n, bool)
        if direction == 'EMA200':
            e = talib.EMA(c, 200); lo &= np.nan_to_num(c > e).astype(bool); so &= np.nan_to_num(c < e).astype(bool)
        elif direction == '日线EMA50':
            a, b = daily_ema_side(f); lo &= a; so &= b
        elif direction == '超级趋势':
            dd = S.st_dir(h, l, c, 10, 3); lo &= dd == 1; so &= dd == -1
        if entry in ('RSI', 'RSI+MACD'):
            r = talib.RSI(c, 14); lo &= np.nan_to_num(r < 70).astype(bool); so &= np.nan_to_num(r > 30).astype(bool)
        if entry in ('MACD', 'RSI+MACD'):
            _, _, hist = talib.MACD(c, 12, 26, 9); lo &= np.nan_to_num(hist > 0).astype(bool); so &= np.nan_to_num(hist < 0).astype(bool)
        return lo, so
    return flt
GRID = list(itertools.product(('不过滤', 'EMA200', '日线EMA50', '超级趋势'), ('不加', 'RSI', 'MACD', 'RSI+MACD')))
DATA = None
def job(args):
    direction, entry = args; A.FILTER[0] = make_filter(direction, entry); out = []
    for kind, ds, lab, a, c in U.SEG:
        fr, btc = DATA[ds]
        for fee in (0.0, 0.0015):
            T = A.run(fr, btc, fee=fee, t0=U.ms(a), t1=U.ms(c), wallet=990, short=True, use_btc=False); T = T[T.why != '结束']
            w = T.r > 0
            out.append(dict(方向=direction, 进场=entry, 段=f'{kind}:{ds}{lab}', 成本=fee, 笔数=len(T), PF=round(T.r[w].sum() / max(-T.r[~w].sum(), 1e-9), 3)))
    return out
if __name__ == '__main__':
    DATA = U.load(); rows = []
    with Pool(1) as p:
        for r in p.imap_unordered(job, GRID):
            rows += r; print(r[0]['方向'], r[0]['进场'], [x['PF'] for x in r if x['成本'] > 0], flush=True)
    pd.DataFrame(rows).to_csv('tune_nobtc.csv', index=False); print('DONE')
