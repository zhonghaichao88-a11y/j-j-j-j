"""多头摊平做空：只放宽止损（不补仓），进场、"多头被清洗就平"、最多 48 小时都不变。止损 5%（原版）/ 7 / 10 / 15 / 20 / 30%。
另外看原版被止损的单：如果当时不止损、一直拿到原来的平仓规则（清洗平仓或 48 小时），最后是赚还是亏（"打了止损再下去"有多少）。"""
import sys, heapq, numpy as np, pandas as pd
sys.path.insert(0, '/home/user/ext/long/lab'); sys.path.insert(0, '.')
import data, report
import s28_short4y as B
from trap_dca import run
from trap_dca_analyze import port, SEG
STOPS = [0.05, 0.07, 0.10, 0.15, 0.20, 0.30, 0.99]
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
        for sl in STOPS:
            ii, rr, jj, kk = run(O, H, L, C, flush, cf.astype(float), sig, np.array([0.0]), np.array([1.0]), sl, 576)
            for i, r, j in zip(ii, rr, jj):
                rows.append((sl, c, int(T[i]), int(T[i]) + 300_000, int(T[j]) + 300_000, r))
T = pd.DataFrame(rows, columns=['sl', 'coin', 't', 't_in', 't_out', 'ret'])
pf = report.pf
print('止损 | 单数 | 胜率 | PF | 5 段 PF | 最差一单 | 组合 100U（每单 10%，最多 10 单） | 回撤')
for sl in STOPS:
    X = T[T.sl == sl]
    segs = [pf(X[(X.t >= pd.Timestamp(a).value // 10**6) & (X.t < pd.Timestamp(b).value // 10**6)].ret) for _, a, b in SEG]
    fin, dd = port(X)
    print(f'{"不设(99%)" if sl > 0.9 else f"{sl:.0%}"} | {len(X)} | {(X.ret > 0).mean():.0%} | {pf(X.ret):.2f} | ' + '/'.join(f'{v:.2f}' for v in segs) +
          f' | {X.ret.min():.1%} | {fin}U | {dd}%')
# 原版被止损的单：不止损的话最后怎样
A, Z = T[T.sl == 0.05].set_index(['coin', 't']), T[T.sl == 0.99].set_index(['coin', 't'])
st = A[A.ret < -0.045].index.intersection(Z.index)
after = Z.loc[st].ret
print(f'\n原版被止损 {len(st)} 单，如果不止损拿到原规则平仓：最后赚的 {(after > 0).mean():.0%}，最后亏得比止损还多的 {(after < -0.06).mean():.0%}，平均 {after.mean():+.1%}（止损是 -5.6% 左右）')
T.to_parquet('/home/user/ext/of/trap_stop.parquet')
