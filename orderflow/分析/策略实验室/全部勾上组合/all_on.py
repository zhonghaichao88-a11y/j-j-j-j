"""全部打法一起勾、完全按程序规则跑的组合回测。
程序规则：同一个币同时只拿一单（不管哪个打法，先来先开）；所有打法共用"最多同时持仓"10 个；每笔用当时权益的 10%（滚利）。
旧打法：订单流 Detector 全开，5 分钟周期；K线收盘出信号，下一根第一分钟开盘价进场。
  出场 A 原版：信号自己的止损，止盈 2 倍止损距离，最多拿 48 根（4 小时）
  出场 B 像新打法：止损 5%，不设止盈，最多拿 48 小时
新打法：清洗接盘（大盘 + 持仓量过滤）、多头摊平做空，用之前币安 5 分钟长数据回测出来的逐笔结果（111 个币）。
数据（欧易逐笔足迹，旧打法只能用这个）：
  时段 1：2026-04 ~ 09，43 个币（BTC ETH SOL + 40 个山寨币）
  时段 2：2025-10 ~ 2026-03，20 个山寨币
成本：每边吃单 0.05% + 滑点 0.02%；止损再加 0.05%。"""
import sys, os, pickle, numpy as np, pandas as pd
from numba import njit
from multiprocessing import Pool
sys.path.insert(0, '/home/user/j-j-j-j/orderflow')
import of_backtest as B
from of_core import Detector, SIGNAL_NAMES
FEE, SLIP, STOP_SLIP = 0.0005, 0.0002, 0.0005
A20 = 'DOGE XRP ADA AVAX LINK LTC BCH DOT SUI OP ARB APT NEAR FIL AAVE UNI TRX WLD INJ ETC'.split()
B20 = 'ZEC PUMP STRK HYPE PEPE TRUMP BNB ENA TAO ONDO ZRO GRASS VIRTUAL PENGU ICP HBAR XLM SHIB FET WIF'.split()
SETS = {'P1': [('/home/user/ext/of/fp', c) for c in ('BTC', 'ETH', 'SOL')] + [('/home/user/ext/of/fp2', c) for c in A20] + [('/home/user/ext/of/fp_alt2', c) for c in B20],
        'P2': [('/home/user/ext/of/fp_old', c) for c in A20]}
PERIOD = {'P1': ('2026-04-01', '2026-10-01'), 'P2': ('2025-10-01', '2026-04-01')}


@njit(cache=True)
def trade(om, mo, mh, ml, mc, i0, side, sd, fixed, rr, hold_min):
    n = len(om)
    e = mo[i0] * (1 + side * SLIP)
    st = e * (1 - side * (0.05 if fixed else sd))
    tg = np.nan if fixed else e * (1 + side * rr * sd)
    end = om[i0] + hold_min
    j = i0
    while j < n and om[j] < end:
        if (ml[j] <= st) if side > 0 else (mh[j] >= st):
            ex = (min(mo[j], st) if side > 0 else max(mo[j], st)) * (1 - side * STOP_SLIP)
            return side * (ex / e - 1) - 2 * FEE, om[j]
        if tg == tg and ((mh[j] >= tg) if side > 0 else (ml[j] <= tg)):
            return side * (tg / e - 1) - 2 * FEE, om[j]
        j += 1
    j = min(j, n) - 1
    return side * (mc[j] * (1 - side * SLIP) / e - 1) - 2 * FEE, om[j]


def coin(job):
    root, c = job
    d = B.load(root, c)
    om = d['om'].astype(np.int64); mo, mh, ml, mc = (d[k].astype(np.float64) for k in 'ohlc')
    idx = {int(x): i for i, x in enumerate(om)}
    bars, row = B.build_bars(d, 5)
    det = Detector(row, {}, enabled=None)
    rows = []
    for bar in bars:
        for s in det.on_bar(bar):
            st = bar.t // 60000 + 5
            if st not in idx or bar.c <= 0:
                continue
            i0 = idx[st]; sd = abs(bar.c - s.stop) / bar.c
            if sd <= 0:
                continue
            ra, ta = trade(om, mo, mh, ml, mc, i0, float(s.side), sd, False, 2.0, 48 * 5)
            rb, tb = trade(om, mo, mh, ml, mc, i0, float(s.side), sd, True, 0.0, 48 * 60)
            rows.append((c, s.kind, int(om[i0]) * 60000, int(ta) * 60000 + 60000, ra, int(tb) * 60000 + 60000, rb))
    return pd.DataFrame(rows, columns=['coin', 'kind', 't_in', 't_out_a', 'ret_a', 't_out_b', 'ret_b'])


def port(T, cap=10, size=0.10, start=100.0):
    """按程序规则：时间顺序，同币只拿一单、先来先开，最多 cap 单，每笔用当时权益 size"""
    T = T.sort_values('t_in', kind='stable').reset_index(drop=True)
    eq, open_, busy, curve, taken = start, [], {}, [], []
    tin, tout, cn, rt = T.t_in.values, T.t_out.values, T.coin.values, T.ret.values
    import heapq
    for i in range(len(T)):
        while open_ and open_[0][0] <= tin[i]:
            t, k, amt = heapq.heappop(open_)
            eq += amt * rt[k]; curve.append((t, eq)); busy.pop(cn[k], None)
        if cn[i] in busy or len(open_) >= cap:
            continue
        busy[cn[i]] = 1; taken.append(i)
        heapq.heappush(open_, (tout[i], i, eq * size))
    while open_:
        t, k, amt = heapq.heappop(open_); eq += amt * rt[k]; curve.append((t, eq))
    c = pd.Series([e for _, e in curve], index=pd.to_datetime([t for t, _ in curve], unit='ms')) if curve else pd.Series([start])
    dd = float((c / c.cummax() - 1).min()) if len(c) else 0.0
    tk = T.loc[taken]
    r = tk.ret.values
    pf = r[r > 0].sum() / -r[r < 0].sum() if (r < 0).any() else float('inf')
    return dict(final=round(float(c.iloc[-1]), 1), dd=round(dd * 100, 1), n=len(tk), pf=round(float(pf), 2),
                win=round(float((r > 0).mean()) * 100) if len(r) else 0, by=tk.groupby('src').size().to_dict())


if __name__ == '__main__':
    os.makedirs('/home/user/ext/of/allon', exist_ok=True)
    for P, jobs in SETS.items():
        f = f'/home/user/ext/of/allon/{P}.parquet'
        if not os.path.exists(f):
            with Pool(4) as p:
                pd.concat([x for x in p.map(coin, jobs, chunksize=1) if len(x)]).to_parquet(f)
    FN = pd.read_parquet('/home/user/ext/long/lab/results/flush_of_feats.parquet')
    FN = FN[FN.bull & (FN.oi24h <= 0.01668)][['coin', 't_in', 't_out', 'ret']].assign(src='清洗接盘')
    S = pd.read_parquet('/home/user/ext/long/lab/results/s29_pick.parquet')
    SH = S.assign(t_in=S.t + 300_000, t_out=S.t + 300_000 + S.bars * 300_000)[['coin', 't_in', 't_out', 'ret']].assign(src='多头摊平做空')
    NEW = pd.concat([FN, SH])
    for P in SETS:
        a, b = (pd.Timestamp(x).value // 10**6 for x in PERIOD[P])
        O = pd.read_parquet(f'/home/user/ext/of/allon/{P}.parquet')
        O = O[(O.t_in >= a) & (O.t_in < b)]
        OA = O[['coin', 't_in']].assign(t_out=O.t_out_a, ret=O.ret_a, src='旧打法')
        OB = O[['coin', 't_in']].assign(t_out=O.t_out_b, ret=O.ret_b, src='旧打法')
        N = NEW[(NEW.t_in >= a) & (NEW.t_in < b)]
        days = (b - a) / 86_400_000
        print(f'\n===== {P} {PERIOD[P][0]} ~ {PERIOD[P][1]}  旧打法 {O.coin.nunique()} 个币，信号 {len(O)} 个（每天 {len(O) / days:.0f} 个）；新打法 {N.coin.nunique()} 个币')
        for nm, T in (('只勾新打法（清洗接盘 + 多头摊平做空）', N),
                      ('只勾全部旧打法（原版出场）', OA), ('只勾全部旧打法（像新打法出场）', OB),
                      ('新打法 + 全部旧打法（原版出场）', pd.concat([N, OA])), ('新打法 + 全部旧打法（像新打法出场）', pd.concat([N, OB]))):
            r = port(T)
            print(f'{nm}: 100U → {r["final"]}U，最大回撤 {r["dd"]}%，开了 {r["n"]} 单（每天 {r["n"] / days:.1f}），胜率 {r["win"]}%，PF {r["pf"]}，各打法单数 {r["by"]}')
