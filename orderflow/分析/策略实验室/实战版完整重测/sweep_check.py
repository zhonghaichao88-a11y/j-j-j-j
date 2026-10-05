"""扫止损收回 + 5 笔补仓：第四份数据验证 + 组合回测（事先定好，只测这 2 组，参数不改）。
两组：5 分钟扫止损收回，补仓 5 笔在 -2/-4/-6/-8% 补，比例 ①1:1:1:1:1 ②1:1:2:2:4；均价赚 2% 止盈；离第一笔 11% 止损；最多 72 小时。
验证 3：另外 20 个山寨币 B + BTC/ETH/SOL，2025-10 ~ 2026-03（之前都没用过这段）。过关标准同前。
组合回测（按程序规则：同币一单、最多同时 10 单、每单最多用权益 10%，补仓也在这 10% 里）：
  时段 1 2026-04~09（43 币）、时段 2 2025-10~2026-03（43 币）
  只看它自己（不和新打法一起跑）"""
import sys, os, pickle, heapq, numpy as np, pandas as pd
from numba import njit
from multiprocessing import Pool
sys.path.insert(0, '/home/user/j-j-j-j/orderflow'); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import of_backtest as B
from of_backtest_pb import minute_bars
from of_playbook import Playbook
from pb_full import MAKER, TAKER, SLIP, STOP_SLIP
A20 = 'DOGE XRP ADA AVAX LINK LTC BCH DOT SUI OP ARB APT NEAR FIL AAVE UNI TRX WLD INJ ETC'.split()
B20 = 'ZEC PUMP STRK HYPE PEPE TRUMP BNB ENA TAO ONDO ZRO GRASS VIRTUAL PENGU ICP HBAR XLM SHIB FET WIF'.split()
M3 = ['BTC', 'ETH', 'SOL']
SETS = {'挑选': [('/home/user/ext/of/fp', c) for c in M3] + [('/home/user/ext/of/fp2', c) for c in A20],
        '验证1换时间': [('/home/user/ext/of/fp_old', c) for c in A20],
        '验证2换币': [('/home/user/ext/of/fp_alt2', c) for c in B20],
        '验证3新数据': [('/home/user/ext/of/fp_alt2_old', c) for c in B20] + [('/home/user/ext/of/fp_old3', c) for c in M3]}
SPLIT = {'挑选': 1782864000000, '验证1换时间': 1767225600000, '验证2换币': 1782864000000, '验证3新数据': 1767225600000}
LV = np.array([0.0, 0.02, 0.04, 0.06, 0.08])
CFGS = {'①1:1:1:1:1': np.array([1, 1, 1, 1, 1.0]), '②1:1:2:2:4': np.array([1, 1, 2, 2, 4.0])}
TP, SL, HOLD = 0.02, 0.11, 72 * 60


@njit(cache=True)
def dca(om, mo, mh, ml, mc, i0s, ents, sides, lv, wt, tp, sl, hold_min):
    n = len(om); k = len(i0s); W = wt.sum()
    rets = np.empty(k); t0 = np.empty(k, np.int64); t1 = np.empty(k, np.int64); legs = np.empty(k, np.int64); m = 0; busy = -1
    for q in range(k):
        i0 = i0s[q]
        if i0 <= busy or i0 >= n:
            continue
        s = sides[q]; e0 = ents[q]
        qty = wt[0]; cost = wt[0] * e0; fees = wt[0] * MAKER; nxt = 1
        st = e0 * (1 - s * sl); end = om[i0] + hold_min; ex = -1.0; j = i0
        while j < n and om[j] < end:
            if (ml[j] <= st) if s > 0 else (mh[j] >= st):
                ex = (min(mo[j], st) if s > 0 else max(mo[j], st)) * (1 - s * STOP_SLIP); break
            added = False
            while nxt < len(lv):
                px = e0 * (1 - s * lv[nxt])
                if (ml[j] <= px) if s > 0 else (mh[j] >= px):
                    f = (min(mo[j], px) if s > 0 else max(mo[j], px)) * (1 + s * SLIP)
                    qty += wt[nxt]; cost += wt[nxt] * f; fees += wt[nxt] * TAKER; nxt += 1; added = True
                else:
                    break
            tg = cost / qty * (1 + s * tp)
            if j > i0 and not added and ((mh[j] >= tg) if s > 0 else (ml[j] <= tg)):
                ex = tg; break
            j += 1
        if ex < 0:
            j = min(j, n) - 1; ex = mc[j] * (1 - s * SLIP)
        rets[m] = (qty * s * (ex / (cost / qty) - 1) - fees - qty * TAKER) / W
        t0[m] = om[i0]; t1[m] = om[min(j, n - 1)] + 1; legs[m] = nxt; m += 1; busy = j
    return rets[:m], t0[:m], t1[:m], legs[:m]


def coin(job):
    name, root, c = job
    d = B.load(root, c)
    if d is None or len(d['om']) == 0:
        return name, c, None
    om = d['om'].astype(np.int64); mo, mh, ml, mc = (d[k].astype(np.float64) for k in 'ohlc')
    idx = {int(x): i for i, x in enumerate(om)}
    bars, tick = minute_bars(d, 5)
    pb = Playbook(tick, {'max_trades_day': 10**9, 'max_losses_day': 10**9})
    L = []
    for b in bars:
        for sg in pb.on_bar(b):
            if sg.kind != 'pb_sweep':
                continue
            m0 = b.t // 60000 + 5
            if m0 not in idx:
                continue
            i = idx[m0]
            while i < len(om) and om[i] < m0 + 15:
                if (ml[i] <= sg.entry) if sg.side > 0 else (mh[i] >= sg.entry):
                    L.append((i, min(mo[i], sg.entry) if sg.side > 0 else max(mo[i], sg.entry), float(sg.side))); break
                i += 1
    out = {}
    rng = np.random.default_rng(abs(hash(c + name)) % 2**32)
    if L:
        i0s = np.array([x[0] for x in L], np.int64); ents = np.array([x[1] for x in L]); sides = np.array([x[2] for x in L])
        for nm, wt in CFGS.items():
            r, t0, t1, lg = dca(om, mo, mh, ml, mc, i0s, ents, sides, LV, wt, TP, SL, HOLD)
            rr = []
            for _ in range(5):
                ri = np.sort(rng.integers(0, len(om) - 1, len(L)))
                rr.append(dca(om, mo, mh, ml, mc, ri, mo[ri], rng.permutation(sides), LV, wt, TP, SL, HOLD)[0])
            out[nm] = (pd.DataFrame({'coin': c, 't_in': t0 * 60000, 't_out': t1 * 60000, 'ret': r, 'legs': lg}), np.concatenate(rr))
    return name, c, out


def pf(r):
    r = np.asarray(r); n = -r[r < 0].sum(); return float(r[r > 0].sum() / n) if n > 0 else float('inf')


def port(T, cap=10, size=0.10, start=100.0):
    T = T.sort_values('t_in', kind='stable').reset_index(drop=True)
    eq, op, busy, curve, taken = start, [], set(), [], []
    tin, tout, cn, rt = T.t_in.values, T.t_out.values, T.coin.values, T.ret.values
    for i in range(len(T)):
        while op and op[0][0] <= tin[i]:
            t, k, amt = heapq.heappop(op); eq += amt * rt[k]; curve.append((t, eq)); busy.discard(cn[k])
        if cn[i] in busy or len(op) >= cap:
            continue
        busy.add(cn[i]); taken.append(i); heapq.heappush(op, (tout[i], i, eq * size))
    while op:
        t, k, amt = heapq.heappop(op); eq += amt * rt[k]; curve.append((t, eq))
    c = pd.Series([e for _, e in curve], index=pd.to_datetime([t for t, _ in curve], unit='ms'))
    tk = T.loc[taken]
    return dict(final=round(float(c.iloc[-1]), 1), dd=round(float((c / c.cummax() - 1).min()) * 100, 1), n=len(tk),
                by=tk.groupby('src').size().to_dict(), worst=round(float(tk.ret.min()) * size * 100, 2))


if __name__ == '__main__':
    os.makedirs('/home/user/ext/of/sweep', exist_ok=True)
    f = '/home/user/ext/of/sweep/all.pkl'
    if not os.path.exists(f):
        jobs = [(nm, r, c) for nm, L in SETS.items() for r, c in L]
        with Pool(4) as p:
            res = p.map(coin, jobs, chunksize=1)
        pickle.dump(res, open(f, 'wb'))
    res = pickle.load(open(f, 'rb'))
    T = {}
    print('=== 每份数据（单独算每笔，不限仓位）')
    for cfg in CFGS:
        for nm in SETS:
            parts = [o[cfg] for n2, c, o in res if n2 == nm and o and cfg in o]
            if not parts:
                continue
            tr = pd.concat([p[0] for p in parts]); rnd = np.concatenate([p[1] for p in parts])
            b = tr.t_in.values >= SPLIT[nm]
            cp = sum(int(p[0].ret.sum() > 0) for p in parts)
            r = tr.ret.values
            okk = len(r) >= 100 and pf(r) >= 1.1 and pf(r[~b]) >= 1 and pf(r[b]) >= 1 and pf(r) - pf(rnd) >= 0.1 and cp * 2 > len(parts)
            print(f'{cfg} {nm}: {len(r)}笔 胜率{(r > 0).mean():.0%} PF{pf(r):.2f}（前半{pf(r[~b]):.2f}/后半{pf(r[b]):.2f}）随机{pf(rnd):.2f} 赚钱币{cp}/{len(parts)} '
                  f'补满5笔的{(tr.legs == 5).mean():.0%} 最差一单{r.min():.1%} {"✔" if okk else "✘"}')
            T[(cfg, nm)] = tr
    FN = pd.read_parquet('/home/user/ext/long/lab/results/flush_of_feats.parquet')
    FN = FN[FN.bull & (FN.oi24h <= 0.01668)][['coin', 't_in', 't_out', 'ret']].assign(src='清洗接盘')
    S = pd.read_parquet('/home/user/ext/long/lab/results/s29_pick.parquet')
    SH = S.assign(t_in=S.t + 300_000, t_out=S.t + 300_000 + S.bars * 300_000)[['coin', 't_in', 't_out', 'ret']].assign(src='多头摊平做空')
    NEW = pd.concat([FN, SH])
    print('\n=== 组合回测（同币一单、最多 10 单、每单 10%，滚利）')
    for per, (a, b), sets in (('2026-04~09', ('2026-04-01', '2026-10-01'), ('挑选', '验证2换币')), ('2025-10~2026-03', ('2025-10-01', '2026-04-01'), ('验证1换时间', '验证3新数据'))):
        a, b = pd.Timestamp(a).value // 10**6, pd.Timestamp(b).value // 10**6
        N = NEW[(NEW.t_in >= a) & (NEW.t_in < b)]
        days = (b - a) / 86_400_000
        print(f'\n{per}：')
        for cfg in CFGS:
            SW = pd.concat([T[(cfg, s)] for s in sets if (cfg, s) in T]).assign(src='扫止损补仓')
            SW = SW[(SW.t_in >= a) & (SW.t_in < b)][['coin', 't_in', 't_out', 'ret', 'src']]
            for nm2, X in (('只开扫止损补仓', SW),):
                r = port(X)
                print(f'  {cfg} {nm2}：100U → {r["final"]}U，回撤 {r["dd"]}%，{r["n"]} 单（每天 {r["n"] / days:.1f}），最差一单亏权益 {r["worst"]}%，各打法 {r["by"]}')
