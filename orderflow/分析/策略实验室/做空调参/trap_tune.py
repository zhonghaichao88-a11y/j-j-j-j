"""多头摊平做空：调参数（反弹多少再空、挂单等多久、离 24h 低点多远才空、止损、出场），找最合适的一组。
信号和程序一样：24h 跌超 5%、持仓量 24h 涨超 5%、资金费率 > 0、散户多空比 7 天 z > 0（币安，2021-12 ~ 2026-09，约 200 个币）。
参数格子（事先定好）：
  反弹再空：0（市价）/ 0.5% / 1% / 1.5% / 2% / 2.5% / 3%；挂单等：30 / 60 / 120 分钟
  离 24h 低点：不限 / >1% / >2% / >3% / >4%
  出场：原版（被清洗就平）止损 3% / 5% / 8%，48h；ATR 止盈 2 倍止损 3 倍 48h
  大盘：不分 / 只在熊市
模拟和程序一样：有仓位或有挂单在等时，新信号不理；离低点不够的信号跳过后冷却 1 小时；挂单到时没成交就撤。
挑法：三段（2021-12~2023-12 / 2024-01~2025-03 / 2025-04~2026-09）每段 ≥30 笔、PF ≥ 1.25、胜率 ≥ 45%、组合赚钱、前后两半 PF ≥ 1，
  过关的按"三段组合收益相乘"排（单多又稳的排前面）；再看最好那组旁边的参数是不是也好（不是碰巧）；
  另外做一次"只用前两段挑、第三段检验"。"""
import os, sys, numpy as np, pandas as pd
from multiprocessing import Pool
sys.path.insert(0, '/home/user/ext/long/lab'); sys.path.insert(0, '/home/user/j-j-j-j/orderflow/分析/策略实验室/NFI信号对比')
import data
from sim import TAKER, MAKER, SLIP, STOP_SLIP
HERE = os.path.dirname(os.path.abspath(__file__)); N = '/home/user/ext/nfisig'
OUT = f'{N}/trap_tune2.parquet'
ROOTS = ['/home/user/ext/oos/f5', '/home/user/ext/oos/f6', '/home/user/ext/long']
BOUNCE = [0.0, 0.005, 0.01, 0.015, 0.02, 0.025, 0.03]; WAIT = [6, 12, 24]
EXITS = {'原版 止损3%': 0.03, '原版 止损5%': 0.05, '原版 止损8%': 0.08, 'ATR止盈2倍止损3倍': None}


def job(a):
    root, c = a
    data.ROOT = root
    if not os.path.exists(f'{root}/met/{c}.parquet'): return None
    try:
        df = data.load(c)
    except Exception as e:
        print('ERR', c, e, flush=True); return None
    if len(df) < 20000 or df.oi.notna().sum() < 10000 or df.ls.notna().sum() < 10000: return None
    import talib
    O, H, L, C = (df[x].values.astype(float) for x in ('o', 'h', 'l', 'c'))
    r24 = df.c.pct_change(288); oi24 = df.oi / df.oi.shift(288) - 1
    lsh = df.ls.iloc[11::12]; lz = (lsh - lsh.rolling(168, min_periods=48).mean()) / lsh.rolling(168, min_periods=48).std()
    lsz = lz.reindex(df.index, method='ffill')
    sig = np.flatnonzero(((r24 < -0.05) & (oi24 > 0.05) & (df.fund > 0) & (lsz > 0)).fillna(False).values)
    if not len(sig): return None
    r60 = df.c.pct_change(12).values; oi60 = (df.oi / df.oi.shift(12) - 1).values
    flush = (r60 < -0.02) & (oi60 < -0.03)
    k = df[['h', 'l', 'c']].copy(); k.attrs = {}; k.index = pd.to_datetime(k.index, unit='ms')
    k1 = k.resample('1h', label='left', closed='left').agg({'h': 'max', 'l': 'min', 'c': 'last'}).dropna()
    s = pd.Series(talib.ATR(k1.h.values, k1.l.values, k1.c.values, 14), index=(k1.index + pd.Timedelta('1h')).values.astype('datetime64[ms]').astype(np.int64))
    atr1 = s.reindex(df.index, method='ffill').values
    lo24 = df.l.rolling(288).min().values
    n = len(C); T5 = df.index.values.astype(np.int64)
    sig = sig[sig + 2 < n]
    dist = C[sig] / lo24[sig] - 1

    def outcome(i, j0, e, fee0, sl):
        fee = fee0; a = atr1[i]
        if sl is None:
            if not (a > 0): return None
            st, tg = e + 3 * a, e - 2 * a
        else:
            st, tg = e * (1 + sl), None
        end = min(j0 + 576 - 1, n - 1); xp = None
        for j in range(j0, end + 1):
            if H[j] >= st:
                xp = max(O[j], st) * (1 + STOP_SLIP); fee += TAKER; break
            if j > j0 and tg is not None and L[j] <= tg:
                xp = tg; fee += MAKER; break
            if sl is not None and j > j0 and flush[j]:
                xp = C[j] * (1 + SLIP); fee += TAKER; break
        if xp is None:
            j = end; xp = C[j] * (1 + SLIP); fee += TAKER
        return j, -(xp / e - 1) - fee

    rows = []
    for bx in BOUNCE:
        for w in (WAIT if bx > 0 else [0]):
            # 每个信号挂单的成交位置（和后面怎么走无关，先算好）
            fill = {}
            for i in sig:
                if bx == 0:
                    fill[i] = (i + 1, O[i + 1] * (1 - SLIP), TAKER)
                else:
                    px = C[i] * (1 + bx); fill[i] = None
                    for j in range(i + 1, min(i + 1 + w, n)):
                        if H[j] >= px: fill[i] = (j, max(O[j], px), MAKER); break
            cache = {}
            for dn in (0, 0.01, 0.02, 0.03, 0.04):
                for ex, sl in EXITS.items():
                    # 和程序一样一根一根走：有仓位 / 有挂单等着时新信号不理；离低点不够的信号跳过并冷却 1 小时
                    free_at = -1; skip_until = -1
                    for q, i in enumerate(sig):
                        if i <= free_at or T5[i] < skip_until: continue
                        if dn and not (dist[q] > dn):
                            skip_until = T5[i] + 3_600_000; continue
                        f = fill[i]
                        if f is None:
                            free_at = i + w; continue            # 挂单没成交，等到撤单才接新信号
                        key = (i, ex)
                        if key not in cache: cache[key] = outcome(i, f[0], f[1], f[2], sl)
                        r = cache[key]
                        if r is None: continue
                        jx, ret = r; free_at = jx
                        rows.append((c, bx, w, dn, ex, int(T5[i]), int(T5[f[0]]), int(T5[jx]), ret))
    return pd.DataFrame(rows, columns=['coin', 'bounce', 'wait', 'dmin', 'exit', 't', 't_in', 't_out', 'ret']) if rows else None


def main():
    if not os.path.exists(OUT):
        jobs = [(r, f[:-8]) for r in ROOTS for f in sorted(os.listdir(f'{r}/k')) if f.endswith('.parquet')]
        with Pool(4) as p:
            res = [x for x in p.map(job, jobs, chunksize=1) if x is not None]
        D = pd.concat(res, ignore_index=True)
        D = D.drop_duplicates(['coin', 'bounce', 'wait', 'dmin', 'exit', 't'])        # 几个数据目录同一个币同一段时间只算一次
        D.to_parquet(OUT)
    D = pd.read_parquet(OUT)
    D = D[(D.t >= pd.Timestamp('2021-12-01').value // 10**6) & (D.t < pd.Timestamp('2026-10-01').value // 10**6)]
    D['seg'] = np.select([D.t < pd.Timestamp('2024-01-01').value // 10**6, D.t < pd.Timestamp('2025-04-01').value // 10**6], [0, 1], 2)
    R = pd.read_parquet(f'{N}/btc_regime.parquet'); R['day'] = R.day.values.astype('datetime64[ms]').astype(np.int64)
    D['day'] = (D.t // 86_400_000 * 86_400_000).astype(np.int64); D = D.merge(R, on='day', how='left')
    D['d'] = ((D.t_out - D.t_in) // 300_000 + 1).astype(np.int64)
    D = D.sort_values('t_in').reset_index(drop=True)
    D['cid'] = D.coin.astype('category').cat.codes.astype(np.int64); nc = int(D.cid.max()) + 1
    from itemsets import evaluate
    DAYS = [761, 455, 548]; SEGN = ['2021-12~2023-12', '2024-01~2025-03', '2025-04~2026-09']
    out = []
    for (bx, w, dn, ex), g in D.groupby(['bounce', 'wait', 'dmin', 'exit']):
        if True:
            for rn in ('不分大盘', '熊'):
                m = g
                if rn == '熊': m = m[m.bull == False]
                rs = []
                for s in range(3):
                    x = m[m.seg == s]
                    if not len(x): rs.append(None); continue
                    n_, win, gp, gl, p1, l1, p2, l2, eq, dd = evaluate(x.t_in.values, x.cid.values, x.ret.values.astype(float), x.d.values, nc)
                    rs.append(dict(n=n_, day=n_ / DAYS[s], win=win / max(n_, 1), pf=gp / gl if gl > 0 else 9.99, eq=eq, dd=-dd * 100,
                                   h1=p1 / l1 if l1 > 0 else 9.99, h2=p2 / l2 if l2 > 0 else 9.99))
                ok = [r is not None and r['n'] >= 30 and r['pf'] >= 1.25 and r['win'] >= 0.45 and r['eq'] > 100 and r['h1'] >= 1 and r['h2'] >= 1 for r in rs]
                good = [r for r in rs if r]
                out.append(dict(反弹=f'{bx:.1%}' if bx else '市价', 等=f'{w * 5}分钟' if w else '-', 离低点=f'>{dn:.0%}' if dn else '不限', 出场=ex, 大盘=rn,
                                过关=all(ok), 前两段过=ok[0] and ok[1], 第三段过=ok[2],
                                总收益=round(np.prod([r['eq'] / 100 for r in good]) * 100 if len(good) == 3 else 0, 1),
                                前两段收益=round(np.prod([r['eq'] / 100 for r in rs[:2] if r]) * 100, 1),
                                每天=round(np.mean([r['day'] for r in good]), 2) if good else 0,
                                最差PF=round(min((r['pf'] for r in good), default=0), 2), 最大回撤=round(max((r['dd'] for r in good), default=0)),
                                **{SEGN[s]: (f"{r['n']}笔 每天{r['day']:.2f} 胜{r['win']:.0%} PF{r['pf']:.2f} 组合{r['eq']:.0f} 撤{r['dd']:.0f}%" if r else '无') for s, r in enumerate(rs)}))
    S = pd.DataFrame(out).sort_values(['过关', '总收益'], ascending=False); S.to_csv(HERE + '/结果.csv', index=False)
    pd.set_option('display.width', 500); pd.set_option('display.max_colwidth', 70)
    cols = ['反弹', '等', '离低点', '出场', '大盘', '每天', '最差PF', '最大回撤', '总收益'] + SEGN
    print(f'币 {D.coin.nunique()}，试了 {len(S)} 组，三段都过关 {S.过关.sum()} 组')
    print('\n现在的 A（反弹2% 等60分钟 离低点>2% 原版止损5%）：')
    print(S[(S.反弹 == '2.0%') & (S.等 == '60分钟') & (S.离低点 == '>2%') & (S.出场 == '原版 止损5%')][cols].to_string(index=False))
    print('\n三段都过关，按三段收益相乘排：'); print(S[S.过关].head(25)[cols].to_string(index=False))
    print('\n三段都过关，按每天单数排：'); print(S[S.过关].sort_values('每天', ascending=False).head(15)[cols].to_string(index=False))
    W = S[S.前两段过].sort_values('前两段收益', ascending=False).head(10)
    print('\n只用前两段挑（前两段收益最高的 10 组），看第三段：'); print(W[cols + ['第三段过']].to_string(index=False))


if __name__ == '__main__':
    main()
