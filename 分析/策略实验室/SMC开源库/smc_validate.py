"""SMC + 补仓 候选组合的验证（参数照上一轮原样，不再改）：
候选：
  H1 1 小时 BOS→OB + 大周期同向 做多，6 笔深补（3/6/9/13/18%，1:1:1:2:2:3），均价止盈 8%，补满再亏 10% 止损，最多 18 根
  H2 1 小时 BOS→OB 做多，同 H1 补仓
  H3 1 小时 BOS→OB + 大周期 做多，5 笔 1:1:2:2:4（2/4/6/8%），止盈 8%，补满再亏 5%，最多 18 根
  M1 15 分钟 BOS→OB + 大周期 做多，5 笔 1:1:2:2:4，止盈 5%，补满再亏 5%，最多 48 根
  M2 15 分钟 BOS→OB + 大周期 做空，5 笔 1:1:2:2:4，止盈 3%，补满再亏 5%，最多 48 根
  每个候选配同周期同方向同补仓的"随机进场"对照。
一、组合回测（按程序规则：同币一单、最多同时 10 单、整单最多用权益 10%、滚利）：欧易数据（上一轮用的）
二、换交易所复测：币安合约 5 分钟数据合成 15 分钟 / 1 小时 —— 2022-23 年 38 个币、2024-01 ~ 2026-09 111 个币（单笔统计 + 组合回测）
过关：币安数据上每段（2022-23 / 2024 / 2025+）PF ≥ 1.1 且比随机高 0.1；组合 100U 赚钱、回撤可以接受。"""
import sys, os, glob, heapq, numpy as np, pandas as pd
from numba import njit
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0, '../裸K形态')
import nk, smc_full as S
import smc_dca_all as M
TK, MK = M.TK, M.MK
DEEP6 = ([0, .03, .06, .09, .13, .18], [1, 1, 1, 2, 2, 3]); G5 = ([0, .02, .04, .06, .08], [1, 1, 2, 2, 4])
CANDS = {'H1': ('1h', 8, 1, DEEP6, 0.08, 0.10, 18), 'H2': ('1h', 2, 1, DEEP6, 0.08, 0.10, 18), 'H3': ('1h', 8, 1, G5, 0.08, 0.05, 18),
         'M1': ('15m', 8, 1, G5, 0.05, 0.05, 48), 'M2': ('15m', 8, -1, G5, 0.03, 0.05, 48)}


@njit(cache=True)
def run_dca(o, h, l, c, fs, es, fees, side, lv, wt, tp, sl, hold):
    n = len(o); W = wt.sum(); out = np.empty(len(fs)); jj = np.zeros(len(fs), np.int64); keep = np.zeros(len(fs), np.bool_); busy = -1
    for q in range(len(fs)):
        i = fs[q]
        if i <= busy or i >= n:
            continue
        e0 = es[q]; qty = wt[0]; cost = wt[0] * e0; fe = wt[0] * fees[q]; nxt = 1
        st = e0 * (1 - side * sl); end = min(i + hold, n); ex = -1.0; fo = TK; j = i
        while j < end:
            if (l[j] <= st) if side > 0 else (h[j] >= st):
                ex = st if j == i else (min(o[j], st) if side > 0 else max(o[j], st)); break
            added = False
            while nxt < len(lv):
                px = e0 * (1 - side * lv[nxt])
                if (l[j] <= px) if side > 0 else (h[j] >= px):
                    f = min(o[j], px) if side > 0 else max(o[j], px)
                    qty += wt[nxt]; cost += wt[nxt] * f; fe += wt[nxt] * TK; nxt += 1; added = True
                else:
                    break
            tg = cost / qty * (1 + side * tp)
            if j > i and not added and ((h[j] >= tg) if side > 0 else (l[j] <= tg)):
                ex = tg; fo = MK; break
            j += 1
        if ex < 0:
            j = end - 1; ex = c[j]
        avg = cost / qty
        out[q] = (qty * side * (ex / avg - 1) - fe - qty * fo) / W; jj[q] = j; keep[q] = True; busy = j
    return out, jj, keep


def trades(df, sym, tf, src):
    o, h, l, c = (df[k].values.astype(float) for k in ('open', 'high', 'low', 'close'))
    ev, _ = S.structure(df, 5)
    htf = S.resample(df, S.HTF[tf]); _, htr = S.structure(htf, 5)
    hs = pd.Series(htr, index=htf.index).shift(1).reindex(df.index, method='ffill').fillna(0).values
    rng = np.random.default_rng(abs(hash(sym + tf + src)) % 2**32)
    T = df.index.values.astype('datetime64[ms]').astype(np.int64)
    out = []
    for name, (ctf, v, side, (lv, wt), tp, ex, hd) in CANDS.items():
        if ctf != tf:
            continue
        L = M.entries(df, tf, side, v, ev, hs)
        ts = np.sort(rng.choice(np.arange(30, len(df) - 80), size=min(max(len(L), 1) * 2, len(df) - 120), replace=False))
        R = [(t, t + 1, o[t + 1], TK, 0) for t in ts]
        lv, wt = np.array(lv, float), np.array(wt, float)
        for grp, E in ((name, L), (name + '随机', R)):
            if not E:
                continue
            fs = np.array([x[1] for x in E], np.int64); es = np.array([x[2] for x in E]); fe = np.array([x[3] for x in E])
            r, jj, keep = run_dca(o, h, l, c, fs, es, fe, float(side), lv, wt, tp, lv[-1] + ex, hd)
            for q in np.flatnonzero(keep):
                out.append((src, grp, sym, int(T[fs[q]]), int(T[min(jj[q] + 1, len(T) - 1)]), float(r[q])))
    return out


def job(a):
    src, sym, tf = a
    try:
        if src == 'okx_old':
            df = nk.load(sym, tf)
        elif src == 'okx_new':
            d = np.load(f'/home/user/okx_data/{sym}_bn1h.npz')
            df = pd.DataFrame({k: d[k] for k in ('open', 'high', 'low', 'close')}, index=pd.to_datetime(d['ts'], unit='ms'))
            df = df[~df.index.duplicated()].sort_index()
        elif src == 'okx_15m':
            df = M.load_tf(sym, '15m')
        else:                                  # 币安 5 分钟 → 15 分钟 / 1 小时
            root = '/home/user/ext/oos/f5' if src == 'bn_2223' else '/home/user/ext/long'
            k = pd.read_parquet(f'{root}/k/{sym}.parquet', columns=['ts', 'open', 'high', 'low', 'close']).sort_values('ts').drop_duplicates('ts')
            df = S.resample(k.set_index(pd.to_datetime(k.ts, unit='ms'))[['open', 'high', 'low', 'close']], '15min' if tf == '15m' else '1h')
    except Exception:
        return []
    if len(df) < 300:
        return []
    return trades(df, sym, tf, src)


def pf(x):
    x = np.asarray(x); n = -x[x < 0].sum(); return x[x > 0].sum() / n if n > 0 else np.nan


def port(X, cap=10, size=0.10, start=100.0):
    X = X.sort_values('t_in', kind='stable')
    eq, op, busy, curve = start, [], set(), []
    for cn, ti, to, rt in zip(X.coin.values, X.t_in.values, X.t_out.values, X.ret.values):
        while op and op[0][0] <= ti:
            t, k, amt, r_, cc = heapq.heappop(op); eq += amt * r_; curve.append(eq); busy.discard(cc)
        if cn in busy or len(op) >= cap:
            continue
        busy.add(cn); heapq.heappush(op, (to, len(curve) + len(op) + ti % 997, eq * size, rt, cn))
    while op:
        t, k, amt, r_, cc = heapq.heappop(op); eq += amt * r_; curve.append(eq)
    c = pd.Series(curve if curve else [start])
    return round(float(c.iloc[-1]), 1), round(float((c / c.cummax() - 1).min()) * 100, 1)


if __name__ == '__main__':
    old = nk.coins()
    new = [s for s in sorted(os.path.basename(f).split('_bn1h')[0] for f in glob.glob('/home/user/okx_data/*_bn1h.npz')) if s not in set(old)]
    s15 = sorted(os.path.basename(f).split('-USDT')[0] for f in glob.glob('/home/user/okx_data/*-USDT-SWAP_1m2y.npz'))
    bn = sorted(os.path.basename(f)[:-8] for f in glob.glob('/home/user/ext/long/k/*.parquet'))
    b23 = open('/home/user/ext/oos/flush_old_coins.txt').read().split()
    jobs = [('okx_old', s, '1h') for s in old] + [('okx_new', s, '1h') for s in new] + [('okx_15m', s, '15m') for s in s15] + \
           [(src, s, tf) for tf in ('1h', '15m') for src, L in (('bn_2223', b23), ('bn_2426', bn)) for s in L]
    f = '/home/user/ext/smc_validate.parquet'
    if not os.path.exists(f):
        with ProcessPoolExecutor(4) as ex:
            rows = sum(ex.map(job, jobs, chunksize=4), [])
        pd.DataFrame(rows, columns=['src', 'grp', 'coin', 't_in', 't_out', 'ret']).to_parquet(f)
    T = pd.read_parquet(f)
    T['yr'] = pd.to_datetime(T.t_in, unit='ms').dt.year
    T['段'] = np.where(T.yr <= 2023, '2022-23', np.where(T.yr == 2024, '2024', '2025+'))
    print('=== 币安数据（换交易所）单笔统计：PF（随机）')
    for name in CANDS:
        line = f'{name} {CANDS[name][0]}:'
        ok = True
        for sg in ('2022-23', '2024', '2025+'):
            a = T[(T.src.str.startswith('bn')) & (T.grp == name) & (T.段 == sg)].ret; b = T[(T.src.str.startswith('bn')) & (T.grp == name + '随机') & (T.段 == sg)].ret
            line += f'  {sg} {len(a)}笔 胜{(a > 0).mean():.0%} PF{pf(a):.2f}（随机{pf(b):.2f}）'
            ok &= len(a) >= 50 and pf(a) >= 1.1 and pf(a) - pf(b) >= 0.1
        print(line, '✔' if ok else '✘')
    print('\n=== 组合回测（同币一单、最多 10 单、整单最多 10%，滚利）：100U → 最后 / 最大回撤')
    for name in CANDS:
        tf = CANDS[name][0]
        line = f'{name}:'
        for lab, srcs, a, b in (('欧易老币池', ['okx_old'] if tf == '1h' else ['okx_15m'], None, None),
                                ('欧易 2024-09~2026-08（老+新币）', ['okx_old', 'okx_new'] if tf == '1h' else ['okx_15m'], '2024-09-01', '2026-09-01'),
                                ('币安 2022-23', ['bn_2223'], None, None), ('币安 2024~2026-09', ['bn_2426'], None, None)):
            X = T[T.src.isin(srcs)]
            if a:
                X = X[(X.t_in >= pd.Timestamp(a).value // 10**6) & (X.t_in < pd.Timestamp(b).value // 10**6)]
            s1 = port(X[X.grp == name]); s2 = port(X[X.grp == name + '随机'])
            line += f'  {lab} {s1[0]}U/{s1[1]}%（随机 {s2[0]}U）'
        print(line)
