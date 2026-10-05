"""多头摊平做空：补数据（2021-12 ~ 2023-12 共 140 多个币 + 2024-01 ~ 2026-09 111 个币）重测，
并解决"空在低位"：加进场位置（等反弹再空）、位置过滤、趋势指标（ADX/DMI、EMA、MA200、MACD、SAR、高低点结构）。

信号（和程序一样）：24 小时跌超 5%、持仓量 24 小时涨超 5%、资金费率 > 0、散户多空比 7 天 z > 0。
进场：市价（下一根开盘）/ 反弹 1% 挂空 / 反弹 2% 挂空（信号后 12 根 5 分钟内价格涨到 信号收盘×(1+x) 才成交）
出场：原版（止损 5%、48 小时、多头被清洗＝1 小时跌超 2% 且持仓量 1 小时降超 3% 就平）
      / 原版但不看清洗 / ATR（1 小时 ATR14）止盈 2 倍止损 3 倍 48h / 止盈 3% 止损 5% 48h
过滤（每类一个，含"不用"）：
  趋势（4 小时K线，用已收盘的）：ADX>25 且 -DI>+DI / EMA20<EMA60 / 收盘<MA200 / MACD<0 / SAR 在价格上方 / 高点降低且低点降低（最近 6 根 vs 前 6 根）
  位置：离 24 小时最低点 >2% / 1 小时 RSI14 >30 / 1 小时布林 %B >0.2
  大盘：不分 / 熊（BTC 昨收在 200 天均线下方）/ 牛；币：NFI 头部币 / 全部
三段（每段都要过）：2021-12 ~ 2023-12 / 2024-01 ~ 2025-03 / 2025-04 ~ 2026-09
过关：每段 ≥30 笔、PF ≥ 1.3、胜率 ≥ 45%、组合（10% 仓位、最多 10 单）赚钱、前后两半 PF ≥ 1。"""
import os, sys, numpy as np, pandas as pd
from multiprocessing import Pool
import talib
sys.path.insert(0, '/home/user/ext/long/lab'); sys.path.insert(0, '/home/user/j-j-j-j/orderflow/分析/策略实验室/NFI信号对比')
import data
from sim import TAKER, MAKER, SLIP, STOP_SLIP
HERE = os.path.dirname(os.path.abspath(__file__)); N = '/home/user/ext/nfisig'
OUT = '/home/user/ext/nfisig/trap_redo.parquet'
ROOTS = ['/home/user/ext/oos/f5', '/home/user/ext/oos/f6', '/home/user/ext/long']
ENTRIES = {'市价': 0.0, '反弹1%再空': 0.01, '反弹2%再空': 0.02}
EXITS = ['原版（被清洗就平）', '原版不看清洗', 'ATR止盈2倍止损3倍', '止盈3%止损5%']


def tf_frame(df, rule):
    k = df[['o', 'h', 'l', 'c']].copy(); k.attrs = {}; k.index = pd.to_datetime(k.index, unit='ms')
    return k.resample(rule, label='left', closed='left').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}).dropna()


def back(df, k, rule, arr):
    """大周期K线收盘后的值，对齐到 5 分钟（收盘时刻之后才能用）"""
    s = pd.Series(arr, index=(k.index + pd.Timedelta(rule)).values.astype('datetime64[ms]').astype(np.int64))
    return s.reindex(df.index, method='ffill').values


def job(a):
    root, c = a
    data.ROOT = root
    if not os.path.exists(f'{root}/met/{c}.parquet'): return None
    try:
        df = data.load(c)
    except Exception as e:
        print('ERR', c, e, flush=True); return None
    if len(df) < 20000 or df.oi.notna().sum() < 10000 or df.ls.notna().sum() < 10000: return None
    O, H, L, C = (df[x].values.astype(float) for x in ('o', 'h', 'l', 'c'))
    r24 = df.c.pct_change(288); oi24 = df.oi / df.oi.shift(288) - 1
    lsh = df.ls.iloc[11::12]; lz = (lsh - lsh.rolling(168, min_periods=48).mean()) / lsh.rolling(168, min_periods=48).std()
    lsz = lz.reindex(df.index, method='ffill')
    sig = np.flatnonzero(((r24 < -0.05) & (oi24 > 0.05) & (df.fund > 0) & (lsz > 0)).fillna(False).values)
    if not len(sig): return None
    r60 = df.c.pct_change(12).values; oi60 = (df.oi / df.oi.shift(12) - 1).values
    flush = (r60 < -0.02) & (oi60 < -0.03)
    # ---- 1 小时、4 小时指标
    k1 = tf_frame(df, '1h'); h1c = k1.c.values
    atr1 = back(df, k1, '1h', talib.ATR(k1.h.values, k1.l.values, h1c, 14))
    rsi1 = back(df, k1, '1h', talib.RSI(h1c, 14))
    up, mid, lo = talib.BBANDS(h1c, 20, 2, 2); pb1 = back(df, k1, '1h', (h1c - lo) / (up - lo))
    k4 = tf_frame(df, '4h'); h4, l4, c4 = k4.h.values, k4.l.values, k4.c.values
    adx = talib.ADX(h4, l4, c4, 14); pdi = talib.PLUS_DI(h4, l4, c4, 14); mdi = talib.MINUS_DI(h4, l4, c4, 14)
    ema20, ema60, ma200 = talib.EMA(c4, 20), talib.EMA(c4, 60), talib.SMA(c4, 200)
    macd = talib.MACD(c4, 12, 26, 9)[0]; sar = talib.SAR(h4, l4)
    hh = pd.Series(h4); ll = pd.Series(l4)
    lhll = ((hh.rolling(6).max() < hh.shift(6).rolling(6).max()) & (ll.rolling(6).min() < ll.shift(6).rolling(6).min())).values
    F = {'ADX>25且-DI>+DI': (adx > 25) & (mdi > pdi), 'EMA20<EMA60': ema20 < ema60, '收盘<MA200': c4 < ma200,
         'MACD<0': macd < 0, 'SAR在上方': sar > c4, '高低点都降低': lhll}
    T = {k: back(df, k4, '4h', v.astype(float)) for k, v in F.items()}
    lo24 = df.l.rolling(288).min().values
    loc = {'离24h低点>2%': C > lo24 * 1.02, '1h RSI>30': rsi1 > 30, '1h 布林%B>0.2': pb1 > 0.2}
    n = len(C); rows = []
    for en, bx in ENTRIES.items():
        for ex in EXITS:
            busy = -1
            for i in sig:
                if i <= busy or i + 2 >= n: continue
                # 进场
                if bx == 0:
                    j0, e, fee = i + 1, O[i + 1] * (1 - SLIP), TAKER
                else:
                    px = C[i] * (1 + bx); j0 = None
                    for j in range(i + 1, min(i + 13, n)):
                        if H[j] >= px: j0 = j; break
                    if j0 is None: continue
                    e, fee = max(O[j0], px), MAKER
                a = atr1[i]
                if ex.startswith('ATR'):
                    if not (a > 0): continue
                    st, tg = e + 3 * a, e - 2 * a
                elif ex == '止盈3%止损5%':
                    st, tg = e * 1.05, e * 0.97
                else:
                    st, tg = e * 1.05, None
                end = min(j0 + 576 - 1, n - 1); xp = None
                for j in range(j0, end + 1):
                    if H[j] >= st:
                        xp = max(O[j], st) * (1 + STOP_SLIP); fee += TAKER; break
                    if j > j0 and tg is not None and L[j] <= tg:
                        xp = tg; fee += MAKER; break
                    if ex == '原版（被清洗就平）' and j > j0 and flush[j]:
                        xp = C[j] * (1 + SLIP); fee += TAKER; break
                if xp is None:
                    j = end; xp = C[j] * (1 + SLIP); fee += TAKER
                ret = -(xp / e - 1) - fee
                rows.append((c, en, ex, int(df.index[i]), int(df.index[j0]), int(df.index[j]), ret,
                             *[bool(T[k][i] > 0.5) for k in F], *[bool(v[i]) for v in loc.values()]))
                busy = j
    cols = ['coin', 'entry', 'exit', 't', 't_in', 't_out', 'ret'] + list(F) + list(loc)
    return pd.DataFrame(rows, columns=cols) if rows else None


def main():
    if not os.path.exists(OUT):
        jobs = [(r, f[:-8]) for r in ROOTS for f in sorted(os.listdir(f'{r}/k')) if f.endswith('.parquet')]
        with Pool(4) as p:
            res = [x for x in p.map(job, jobs, chunksize=1) if x is not None]
        pd.concat(res, ignore_index=True).to_parquet(OUT)
    D = pd.read_parquet(OUT)
    D['seg'] = np.select([D.t < pd.Timestamp('2024-01-01').value // 10**6, D.t < pd.Timestamp('2025-04-01').value // 10**6],
                         ['2021-12~2023-12', '2024-01~2025-03'], '2025-04~2026-09')
    R = pd.read_parquet(f'{N}/btc_regime.parquet'); R['day'] = R.day.values.astype('datetime64[ms]').astype(np.int64)
    D['day'] = (D.t // 86_400_000 * 86_400_000).astype(np.int64); D = D.merge(R, on='day', how='left')
    D['d'] = ((D.t_out - D.t_in) // 300_000 + 1).astype(np.int64)
    D = D.sort_values('t_in').reset_index(drop=True)
    D['cid'] = D.coin.astype('category').cat.codes.astype(np.int64); nc = int(D.cid.max()) + 1
    from itemsets import evaluate
    TOP = set(open(f'{N}/top.txt').read().split())
    SEGS = ['2021-12~2023-12', '2024-01~2025-03', '2025-04~2026-09']; DAYS = {SEGS[0]: 761, SEGS[1]: 455, SEGS[2]: 548}
    trend = ['不用', 'ADX>25且-DI>+DI', 'EMA20<EMA60', '收盘<MA200', 'MACD<0', 'SAR在上方', '高低点都降低']
    loc = ['不用', '离24h低点>2%', '1h RSI>30', '1h 布林%B>0.2']
    reg = {'不分大盘': np.ones(len(D), bool), '熊': (D.bull == False).values, '牛': (D.bull == True).values}
    uni = {'头部币': D.coin.isin(TOP).values, '全部币': np.ones(len(D), bool)}
    out = []
    for (en, ex), g in D.groupby(['entry', 'exit']):
        gi = g.index.values
        for tn in trend:
            tm = np.ones(len(D), bool) if tn == '不用' else D[tn].values
            for ln in loc:
                lm = np.ones(len(D), bool) if ln == '不用' else D[ln].values
                for rn, rm in reg.items():
                    for un, um in uni.items():
                        m = gi[tm[gi] & lm[gi] & rm[gi] & um[gi]]
                        rs = []; ok = True
                        for s in SEGS:
                            ix = m[D.seg.values[m] == s]
                            if len(ix) == 0: rs.append(None); ok = False; continue
                            n_, win, gp, gl, p1, l1, p2, l2, eq, dd = evaluate(D.t_in.values[ix], D.cid.values[ix], D.ret.values[ix].astype(float), D.d.values[ix], nc)
                            r = dict(笔=n_, 每天=n_ / DAYS[s], 胜=win / max(n_, 1), PF=gp / gl if gl > 0 else 9.99, 组合=eq, 撤=dd * 100,
                                     前=p1 / l1 if l1 > 0 else 9.99, 后=p2 / l2 if l2 > 0 else 9.99)
                            rs.append(r); ok &= n_ >= 30 and r['PF'] >= 1.3 and r['胜'] >= 0.45 and eq > 100 and r['前'] >= 1 and r['后'] >= 1
                        good = [r for r in rs if r]
                        out.append(dict(进场=en, 出场=ex, 趋势=tn, 位置=ln, 大盘=rn, 币=un, 过关=ok,
                                        最差PF=round(min((r['PF'] for r in good), default=0), 2),
                                        平均每天=round(np.mean([r['每天'] for r in good]), 2) if good else 0,
                                        **{s: (f"{r['笔']}笔 每天{r['每天']:.2f} 胜{r['胜']:.0%} PF{r['PF']:.2f} 组合{r['组合']:.0f} 撤{r['撤']:.0f}% 半{r['前']:.1f}/{r['后']:.1f}" if r else '无') for s, r in zip(SEGS, rs)}))
    S = pd.DataFrame(out).sort_values(['过关', '平均每天'], ascending=False); S.to_csv(HERE + '/结果.csv', index=False)
    pd.set_option('display.width', 450); pd.set_option('display.max_colwidth', 80)
    print(f'币 {D.coin.nunique()}（三段各 {[D[D.seg == s].coin.nunique() for s in SEGS]}），试了 {len(S)} 种，过关 {S.过关.sum()} 种')
    cur = S[(S.进场 == '市价') & (S.出场 == '原版（被清洗就平）') & (S.趋势 == '不用') & (S.位置 == '不用') & (S.币 == '全部币')]
    print('\n程序现在的做法：'); print(cur[['大盘', '最差PF', '平均每天'] + SEGS].to_string(index=False))
    print('\n过关的（按每天单数排）：'); print(S[S.过关].head(30).drop(columns=['过关']).to_string(index=False))
    print('\n没过关里最接近的：'); print(S[~S.过关 & (S.平均每天 >= 0.2)].sort_values('最差PF', ascending=False).head(12).drop(columns=['过关']).to_string(index=False))


if __name__ == '__main__':
    main()
