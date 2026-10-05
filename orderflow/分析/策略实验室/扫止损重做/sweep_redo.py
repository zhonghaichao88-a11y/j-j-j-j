"""扫止损收回：照"大跌抄底"的办法重做一遍（币安 5 分钟近似数据，2022-01 ~ 2026-09）。
信号和程序一样（5 分钟、时段过滤、3 根K线内碰到挂单价才成交；币安没有逐价位成交，所以是近似版，同 sweep_bn.py）。
每个信号试这些搭配，事先定好：
  出场：固定 ①止盈2%止损5%24h ②3%/8%/48h ③5%/11%/72h ④1.5%/3%/12h；
        ATR（1 小时 ATR14，进场前最后一根收盘的）⑤止盈1倍/止损3倍 48h ⑥0.5倍/2倍 24h ⑦2倍/4倍 72h；
        ⑧原来的 5 笔补仓（-2/-4/-6/-8%，1:1:1:1:1，均价止盈2%，离第一笔11%止损，72h）
  大盘：不分 / 牛（BTC 昨收在 200 天均线上方）/ 熊；币：NFI 头部币名单 / 全部；方向：做多 / 做空分开；
  订单流（币安）：不用 / 持仓量24h涨>5% / 跌>5% / 资金费率>0.01% / <0 / 散户多空比z>1 / <-1
过关（同前）：三段（2022-23 / 2024-01~2025-09 / 2025-10~2026-09）每段 ≥30 笔（同币不重叠）、PF ≥ 1.3、胜率 ≥ 60%、
  组合（10% 仓位、最多 10 单）赚钱、前后两半 PF ≥ 1。"""
import sys, os, numpy as np, pandas as pd
from multiprocessing import Pool
from numba import njit
sys.path.insert(0, '/home/user/j-j-j-j/orderflow'); sys.path.insert(0, '/home/user/j-j-j-j/orderflow/分析/策略实验室/实战版完整重测')
sys.path.insert(0, '/home/user/j-j-j-j/orderflow/分析/策略实验室/NFI信号对比')
from of_core import Bar
from of_playbook import Playbook
from pb_full import MAKER, TAKER, SLIP, STOP_SLIP
from sweep_check import dca, LV, CFGS
HERE = os.path.dirname(os.path.abspath(__file__)); N = '/home/user/ext/nfisig'
OUT = '/home/user/ext/nfisig/sweep_redo.parquet'
FIX = {0: (0.02, 0.05, 24), 1: (0.03, 0.08, 48), 2: (0.05, 0.11, 72), 3: (0.015, 0.03, 12)}
ATRX = {4: (1.0, 3.0, 48), 5: (0.5, 2.0, 24), 6: (2.0, 4.0, 72)}
EXN = {0: '止盈2% 止损5% 24h', 1: '止盈3% 止损8% 48h', 2: '止盈5% 止损11% 72h', 3: '止盈1.5% 止损3% 12h',
       4: '止盈1倍ATR 止损3倍ATR 48h', 5: '止盈0.5倍ATR 止损2倍ATR 24h', 6: '止盈2倍ATR 止损4倍ATR 72h', 7: '原来的5笔补仓'}


@njit(cache=True)
def single(om, o, h, l, c, i0s, ents, sides, tpd, sld, hold_min):
    """挂单成交（maker 手续费）在第 i0 根，从这根开始查止损；止盈从下一根开始查（同一根先止损）。tpd/sld 是价格距离"""
    n = len(om); k = len(i0s); res = np.full(k, np.nan); dur = np.full(k, -1, np.int64)
    for q in range(k):
        i0 = i0s[q]; s = sides[q]; e = ents[q]
        if not (tpd[q] > 0 and sld[q] > 0): continue
        tg = e + s * tpd[q]; st = e - s * sld[q]; end = om[i0] + hold_min; ex = -1.0; j = i0; fee = MAKER
        while j < n and om[j] < end:
            if (l[j] <= st) if s > 0 else (h[j] >= st):
                ex = (min(o[j], st) if s > 0 else max(o[j], st)) * (1 - s * STOP_SLIP); fee += TAKER; break
            if j > i0 and ((h[j] >= tg) if s > 0 else (l[j] <= tg)):
                ex = tg; fee += MAKER; break
            j += 1
        if ex < 0:
            if j >= n: continue
            j = j - 1; ex = c[j] * (1 - s * SLIP); fee += TAKER
        res[q] = s * (ex / e - 1) - fee; dur[q] = (om[min(j, n - 1)] - om[i0]) // 5 + 1
    return res, dur


def job(a):
    root, c = a
    f = f'{root}/k/{c}.parquet'
    if not os.path.exists(f): return None
    k = pd.read_parquet(f).sort_values('ts').drop_duplicates('ts')
    if len(k) < 3000: return None
    ts = k.ts.values.astype(np.int64); o, h, l, cl = (k[x].values.astype(float) for x in ('open', 'high', 'low', 'close'))
    qv = k.quote_volume.values.astype(float); bq = k.taker_buy_quote_volume.values.astype(float)
    row = float(np.nanmedian(cl[:2000])) * 0.0003
    pb = Playbook(row, {'max_trades_day': 10**9, 'max_losses_day': 10**9})
    L = []
    for i in range(len(ts) - 4):
        if not (cl[i] > 0): continue
        b = Bar(t=int(ts[i]), o=o[i], h=h[i], l=l[i], c=cl[i], closed=True)
        buy = bq[i] / cl[i]; sell = max(qv[i] - bq[i], 0) / cl[i]
        b.rows[int(np.floor(cl[i] / row))] = [sell, buy]
        for sg in pb.on_bar(b):
            if sg.kind != 'pb_sweep': continue
            for j in range(i + 1, i + 4):
                if (l[j] <= sg.entry) if sg.side > 0 else (h[j] >= sg.entry):
                    L.append((i, j, min(o[j], sg.entry) if sg.side > 0 else max(o[j], sg.entry), float(sg.side))); break
    if not L: return None
    om = (ts // 60000).astype(np.int64)
    sig = np.array([x[0] for x in L], np.int64); i0s = np.array([x[1] for x in L], np.int64)
    ents = np.array([x[2] for x in L]); sides = np.array([x[3] for x in L])
    # 1 小时 ATR14：信号K线时已经收盘的最后一根 1 小时K线
    import talib
    hk = pd.DataFrame({'ts': ts, 'h': h, 'l': l, 'c': cl}); hk['hr'] = hk.ts // 3_600_000
    g = hk.groupby('hr').agg(h=('h', 'max'), l=('l', 'min'), c=('c', 'last'))
    atr = pd.Series(talib.ATR(g.h.values, g.l.values, g.c.values, 14), index=g.index)
    sig_hr = ts[sig] // 3_600_000 - 1                       # 信号K线所在小时的前一小时（已收盘）
    a = atr.reindex(sig_hr).values
    out = pd.DataFrame({'coin': c, 't': ts[i0s], 'side': sides})
    for kk, (tp, sl, hd) in FIX.items():
        r, d = single(om, o, h, l, cl, i0s, ents, sides, ents * tp, ents * sl, hd * 60); out[f'y{kk}'] = r; out[f'd{kk}'] = d
    for kk, (tpx, slx, hd) in ATRX.items():
        r, d = single(om, o, h, l, cl, i0s, ents, sides, a * tpx, a * slx, hd * 60); out[f'y{kk}'] = r; out[f'd{kk}'] = d
    # 补仓：dca() 自带"同币有仓位跳过"，这里每个信号单独算（不跳过），后面统一做不重叠
    ys, ds = [], []
    for q in range(len(i0s)):
        r, t0, t1, lg = dca(om, o, h, l, cl, i0s[q:q + 1], ents[q:q + 1], sides[q:q + 1], LV, CFGS['①1:1:1:1:1'], 0.02, 0.11, 72 * 60)
        ys.append(r[0] if len(r) else np.nan); ds.append((t1[0] - t0[0]) // 5 + 1 if len(r) else -1)
    out['y7'] = ys; out['d7'] = ds
    return out


if __name__ == '__main__':
    if not os.path.exists(OUT):
        jobs = [('/home/user/ext/oos/f5', c) for c in open('/home/user/ext/oos/flush_old_coins.txt').read().split()] + \
               [('/home/user/ext/long', os.path.basename(f)[:-8]) for f in sorted(os.listdir('/home/user/ext/long/k')) if f.endswith('.parquet')]
        with Pool(4) as p:
            res = [x for x in p.map(job, jobs, chunksize=1) if x is not None]
        pd.concat(res, ignore_index=True).to_parquet(OUT)
    D = pd.read_parquet(OUT)
    for kk in EXN: D[f'y{kk}'] = D[f'y{kk}'].astype(float); D[f'd{kk}'] = D[f'd{kk}'].astype(np.int64)
    D['seg'] = np.where(D.t < pd.Timestamp('2024-01-01').value // 10**6, '2022-23',
                        np.where(D.t < pd.Timestamp('2025-10-01').value // 10**6, '2024-01~2025-09', '2025-10~2026-09'))
    D = D[D.t >= pd.Timestamp('2022-01-01').value // 10**6]
    A = pd.read_parquet(f'{N}/attrs_hour.parquet'); D['hour'] = (D.t // 3_600_000 * 3_600_000).astype(np.int64)
    D = D.merge(A, on=['coin', 'hour'], how='left')
    R = pd.read_parquet(f'{N}/btc_regime.parquet'); R['day'] = R.day.values.astype('datetime64[ms]').astype(np.int64)
    D['day'] = (D.t // 86_400_000 * 86_400_000).astype(np.int64); D = D.merge(R, on='day', how='left')
    D = D.sort_values('t').reset_index(drop=True)
    D['cid'] = D.coin.astype('category').cat.codes.astype(np.int64); nc = int(D.cid.max()) + 1
    from itemsets import evaluate
    TOP = set(open(f'{N}/top.txt').read().split())
    SEGS = ['2022-23', '2024-01~2025-09', '2025-10~2026-09']; DAYS = {SEGS[0]: 730, SEGS[1]: 639, SEGS[2]: 365}
    reg = {'不分大盘': np.ones(len(D), bool), '牛': (D.bull == True).values, '熊': (D.bull == False).values}
    uni = {'头部币': D.coin.isin(TOP).values, '全部币': np.ones(len(D), bool)}
    of = {'': np.ones(len(D), bool), '持仓涨': (D.oi24 > 0.05).values, '持仓跌': (D.oi24 < -0.05).values,
          '费率正': (D.fund > 0.0001).values, '费率负': (D.fund < 0).values, '多空比高': (D.lsz > 1).values, '多空比低': (D.lsz < -1).values}
    sdir = {'做多': (D.side > 0).values, '做空': (D.side < 0).values}
    out = []
    for sn, sm in sdir.items():
        for rn, rm in reg.items():
            for un, um in uni.items():
                for on, omk in of.items():
                    m = sm & rm & um & omk
                    for kk in EXN:
                        y, du = D[f'y{kk}'].values, D[f'd{kk}'].values; rs = []; ok = True
                        for s in SEGS:
                            ix = np.where(m & (D.seg == s).values & ~np.isnan(y))[0]
                            if len(ix) == 0: rs.append(None); ok = False; continue
                            n, win, gp, gl, p1, l1, p2, l2, eq, dd = evaluate(D.t.values[ix], D.cid.values[ix], y[ix], du[ix], nc)
                            r = dict(笔=n, 每天=n / DAYS[s], 胜=win / max(n, 1), PF=gp / gl if gl > 0 else 9.99, 组合=eq, 撤=dd * 100,
                                     前=p1 / l1 if l1 > 0 else 9.99, 后=p2 / l2 if l2 > 0 else 9.99)
                            rs.append(r); ok &= n >= 30 and r['PF'] >= 1.3 and r['胜'] >= 0.6 and eq > 100 and r['前'] >= 1 and r['后'] >= 1
                        out.append(dict(方向=sn, 大盘=rn, 币=un, 订单流=on or '不用', 出场=EXN[kk], 过关=ok,
                                        最差PF=round(min((r['PF'] for r in rs if r), default=0), 2),
                                        平均每天=round(np.mean([r['每天'] for r in rs if r]), 2) if any(rs) else 0,
                                        **{s: (f"{r['笔']}笔 每天{r['每天']:.2f} 胜{r['胜']:.0%} PF{r['PF']:.2f} 组合{r['组合']:.0f} 撤{r['撤']:.0f}% 半{r['前']:.1f}/{r['后']:.1f}" if r else '无') for s, r in zip(SEGS, rs)}))
    S = pd.DataFrame(out).sort_values(['过关', '最差PF'], ascending=False); S.to_csv(HERE + '/结果.csv', index=False)
    pd.set_option('display.width', 450); pd.set_option('display.max_colwidth', 80)
    print(f'试了 {len(S)} 种，过关 {S.过关.sum()} 种')
    print(S.head(25).to_string(index=False))
