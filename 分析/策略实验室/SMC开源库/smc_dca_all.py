"""SMC 全套 + 补仓，所有周期（事先定好）：
进场：smc_full 的 8 种打法 × 多/空（L=5，和原版一样的挂单/市价进场、24 根内没回踩到就放弃），外加 D 随机进场（市价）作对照。
出场：原版（前高/前低止损、盈亏比 2、最多 48 根）+ 80 种补仓（和 4 小时做空那次一样：5 种补仓方案 × 均价止盈 2/3/5/8% × 补满后再亏 5/10% 止损 × 最多 18/48 根）。
周期和数据：
  5 分钟、15 分钟：40 个币 2024-09 ~ 2026-09（2025-09-30 前训练、后检验）
  1 小时、4 小时：老币池 137 个 2021-10 ~ 2026-08（2024-07 前训练、后检验）+ 新币 296 个 2024-09 ~ 2026-08
同一根先止损 → 补仓 → 没补才看止盈。成本：市价/补仓/止损 0.07%，挂单进场和止盈 0.02%。每种打法每个方向每个币同时一单。
过关：每一段（15 分钟两段；1 小时 / 4 小时三段）PF ≥ 1.1、≥ 50 笔，而且比同周期同方向同出场的随机进场高 0.1 以上。"""
import sys, os, glob, numpy as np, pandas as pd
from numba import njit
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0, '../裸K形态')
import nk, smc_full as S
S.HTF.setdefault('5m', '1h')
_load_tf = S.load_tf


def load_tf(sym, tf):
    """5 分钟：用和 15 分钟同一份 1 分钟数据（40 个币 2024-09 ~ 2026-09）合成"""
    if tf == '5m':
        d = np.load(f'/home/user/okx_data/{sym}-USDT-SWAP_1m2y.npz')
        df = pd.DataFrame({k: d[k] for k in ('open', 'high', 'low', 'close')}, index=pd.to_datetime(d['ts'], unit='ms'))
        return S.resample(df[~df.index.duplicated()].sort_index(), '5min')
    return _load_tf(sym, tf)
TK, MK = 0.0007, 0.0002
SCH = {'3笔1:1:1(3/6)': ([0, .03, .06], [1, 1, 1]), '3笔1:1:2(3/6)': ([0, .03, .06], [1, 1, 2]),
       '5笔等(2..8)': ([0, .02, .04, .06, .08], [1] * 5), '5笔1:1:2:2:4(2..8)': ([0, .02, .04, .06, .08], [1, 1, 2, 2, 4]),
       '6笔深(3..18)': ([0, .03, .06, .09, .13, .18], [1, 1, 1, 2, 2, 3])}
CFG = [(s, tp, ex, h) for s in SCH for tp in (0.02, 0.03, 0.05, 0.08) for ex in (0.05, 0.10) for h in (18, 48)]
NAMES = {1: 'BOS→FVG', 2: 'BOS→OB', 3: 'CHoCH→FVG', 4: 'CHoCH→OB', 5: '扫流动性+CHoCH市价', 6: '扫流动性+CHoCH→FVG', 7: 'BOS→FVG+大周期', 8: 'BOS→OB+大周期', 0: '随机进场'}


@njit(cache=True)
def run_dca(o, h, l, c, fs, es, fees, side, lv, wt, tp, sl, hold):
    n = len(o); W = wt.sum(); out = np.empty(len(fs)); keep = np.zeros(len(fs), np.bool_); busy = -1
    for q in range(len(fs)):
        i = fs[q]
        if i <= busy or i >= n:
            continue
        e0 = es[q]; qty = wt[0]; cost = wt[0] * e0; fe = wt[0] * fees[q]; nxt = 1
        st = e0 * (1 - side * sl); end = min(i + hold, n); ex = -1.0; fo = TK; j = i
        while j < end:
            if (l[j] <= st) if side > 0 else (h[j] >= st):
                ex = min(o[j], st) if side > 0 else max(o[j], st)
                if j == i:
                    ex = st
                break
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
        out[q] = (qty * side * (ex / avg - 1) - fe - qty * fo) / W; keep[q] = True; busy = j
    return out, keep


def entries(df, tf, side, v, ev, hs):
    """原版的进场位置和价格（不出场），返回 [(信号根, 成交根, 成交价, 进场费, 推动起点)]"""
    o, h, l = df.open.values, df.high.values, df.low.values
    kind, how, need_sweep, need_htf = S.VARIANTS[v]
    out = []
    for (t, sd, k, leg, swept) in ev:
        if sd != side or k != kind or (need_sweep and not swept) or (need_htf and hs[t] != side) or t + S.W + S.HOLD + 2 >= len(o):
            continue
        stop = l[leg] if side > 0 else h[leg]
        if how == 'MKT':
            f, e, fee = t + 1, o[t + 1], TK
        else:
            z = S.zone(df, t, side, leg, how)
            if z is None:
                continue
            edge, ob = z
            if ob is not None:
                stop = min(stop, ob) if side > 0 else max(stop, ob)
            if (edge - stop) * side <= 0:
                continue
            f = None
            for kk in range(t + 1, t + 1 + S.W):
                if (side > 0 and l[kk] <= edge) or (side < 0 and h[kk] >= edge):
                    f = kk; e = min(o[kk], edge) if side > 0 else max(o[kk], edge); break
            if f is None:
                continue
            fee = MK
        risk = (e - stop) * side
        if risk <= 0 or not (0.002 < risk / e < 0.1):
            continue
        out.append((t, f, e, fee, leg))
    return out


def job(args):
    sym, tf, pool = args
    try:
        if tf in ('5m', '15m'):
            df = load_tf(sym, tf)
        elif pool == 'new':
            d = np.load(f'/home/user/okx_data/{sym}_bn1h.npz')
            df = pd.DataFrame({k: d[k] for k in ('open', 'high', 'low', 'close')}, index=pd.to_datetime(d['ts'], unit='ms'))
            df = df[~df.index.duplicated()].sort_index()
            df = df if tf == '1h' else S.resample(df, '4h')
        else:
            df = nk.load(sym, tf)
    except Exception:
        return []
    if len(df) < (1500 if pool != 'new' else 300):
        return []
    split = pd.Timestamp('2025-09-30') if tf in ('5m', '15m') else nk.SPLIT
    seg = lambda i: '新币' if pool == 'new' else ('训练' if df.index[i] < split else '检验')
    o, h, l, c = (df[k].values.astype(float) for k in ('open', 'high', 'low', 'close'))
    ev, _ = S.structure(df, 5)
    htf = S.resample(df, S.HTF[tf]); _, htr = S.structure(htf, 5)
    hs = pd.Series(htr, index=htf.index).shift(1).reindex(df.index, method='ffill').fillna(0).values
    rng = np.random.default_rng(abs(hash(sym + tf)) % 2**32)
    agg = {}

    def add(key, segs, r):
        for sgm in set(segs):
            m = segs == sgm; x = r[m]
            a = agg.setdefault(key + (sgm,), [0, 0, 0.0, 0.0])
            a[0] += len(x); a[1] += int((x > 0).sum()); a[2] += float(x[x > 0].sum()); a[3] += float(-x[x < 0].sum())
    for side in (1, -1):
        E = {v: entries(df, tf, side, v, ev, hs) for v in S.VARIANTS}
        nmax = max([len(x) for x in E.values()] + [1])
        ts = np.sort(rng.choice(np.arange(30, len(df) - 80), size=min(nmax * 2, len(df) - 120), replace=False))
        E[0] = [(t, t + 1, o[t + 1], TK, t - 20 + int(np.argmax(h[t - 20:t + 1]) if side < 0 else np.argmin(l[t - 20:t + 1]))) for t in ts]
        for v, L in E.items():
            if not L:
                continue
            busy = -1; rs = []; sg = []          # 原版出场
            for (t, f, e, fee, leg) in L:
                if t <= busy:
                    continue
                r = S.sim(df, t, side, leg, 'MKT' if v in (0, 5) else S.VARIANTS[v][1], 2.0)
                if r:
                    busy = r[1]; stop = l[leg] if side > 0 else h[leg]
                    rs.append(r[2] * abs(e - stop) / e); sg.append(seg(t))
            if rs:
                add((tf, v, side, '原版（结构止损、盈亏比2）'), np.array(sg), np.array(rs))
            fs = np.array([x[1] for x in L], np.int64); es = np.array([x[2] for x in L]); fees = np.array([x[3] for x in L])
            segs = np.array([seg(x[0]) for x in L])
            for sc, tp, exr, hd in CFG:
                lv, wt = np.array(SCH[sc][0], float), np.array(SCH[sc][1], float)
                r, keep = run_dca(o, h, l, c, fs, es, fees, float(side), lv, wt, tp, lv[-1] + exr, hd)
                add((tf, v, side, f'{sc} 止盈{tp:.0%} 补满再亏{exr:.0%}止损 拿{hd}根'), segs[keep], r[keep])
    return [k + tuple(a) for k, a in agg.items()]


if __name__ == '__main__':
    old = nk.coins()
    new = sorted(os.path.basename(f).split('_bn1h')[0] for f in glob.glob('/home/user/okx_data/*_bn1h.npz'))
    new = [s for s in new if s not in set(old)]
    s15 = sorted(os.path.basename(f).split('-USDT')[0] for f in glob.glob('/home/user/okx_data/*-USDT-SWAP_1m2y.npz'))
    tfs = sys.argv[1].split(',') if len(sys.argv) > 1 else ['15m', '1h', '4h']
    tag = '' if len(sys.argv) <= 1 else '_' + '_'.join(tfs)
    jobs = [(s, tf, 'old') for tf in tfs if tf in ('5m', '15m') for s in s15] + [(s, tf, 'old') for tf in tfs if tf in ('1h', '4h') for s in old] + \
           [(s, tf, 'new') for tf in tfs if tf in ('1h', '4h') for s in new]
    with ProcessPoolExecutor(4) as ex:
        rows = sum(ex.map(job, jobs, chunksize=4), [])
    A = pd.DataFrame(rows, columns=['周期', 'v', '方向', '出场', '段', 'n', 'win', 'gp', 'gl'])
    A = A.groupby(['周期', 'v', '方向', '出场', '段'])[['n', 'win', 'gp', 'gl']].sum().reset_index()
    A['PF'] = A.gp / A.gl
    A.to_csv(f'/home/user/ext/smc_dca_all_raw{tag}.csv', index=False)
    W = A.pivot_table(index=['周期', 'v', '方向', '出场'], columns='段', values=['PF', 'n']).reset_index()
    W.columns = [a if not b else f'{b}_{a}' for a, b in W.columns]
    rnd = W[W.v == 0].set_index(['周期', '方向', '出场'])
    rows = []
    for _, r in W[W.v != 0].iterrows():
        segs = ['训练', '检验'] + ([] if r.周期 in ('5m', '15m') else ['新币'])
        rr = rnd.loc[(r.周期, r.方向, r.出场)] if (r.周期, r.方向, r.出场) in rnd.index else None
        ok = rr is not None and all(r.get(f'{s}_n', 0) >= 50 and r.get(f'{s}_PF', 0) >= 1.1 and r[f'{s}_PF'] - rr.get(f'{s}_PF', 9) >= 0.1 for s in segs)
        rows.append({'周期': r.周期, '打法': NAMES[r.v], '方向': '多' if r.方向 > 0 else '空', '出场': r.出场,
                     **{f'{s}_PF': round(r.get(f'{s}_PF', np.nan), 2) for s in ('训练', '检验', '新币')},
                     **{f'{s}_笔数': int(r.get(f'{s}_n', 0) or 0) for s in ('训练', '检验', '新币')},
                     **{f'随机_{s}_PF': round(rr.get(f'{s}_PF', np.nan), 2) if rr is not None else np.nan for s in ('训练', '检验', '新币')}, '过关': '✔' if ok else ''})
    R = pd.DataFrame(rows)
    R.to_csv(f'SMC补仓_全部周期{tag}.csv', index=False, encoding='utf-8-sig')
    pd.set_option('display.width', 300); pd.set_option('display.max_columns', 30); pd.set_option('display.max_rows', 100)
    print('组合总数', len(R), '过关', int((R.过关 == '✔').sum()))
    for tf in tfs:
        X = R[R.周期 == tf]; Y = rnd.loc[tf] if tf in rnd.index.get_level_values(0) else None
        print(f'\n{tf}：{len(X)} 组，过关 {int((X.过关 == "✔").sum())}；PF 中位 训练 {X.训练_PF.median():.2f} 检验 {X.检验_PF.median():.2f} 新币 {X.新币_PF.median():.2f}；'
              f'同周期随机进场中位 训练 {X.随机_训练_PF.median():.2f} 检验 {X.随机_检验_PF.median():.2f} 新币 {X.随机_新币_PF.median():.2f}')
    print('\n过关的：'); print(R[R.过关 == '✔'].to_string(index=False))
    m = R[['训练_PF', '检验_PF'] + []].min(axis=1)
    R['最差段'] = R[['训练_PF', '检验_PF', '新币_PF']].min(axis=1)
    print('\n最差段 PF 最高的 15 组（参考）：'); print(R.sort_values('最差段', ascending=False).head(15).to_string(index=False))
