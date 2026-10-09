"""多头摊平做空"多张挂单"挂法：同一个币每来一个新信号就再挂一张空单（信号收盘价 ×(1+反弹)），每张各等 N 分钟；
哪张先被涨上去碰到就成交，其余全部撤掉；有仓位时新信号不理；位置不好跳过后 1 小时不再判断（和程序一样）。
一根一根 5 分钟K线模拟（程序能照做）。和 trap_tune.py（一次只挂一张，挂着时新信号不理）对比。
参数：反弹 1% / 1.5% / 2% / 2.5%；每张等 30 / 60 / 120 分钟；离 24h 低点 不限 / >2% / >3% / >4%；
出场：原版（被清洗就平）止损 5% / 8%，ATR 止盈 2 倍止损 3 倍 48h。过关标准同 trap_tune.py。"""
import os, sys, numpy as np, pandas as pd
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trap_tune as T
import data
from sim import TAKER, MAKER, SLIP, STOP_SLIP
HERE = os.path.dirname(os.path.abspath(__file__)); OUT = '/home/user/ext/nfisig/trap_multi.parquet'
BOUNCE = [0.01, 0.015, 0.02, 0.025]; WAIT = [6, 12, 24]; DMIN = [0, 0.02, 0.03, 0.04]
EXITS = {'原版 止损5%': 0.05, '原版 止损8%': 0.08, 'ATR止盈2倍止损3倍': None}


def job(a):
    root, c = a
    data.ROOT = root
    if not os.path.exists(f'{root}/met/{c}.parquet'): return None
    try: df = data.load(c)
    except Exception: return None
    if len(df) < 20000 or df.oi.notna().sum() < 10000 or df.ls.notna().sum() < 10000: return None
    import talib
    O, H, L, C = (df[x].values.astype(float) for x in ('o', 'h', 'l', 'c'))
    r24 = df.c.pct_change(288); oi24 = df.oi / df.oi.shift(288) - 1
    lsh = df.ls.iloc[11::12]; lz = (lsh - lsh.rolling(168, min_periods=48).mean()) / lsh.rolling(168, min_periods=48).std()
    lsz = lz.reindex(df.index, method='ffill')
    sig = np.flatnonzero(((r24 < -0.05) & (oi24 > 0.05) & (df.fund > 0) & (lsz > 0)).fillna(False).values)
    n = len(C); sig = sig[sig + 2 < n]
    if not len(sig): return None
    r60 = df.c.pct_change(12).values; oi60 = (df.oi / df.oi.shift(12) - 1).values; flush = (r60 < -0.02) & (oi60 < -0.03)
    k = df[['h', 'l', 'c']].copy(); k.attrs = {}; k.index = pd.to_datetime(k.index, unit='ms')
    k1 = k.resample('1h', label='left', closed='left').agg({'h': 'max', 'l': 'min', 'c': 'last'}).dropna()
    s = pd.Series(talib.ATR(k1.h.values, k1.l.values, k1.c.values, 14), index=(k1.index + pd.Timedelta('1h')).values.astype('datetime64[ms]').astype(np.int64))
    atr1 = s.reindex(df.index, method='ffill').values
    lo24 = df.l.rolling(288).min().values; T5 = df.index.values.astype(np.int64)
    issig = np.zeros(n, bool); issig[sig] = True

    def outcome(i, j0, e, sl):
        fee = MAKER; a = atr1[i]
        if sl is None:
            if not (a > 0): return None
            st, tg = e + 3 * a, e - 2 * a
        else:
            st, tg = e * (1 + sl), None
        end = min(j0 + 575, n - 1); xp = None
        for j in range(j0, end + 1):
            if H[j] >= st: xp = max(O[j], st) * (1 + STOP_SLIP); fee += TAKER; break
            if j > j0 and tg is not None and L[j] <= tg: xp = tg; fee += MAKER; break
            if sl is not None and j > j0 and flush[j]: xp = C[j] * (1 + SLIP); fee += TAKER; break
        if xp is None: j = end; xp = C[j] * (1 + SLIP); fee += TAKER
        return j, -(xp / e - 1) - fee

    rows = []
    for bx in BOUNCE:
        for w in WAIT:
            for dn in DMIN:
                for ex, sl in EXITS.items():
                    pend = []; free_at = -1; skip_until = -1; q = 0; j = sig[0]
                    while j < n:
                        if pend:                                          # 先看这根K线有没有挂单被碰到
                            pend = [p for p in pend if p[1] >= j]
                            hit = [p for p in pend if p[2] < j and H[j] >= p[0]]
                            if hit:
                                px, _, i = min(hit)                       # 价格往上走，先碰到最低的那张
                                e = max(O[j], px); r = outcome(i, j, e, sl); pend = []
                                if r is not None:
                                    jx, ret = r; free_at = jx
                                    rows.append((c, bx, w, dn, ex, int(T5[i]), int(T5[j]), int(T5[jx]), ret))
                                    j = jx + 1; continue
                        if issig[j] and j > free_at and T5[j] >= skip_until:
                            if dn and not (C[j] > lo24[j] * (1 + dn)):
                                skip_until = T5[j] + 3_600_000
                            else:
                                pend.append((C[j] * (1 + bx), j + w, j))
                        if pend:
                            j += 1
                        else:
                            nx = np.searchsorted(sig, j + 1)
                            if nx >= len(sig): break
                            j = sig[nx]
    return pd.DataFrame(rows, columns=['coin', 'bounce', 'wait', 'dmin', 'exit', 't', 't_in', 't_out', 'ret']) if rows else None


def main():
    if not os.path.exists(OUT):
        jobs = [(r, f[:-8]) for r in T.ROOTS for f in sorted(os.listdir(f'{r}/k')) if f.endswith('.parquet')]
        with Pool(4) as p: R = [x for x in p.map(job, jobs, chunksize=1) if x is not None]
        pd.concat(R).drop_duplicates(['coin', 'bounce', 'wait', 'dmin', 'exit', 't']).to_parquet(OUT)
    D = pd.read_parquet(OUT); D = D[(D.t >= pd.Timestamp('2021-12-01').value // 10**6)]
    D['seg'] = np.select([D.t < pd.Timestamp('2024-01-01').value // 10**6, D.t < pd.Timestamp('2025-04-01').value // 10**6], [0, 1], 2)
    D['d'] = ((D.t_out - D.t_in) // 300_000 + 1).astype(np.int64)
    D = D.sort_values('t_in').reset_index(drop=True); D['cid'] = D.coin.astype('category').cat.codes.astype(np.int64); nc = int(D.cid.max()) + 1
    sys.path.insert(0, '/home/user/j-j-j-j/orderflow/分析/策略实验室/NFI信号对比'); from itemsets import evaluate
    DAYS = [761, 455, 548]; SEGN = ['2021-12~2023-12', '2024-01~2025-03', '2025-04~2026-09']; out = []
    for (bx, w, dn, ex), g in D.groupby(['bounce', 'wait', 'dmin', 'exit']):
        rs = []
        for s in range(3):
            x = g[g.seg == s]
            if not len(x): rs.append(None); continue
            n_, win, gp, gl, p1, l1, p2, l2, eq, dd = evaluate(x.t_in.values, x.cid.values, x.ret.values.astype(float), x.d.values, nc)
            rs.append(dict(n=n_, day=n_ / DAYS[s], win=win / max(n_, 1), pf=gp / gl if gl > 0 else 9.99, eq=eq, dd=-dd * 100,
                           h1=p1 / l1 if l1 > 0 else 9.99, h2=p2 / l2 if l2 > 0 else 9.99))
        ok = [r is not None and r['n'] >= 30 and r['pf'] >= 1.25 and r['win'] >= 0.45 and r['eq'] > 100 and r['h1'] >= 1 and r['h2'] >= 1 for r in rs]
        good = [r for r in rs if r]
        out.append(dict(反弹=f'{bx:.1%}', 等=f'{w * 5}分钟', 离低点=f'>{dn:.0%}' if dn else '不限', 出场=ex, 过关=all(ok), 前两段过=ok[0] and ok[1], 第三段过=ok[2],
                        总收益=round(np.prod([r['eq'] / 100 for r in good]) * 100 if len(good) == 3 else 0, 1),
                        前两段收益=round(np.prod([r['eq'] / 100 for r in rs[:2] if r]) * 100, 1),
                        每天=round(np.mean([r['day'] for r in good]), 2) if good else 0,
                        最差PF=round(min((r['pf'] for r in good), default=0), 2), 最大回撤=round(max((r['dd'] for r in good), default=0)),
                        **{SEGN[s]: (f"{r['n']}笔 每天{r['day']:.2f} 胜{r['win']:.0%} PF{r['pf']:.2f} 组合{r['eq']:.0f} 撤{r['dd']:.0f}%" if r else '无') for s, r in enumerate(rs)}))
    S = pd.DataFrame(out).sort_values(['过关', '总收益'], ascending=False); S.to_csv(HERE + '/多张挂单_结果.csv', index=False)
    pd.set_option('display.width', 500); pd.set_option('display.max_colwidth', 70)
    cols = ['反弹', '等', '离低点', '出场', '每天', '最差PF', '最大回撤', '总收益'] + SEGN
    txt = [__doc__, f'试了 {len(S)} 组，三段都过关 {S.过关.sum()} 组',
           '\n三段都过关，按三段收益相乘排：', S[S.过关].head(25)[cols].to_string(index=False),
           '\n三段都过关，按每天单数排：', S[S.过关].sort_values('每天', ascending=False).head(15)[cols].to_string(index=False),
           '\n只用前两段挑（前两段收益最高 10 组），看第三段：', S[S.前两段过].sort_values('前两段收益', ascending=False).head(10)[cols + ['第三段过']].to_string(index=False)]
    t = '\n'.join(txt); print(t); open(HERE + '/多张挂单_输出.txt', 'w').write(t)


if __name__ == '__main__':
    main()
