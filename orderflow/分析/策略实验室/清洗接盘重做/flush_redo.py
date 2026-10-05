"""清洗接盘：补数据（2021-12 ~ 2023-12 币安上那时有合约的所有币）后，照"大跌抄底"的办法全部重测。

数据：f5（原来 38 个币，2022-23）+ f6（新补的币，2021-12 ~ 2023-12）+ long（111 个币，2024-01 ~ 2026-09）。
信号（和程序一样，事先定好几档）：
  原版：1 小时跌超 2%、持仓量 1 小时降超 5%、币安现货 1 小时主动买比卖多 5% → 信号K线收盘价挂买单，5 分钟内成交
  跌 3%：同上但跌幅 3%
  按波动：1 小时跌幅 ÷ 这个币过去 7 天 1 小时涨跌的波动 < -3 / -4（其余两个条件不变）
出场：原版（止损 3 倍跌幅、拿 12 小时）/ 原版止损拿 24 小时 / ATR（1 小时 ATR14）1倍止盈3倍止损 48h、0.5/2 24h、2/4 72h /
      固定 止盈2%止损5% 24h、3%/8% 48h
过滤（单个、两个、三个都试，每类有"不用"）：
  大盘：不分 / 牛（BTC 昨收在 200 天均线上方）/ 熊
  币：NFI 头部币名单 / 全部
  订单流（一个或两个一起）：持仓量 24h 涨幅 ≤1.67%（程序里默认开的那个）/ 持仓 24h 涨 >5% / 资金费率 >0.01% / <0 / 散户多空比 z >1 / < -1
三段（每段都要过）：2021-12 ~ 2023-12 / 2024-01 ~ 2025-03 / 2025-04 ~ 2026-09
过关：每段 ≥30 笔、PF ≥ 1.3、胜率 ≥ 50%（清洗接盘本来就是赚多赔少型，胜率 5 成左右）、组合（10% 仓位、最多 10 单）赚钱、前后两半 PF ≥ 1。"""
import os, sys, itertools, numpy as np, pandas as pd
from multiprocessing import Pool
sys.path.insert(0, '/home/user/ext/long/lab'); sys.path.insert(0, '/home/user/j-j-j-j/orderflow/分析/策略实验室/NFI信号对比')
import data, sim
from data import flow
HERE = os.path.dirname(os.path.abspath(__file__)); N = '/home/user/ext/nfisig'
OUT = '/home/user/ext/nfisig/flush_redo.parquet'
ROOTS = ['/home/user/ext/oos/f5', '/home/user/ext/oos/f6', '/home/user/ext/long']
SIGS = {'原版': ('pct', 0.02), '跌3%': ('pct', 0.03), '按波动3': ('vn', 3.0), '按波动4': ('vn', 4.0)}
EXITS = ['原版12h', '原版24h', 'ATR1/3 48h', 'ATR0.5/2 24h', 'ATR2/4 72h', '止盈2%止损5% 24h', '止盈3%止损8% 48h']


def job(a):
    root, c = a
    data.ROOT = root
    if not os.path.exists(f'{root}/k/{c}.parquet') or not os.path.exists(f'{root}/met/{c}.parquet'): return None
    try:
        df = data.load(c)
    except Exception as e:
        print('ERR', c, e, flush=True); return None
    if len(df) < 20000 or df.oi.notna().sum() < 10000 or df.sqv.notna().sum() < 10000: return None
    df['r60'] = df.c.pct_change(12); df['oi60'] = df.oi.pct_change(12); df['sf60'] = flow(df.sbq, df.sqv, 12)
    hr = df.c.iloc[11::12]; r1 = hr.pct_change(); vol = r1.rolling(168, min_periods=48).std()
    df['vol1'] = vol.reindex(df.index, method='ffill')
    hk = pd.DataFrame({'h': df.h, 'l': df.l, 'c': df.c}); hk['hr'] = df.index // 3_600_000
    g = hk.groupby('hr').agg(h=('h', 'max'), l=('l', 'min'), c=('c', 'last'))
    tr = np.maximum(g.h - g.l, np.maximum((g.h - g.c.shift()).abs(), (g.l - g.c.shift()).abs()))
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean().shift(1)          # 上一根已收盘的 1 小时 ATR14
    df['atr'] = atr.reindex(df.index // 3_600_000).values
    df['oi24'] = df.oi / df.oi.shift(288) - 1
    ls = df.ls; df['lsz'] = (ls - ls.rolling(2016, min_periods=576).mean()) / ls.rolling(2016, min_periods=576).std()
    base = (df.oi60 < -0.05) & (df.sf60 > 0.05)
    rows = []
    for sn, (typ, x) in SIGS.items():
        m = base & ((df.r60 < -x) if typ == 'pct' else (df.r60 / df.vol1 < -x))
        sig = np.flatnonzero(m.fillna(False).values)
        if not len(sig): continue
        drop = df.r60.abs().values[sig]; px = df.c.values[sig]; at = df.atr.values[sig] / px
        for ex in EXITS:
            if ex.startswith('原版'):
                kw = dict(sd=3 * drop, td=None, hold=144 if ex == '原版12h' else 288)
            elif ex.startswith('ATR'):
                a, b = {'ATR1/3 48h': (1, 3), 'ATR0.5/2 24h': (0.5, 2), 'ATR2/4 72h': (2, 4)}[ex]
                kw = dict(sd=b * at, td=a * at, hold={'ATR1/3 48h': 576, 'ATR0.5/2 24h': 288, 'ATR2/4 72h': 864}[ex])
            else:
                tp, sl, h = {'止盈2%止损5% 24h': (0.02, 0.05, 288), '止盈3%止损8% 48h': (0.03, 0.08, 576)}[ex]
                kw = dict(sd=sl, td=tp, hold=h)
            for r in sim.run(df, sig, 1, kw['sd'], kw['td'], hold=kw['hold'], entry='limit', lpx=px, lbars=1):
                i = df.index.get_loc(r[1])
                rows.append((c, sn, ex, r[1], r[2], r[3], r[5], df.oi24.iat[i], df.fund.iat[i], df.lsz.iat[i]))
    return pd.DataFrame(rows, columns=['coin', 'sig', 'exit', 't', 't_in', 't_out', 'ret', 'oi24', 'fund', 'lsz']) if rows else None


def main():
    if not os.path.exists(OUT):
        jobs = []
        for root in ROOTS:
            for f in sorted(os.listdir(f'{root}/k')):
                if f.endswith('.parquet'): jobs.append((root, f[:-8]))
        with Pool(4) as p:
            res = [x for x in p.map(job, jobs, chunksize=1) if x is not None]
        pd.concat(res, ignore_index=True).to_parquet(OUT)
    D = pd.read_parquet(OUT)
    D = D[D.coin != 'BTC']
    D['seg'] = np.select([D.t < pd.Timestamp('2024-01-01').value // 10**6, D.t < pd.Timestamp('2025-04-01').value // 10**6],
                         ['2021-12~2023-12', '2024-01~2025-03'], '2025-04~2026-09')
    R = pd.read_parquet(f'{N}/btc_regime.parquet'); R['day'] = R.day.values.astype('datetime64[ms]').astype(np.int64)
    D['day'] = (D.t // 86_400_000 * 86_400_000).astype(np.int64); D = D.merge(R, on='day', how='left')
    D['d'] = ((D.t_out - D.t_in) // 300_000 + 1).astype(np.int64)
    D = D.sort_values('t_in').reset_index(drop=True)
    D['cid'] = D.coin.astype('category').cat.codes.astype(np.int64); nc = int(D.cid.max()) + 1
    from itemsets import evaluate
    TOP = set(open(f'{N}/top.txt').read().split())
    SEGS = ['2021-12~2023-12', '2024-01~2025-03', '2025-04~2026-09']
    DAYS = {SEGS[0]: 761, SEGS[1]: 455, SEGS[2]: 548}
    reg = {'不分大盘': np.ones(len(D), bool), '牛': (D.bull == True).values, '熊': (D.bull == False).values}
    uni = {'头部币': D.coin.isin(TOP).values, '全部币': np.ones(len(D), bool)}
    ofb = {'持仓24h不涨': (D.oi24 <= 0.0167).values, '持仓24h涨': (D.oi24 > 0.05).values, '费率正': (D.fund > 0.0001).values,
           '费率负': (D.fund < 0).values, '多空比高': (D.lsz > 1).values, '多空比低': (D.lsz < -1).values}
    same = {('持仓24h不涨', '持仓24h涨'), ('费率正', '费率负'), ('多空比高', '多空比低')}
    of = {'不用': np.ones(len(D), bool), **ofb,
          **{f'{a}+{b}': ofb[a] & ofb[b] for a, b in itertools.combinations(ofb, 2) if (a, b) not in same}}
    out = []
    for (sn, ex), g in D.groupby(['sig', 'exit']):
        gi = g.index.values
        for rn, rm in reg.items():
            for un, um in uni.items():
                for on, om in of.items():
                    m = gi[rm[gi] & um[gi] & om[gi]]
                    rs = []; ok = True
                    for s in SEGS:
                        ix = m[D.seg.values[m] == s]
                        if len(ix) == 0: rs.append(None); ok = False; continue
                        n, win, gp, gl, p1, l1, p2, l2, eq, dd = evaluate(D.t_in.values[ix], D.cid.values[ix], D.ret.values[ix].astype(float), D.d.values[ix], nc)
                        r = dict(笔=n, 每天=n / DAYS[s], 胜=win / max(n, 1), PF=gp / gl if gl > 0 else 9.99, 组合=eq, 撤=dd * 100,
                                 前=p1 / l1 if l1 > 0 else 9.99, 后=p2 / l2 if l2 > 0 else 9.99)
                        rs.append(r); ok &= n >= 30 and r['PF'] >= 1.3 and r['胜'] >= 0.5 and eq > 100 and r['前'] >= 1 and r['后'] >= 1
                    good = [r for r in rs if r]
                    out.append(dict(信号=sn, 出场=ex, 大盘=rn, 币=un, 订单流=on, 过关=ok,
                                    最差PF=round(min((r['PF'] for r in good), default=0), 2),
                                    平均每天=round(np.mean([r['每天'] for r in good]), 2) if good else 0,
                                    **{s: (f"{r['笔']}笔 每天{r['每天']:.2f} 胜{r['胜']:.0%} PF{r['PF']:.2f} 组合{r['组合']:.0f} 撤{r['撤']:.0f}% 半{r['前']:.1f}/{r['后']:.1f}" if r else '无') for s, r in zip(SEGS, rs)}))
    S = pd.DataFrame(out).sort_values(['过关', '平均每天'], ascending=False); S.to_csv(HERE + '/结果.csv', index=False)
    pd.set_option('display.width', 450); pd.set_option('display.max_colwidth', 80)
    print(f'币 {D.coin.nunique()}（三段各 {[D[D.seg == s].coin.nunique() for s in SEGS]}），试了 {len(S)} 种，过关 {S.过关.sum()} 种')
    base = S[(S.信号 == '原版') & (S.出场 == '原版12h') & (S.币 == '全部币') & (S.订单流.isin(['不用', '持仓24h不涨']))]
    print('\n程序现在的设置（原版信号和出场）：'); print(base[['大盘', '订单流', '最差PF', '平均每天'] + SEGS].to_string(index=False))
    print('\n过关的（按每天单数排）：'); print(S[S.过关].head(30).drop(columns=['过关']).to_string(index=False))


if __name__ == '__main__':
    main()
