"""扫止损收回 + 5 笔补仓，用币安 5 分钟数据复测（近似版）：
币安K线没有逐价位成交，只有每根的主动买/卖量 → 每根K线的成交都记在收盘价那一格（delta、CVD、成交量都对；
"昨天成交最多的价位/价值区"是近似的，关键位里的昨日高低点、VWAP、开盘区间、整数关口不受影响）。
信号、补仓规则和之前完全一样：5 分钟、时段过滤、3 根K线内挂单成交；补仓 -2/-4/-6/-8%，①1:1:1:1:1 ②1:1:2:2:4，均价止盈 2%，离第一笔 11% 止损，最多 72 小时。
补仓、止盈止损按 5 分钟K线判断（之前是 1 分钟），同一根先止损 → 补仓 → 没补才止盈。随机进场对照（同样补仓）。
数据：2022-23 年 38 个币，2024-01 ~ 2026-09 111 个币。"""
import sys, os, numpy as np, pandas as pd
from multiprocessing import Pool
sys.path.insert(0, '/home/user/j-j-j-j/orderflow'); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from of_core import Bar
from of_playbook import Playbook
from sweep_check import dca, LV, CFGS, TP, SL, HOLD
from pb_full import MAKER


def job(a):
    root, c = a
    f = f'{root}/k/{c}.parquet'
    if not os.path.exists(f):
        return None
    k = pd.read_parquet(f).sort_values('ts').drop_duplicates('ts')
    if len(k) < 3000:
        return None
    ts = k.ts.values.astype(np.int64); o, h, l, cl = (k[x].values.astype(float) for x in ('open', 'high', 'low', 'close'))
    qv = k.quote_volume.values.astype(float); bq = k.taker_buy_quote_volume.values.astype(float)
    row = float(np.nanmedian(cl[:2000])) * 0.0003
    pb = Playbook(row, {'max_trades_day': 10**9, 'max_losses_day': 10**9})
    L = []
    for i in range(len(ts) - 4):
        if not (cl[i] > 0):
            continue
        b = Bar(t=int(ts[i]), o=o[i], h=h[i], l=l[i], c=cl[i], closed=True)
        buy = bq[i] / cl[i]; sell = max(qv[i] - bq[i], 0) / cl[i]
        b.rows[int(np.floor(cl[i] / row))] = [sell, buy]
        for sg in pb.on_bar(b):
            if sg.kind != 'pb_sweep':
                continue
            for j in range(i + 1, i + 4):                  # 3 根K线内碰到挂单价才成交
                if (l[j] <= sg.entry) if sg.side > 0 else (h[j] >= sg.entry):
                    L.append((j, min(o[j], sg.entry) if sg.side > 0 else max(o[j], sg.entry), float(sg.side))); break
    if not L:
        return None
    om = (ts // 60000).astype(np.int64)                     # dca() 按分钟算时间，5 分钟一根
    i0s = np.array([x[0] for x in L], np.int64); ents = np.array([x[1] for x in L]); sides = np.array([x[2] for x in L])
    rng = np.random.default_rng(abs(hash(c + root)) % 2**32)
    out = {}
    for nm, wt in CFGS.items():
        r, t0, t1, lg = dca(om, o, h, l, cl, i0s, ents, sides, LV, wt, TP, SL, HOLD)
        ri = np.sort(rng.integers(0, len(om) - 1, len(L) * 2))
        rr, rt0, _, _ = dca(om, o, h, l, cl, ri, o[ri], rng.choice(sides, len(ri)), LV, wt, TP, SL, HOLD)
        out[nm] = (pd.DataFrame({'coin': c, 't_in': t0 * 60000, 't_out': t1 * 60000, 'ret': r}), pd.DataFrame({'t_in': rt0 * 60000, 'ret': rr}))
    return out


def pf(x):
    x = np.asarray(x); n = -x[x < 0].sum(); return x[x > 0].sum() / n if n > 0 else np.nan


if __name__ == '__main__':
    import sweep_check as SC
    jobs = [('/home/user/ext/oos/f5', c) for c in open('/home/user/ext/oos/flush_old_coins.txt').read().split()] + \
           [('/home/user/ext/long', os.path.basename(f)[:-8]) for f in sorted(os.listdir('/home/user/ext/long/k')) if f.endswith('.parquet')]
    jobs = [(r, c if not c.endswith('.parquet') else c[:-8]) for r, c in jobs]
    with Pool(4) as p:
        res = [x for x in p.map(job, jobs, chunksize=2) if x]
    for nm in CFGS:
        T = pd.concat([x[nm][0] for x in res]); R = pd.concat([x[nm][1] for x in res])
        for X in (T, R):
            y = pd.to_datetime(X.t_in, unit='ms').dt.year
            X['段'] = np.where(y <= 2023, '2022-23', np.where(y == 2024, '2024', np.where(y == 2025, '2025', '2026')))
        print(f'\n{nm}：')
        for sg in ('2022-23', '2024', '2025', '2026'):
            a, b = T[T.段 == sg].ret, R[R.段 == sg].ret
            print(f'  {sg}: {len(a)}笔 胜{(a > 0).mean():.0%} PF{pf(a):.2f}（随机 {pf(b):.2f}）')
        for sg, (a0, b0) in (('2022-23', ('2022-01-01', '2024-01-01')), ('2024~2026-09', ('2024-01-01', '2026-10-01'))):
            X = T[(T.t_in >= pd.Timestamp(a0).value // 10**6) & (T.t_in < pd.Timestamp(b0).value // 10**6)].assign(src='扫止损补仓')
            r = SC.port(X)
            print(f'  组合 {sg}: 100U → {r["final"]}U，回撤 {r["dd"]}%，{r["n"]} 单')
