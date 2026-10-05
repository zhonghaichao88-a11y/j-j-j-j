"""SMC 加补仓（4 小时做空）：事先定好。
进场三组（都在信号K线下一根开盘价市价做空）：
  A 扫流动性 + CHoCH 做空（之前 SMC 里最好的）  C 任何看跌结构突破做空（不要 SMC 条件）  D 随机时间做空（A 单数的 3 倍）
出场：原版（前高止损、盈亏比 2、最多 48 根）作对照；补仓：价格往上走到离第一笔 x% 就补空，按均价止盈，止损 = 补满后再涨 y%，最多拿 N 根
  补仓方案：3 笔 1:1:1 +3/+6% / 3 笔 1:1:2 +3/+6% / 5 笔 1:1:1:1:1 +2..+8% / 5 笔 1:1:2:2:4 +2..+8% / 6 笔 1:1:1:2:2:3 +3/6/9/13/18%
  止盈（均价）2 / 3 / 5 / 8%；补满后再涨 5 / 10% 止损；最多拿 18 根（3 天）/ 48 根（8 天）
数据：老币池 137 个 2021-10 ~ 2026-08（2024-07 前训练、后检验）+ 新币 296 个 2024-09 ~ 2026-08。4 小时K线，同一根先止损 → 补仓 → 没补才看止盈。
成本：吃单 0.07%（进、补、出都算），止盈挂单 0.02%。每组每个币同时只拿一单。
过关：A 在三段（老币训练 / 老币检验 / 新币）PF 都 ≥ 1.1，而且都比同样补仓的 D 随机做空高 0.1 以上。"""
import sys, os, glob, numpy as np, pandas as pd
from numba import njit
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0, '../裸K形态')
import nk, smc_full as S
from smc_check_new import load_new
TK, MK = 0.0007, 0.0002
SCH = {'3笔1:1:1(+3/6)': ([0, .03, .06], [1, 1, 1]), '3笔1:1:2(+3/6)': ([0, .03, .06], [1, 1, 2]),
       '5笔等(+2..8)': ([0, .02, .04, .06, .08], [1] * 5), '5笔1:1:2:2:4(+2..8)': ([0, .02, .04, .06, .08], [1, 1, 2, 2, 4]),
       '6笔深(+3..18)': ([0, .03, .06, .09, .13, .18], [1, 1, 1, 2, 2, 3])}
CFG = [(s, tp, ex, h) for s in SCH for tp in (0.02, 0.03, 0.05, 0.08) for ex in (0.05, 0.10) for h in (18, 48)]


@njit(cache=True)
def dca1(o, h, l, c, i, lv, wt, tp, sl, hold):
    """做空一单：返回 (收益(按整单最多用的钱), 平仓根)"""
    n = len(o); W = wt.sum(); e0 = o[i]
    qty = wt[0]; cost = wt[0] * e0; fees = wt[0] * TK; nxt = 1
    st = e0 * (1 + sl); end = min(i + hold, n); ex = -1.0; fo = TK; j = i
    while j < end:
        if h[j] >= st:
            ex = max(o[j], st); break
        added = False
        while nxt < len(lv):
            px = e0 * (1 + lv[nxt])
            if h[j] >= px:
                f = max(o[j], px); qty += wt[nxt]; cost += wt[nxt] * f; fees += wt[nxt] * TK; nxt += 1; added = True
            else:
                break
        tg = cost / qty * (1 - tp)
        if j > i and not added and l[j] <= tg:
            ex = tg; fo = MK; break
        j += 1
    if ex < 0:
        j = end - 1; ex = c[j]
    avg = cost / qty
    return (qty * -(ex / avg - 1) - fees - qty * fo) / W, j


def entries(df, rng):
    ev, _ = S.structure(df, 5)
    out = {'A': [], 'C': []}
    for (t, side, k, leg, sw) in ev:
        if side != -1 or t + 60 >= len(df):
            continue
        out['C'].append((t, leg))
        if k == 'CHOCH' and sw:
            out['A'].append((t, leg))
    h = df.high.values
    nA = max(len(out['A']), 1)
    ts = sorted(rng.choice(np.arange(30, len(df) - 80), size=min(nA * 3, len(df) - 120), replace=False))
    out['D'] = [(t, t - 20 + int(np.argmax(h[t - 20:t + 1]))) for t in ts]
    return out


def coin(args):
    sym, kind = args
    try:
        df = nk.load(sym, '4h') if kind == 'old' else load_new(sym)
    except Exception:
        return []
    if len(df) < (1500 if kind == 'old' else 300):
        return []
    rng = np.random.default_rng(abs(hash(sym)) % 2**32)
    o, h, l, c = (df[k].values.astype(float) for k in ('open', 'high', 'low', 'close'))
    seg = lambda t: ('新币' if kind == 'new' else ('老币训练' if df.index[t] < nk.SPLIT else '老币检验'))
    res = []
    for g, L in entries(df, rng).items():
        busy = -1                                  # 原版
        for t, leg in L:
            if t <= busy:
                continue
            r = S.sim(df, t, -1, leg, 'MKT', 2.0)
            if r:
                busy = r[1]; risk = (h[leg] - o[t + 1]) / o[t + 1]
                res.append((g, '原版（前高止损、盈亏比2）', seg(t), sym, r[2] * risk))
        for sc, tp, ex, hd in CFG:
            lv, wt = np.array(SCH[sc][0], float), np.array(SCH[sc][1], float)
            busy = -1
            for t, leg in L:
                if t + 1 <= busy:
                    continue
                r, j = dca1(o, h, l, c, t + 1, lv, wt, tp, lv[-1] + ex, hd)
                busy = j
                res.append((g, f'{sc} 止盈{tp:.0%} 补满再涨{ex:.0%}止损 拿{hd}根', seg(t), sym, r))
    return res


if __name__ == '__main__':
    D = '/home/user/okx_data'
    old = nk.coins()
    new = sorted(os.path.basename(f).split('_bn1h')[0] for f in glob.glob(f'{D}/*_bn1h.npz'))
    new = [s for s in new if s not in set(old)]
    jobs = [(s, 'old') for s in old] + [(s, 'new') for s in new]
    with ProcessPoolExecutor(4) as ex:
        rows = sum(ex.map(coin, jobs, chunksize=4), [])
    T = pd.DataFrame(rows, columns=['组', '出场', '段', '币', 'ret'])
    T.to_parquet('/home/user/ext/smc_dca.parquet')
    pf = lambda x: x[x > 0].sum() / -x[x < 0].sum() if (x < 0).any() else np.nan
    P = T.groupby(['出场', '组', '段']).ret.apply(pf).unstack(['组', '段'])
    N = T.groupby(['出场', '组']).size().unstack('组')
    W = T.groupby(['出场', '组']).ret.apply(lambda x: (x > 0).mean()).unstack('组')
    segs = ['老币训练', '老币检验', '新币']
    out = []
    for exn in P.index:
        r = {'出场': exn, 'A单数': N.loc[exn, 'A'], 'A胜率%': round(W.loc[exn, 'A'] * 100)}
        for s in segs:
            r[f'A_{s}'] = round(P.loc[exn, ('A', s)], 2); r[f'C_{s}'] = round(P.loc[exn, ('C', s)], 2); r[f'D随机_{s}'] = round(P.loc[exn, ('D', s)], 2)
        r['过关'] = '✔' if all(P.loc[exn, ('A', s)] >= 1.1 and P.loc[exn, ('A', s)] - P.loc[exn, ('D', s)] >= 0.1 for s in segs) else ''
        out.append(r)
    R = pd.DataFrame(out).sort_values('A_新币', ascending=False)
    R.to_csv('SMC补仓_全部.csv', index=False, encoding='utf-8-sig')
    pd.set_option('display.width', 300); pd.set_option('display.max_columns', 30)
    print(R[R.出场.str.startswith('原版')].to_string(index=False))
    print(f'\n补仓 {len(R) - 1} 种：A 三段都过关 {int((R.过关 == "✔").sum())} 种')
    for g in ('A', 'C', 'D随机'):
        print(g, '补仓后 PF 中位：', ' / '.join(f'{s} {R[~R.出场.str.startswith("原版")][f"{g}_{s}"].median():.2f}' for s in segs))
    print('\n按新币 PF 排前 10：'); print(R.head(10).to_string(index=False))
