"""1 分钟实战版 3 个打法（关键位吸收、扫止损收回、回踩VWAP失衡）完整重测，和其他旧打法一样的全部组合。
信号：of_playbook.Playbook（时段过滤照原版；每天单数上限去掉，收集全部信号），1 / 5 / 15 分钟K线确认。
进场：照原版挂限价单，之后 3 根K线内碰到才成交（挂单 0.02%），没碰到撤单。
出场组合（全部测）：
  原版类：信号自己的止损，止盈 1 / 2 / 3 倍止损距离，最多拿 2 小时 / 12 小时
  固定：止损 2 / 3 / 5% × 止盈 不设 / 1 / 2 / 3 / 5 / 8% × 最多拿 4 / 12 / 24 / 48 小时
  补仓：第一轮 A~D（3~4 笔）× 均价止盈 1/2/3% × 止损 10/15% × 24/72 小时；第二轮 E~I（5~8 笔）× 止盈 1/2/3/5/8% × 补满后再亏 3/6/10% × 24/72 小时
成本：挂单进 0.02%；补仓和出场吃单 0.05% + 滑点 0.02%；止损再加 0.05%。每个打法在每个币上同时只拿一单。
随机对照：同样出场，随机时间进场（开盘价），3 次。
数据和过关同前：挑选 23 币 2026-04~09；验证 1 同 20 山寨 2025-10~2026-03；验证 2 另 20 山寨 2026-04~09；三份都要过。"""
import sys, os, pickle, numpy as np
from numba import njit
from multiprocessing import Pool
sys.path.insert(0, '/home/user/j-j-j-j/orderflow'); sys.path.insert(0, '/home/user/j-j-j-j/orderflow/分析/策略实验室/旧打法补仓')
import of_backtest as B
from of_backtest_pb import minute_bars
from of_playbook import Playbook
import dca_bt as D1, dca2_bt as D2
MAKER, TAKER, SLIP, STOP_SLIP = 0.0002, 0.0005, 0.0002, 0.0005
EXITS = [('原版', 0.0, rr, h) for rr in (1.0, 2.0, 3.0) for h in (2, 12)] + \
        [('固定', sp, tp, h) for sp in (0.02, 0.03, 0.05) for tp in (0.0, 0.01, 0.02, 0.03, 0.05, 0.08) for h in (4, 12, 24, 48)] + \
        [('补仓', D1.SCHEMES[s], tp, sl, h) for (s, tp, sl, h) in D1.CFG] + [('补仓', D2.SCHEMES[s], tp, sl, h) for (s, tp, sl, h) in D2.CFG]
SETS, SPLIT = D1.SETS, D1.SPLIT
OUT = '/home/user/ext/of/pbfull'


@njit(cache=True)
def single(om, mo, mh, ml, mc, i0s, ents, sides, sdist, fixed, sp, tp, hold_min, fee_in):
    n = len(om); k = len(i0s); rets = np.empty(k); ts = np.empty(k, np.int64); m = 0; busy = -1
    for q in range(k):
        i0 = i0s[q]
        if i0 <= busy or i0 >= n:
            continue
        s = sides[q]; e = ents[q]
        if fixed:
            st = e * (1 - s * sp); tg = e * (1 + s * tp) if tp > 0 else np.nan
        else:
            st = e * (1 - s * sdist[q]); tg = e * (1 + s * tp * sdist[q])
        end = om[i0] + hold_min; ex = np.nan; fee = TAKER; j = i0
        while j < n and om[j] < end:
            if (ml[j] <= st) if s > 0 else (mh[j] >= st):
                ex = (st if j == i0 else (min(mo[j], st) if s > 0 else max(mo[j], st))) * (1 - s * STOP_SLIP); break
            if j > i0 and tg == tg and ((mh[j] >= tg) if s > 0 else (ml[j] <= tg)):
                ex = tg; fee = MAKER; break
            j += 1
        if ex != ex:
            j = min(j, n) - 1; ex = mc[j] * (1 - s * SLIP)
        rets[m] = s * (ex / e - 1) - fee_in - fee; ts[m] = om[i0]; m += 1; busy = j
    return rets[:m], ts[:m]


@njit(cache=True)
def dca(om, mo, mh, ml, mc, i0s, ents, sides, lv, wt, tp, sl, hold_min, fee_in):
    n = len(om); k = len(i0s); W = wt.sum(); rets = np.empty(k); ts = np.empty(k, np.int64); m = 0; busy = -1
    for q in range(k):
        i0 = i0s[q]
        if i0 <= busy or i0 >= n:
            continue
        s = sides[q]; e0 = ents[q]
        qty = wt[0]; cost = wt[0] * e0; fees = wt[0] * fee_in; nxt = 1
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
        rets[m] = (qty * s * (ex / (cost / qty) - 1) - fees - qty * TAKER) / W; ts[m] = om[i0]; m += 1; busy = j
    return rets[:m], ts[:m]


def run_exit(ex, om, mo, mh, ml, mc, i0s, ents, sides, sd, fee_in):
    if ex[0] == '补仓':
        (lv, wt), tp, sl, h = ex[1], ex[2], ex[3], ex[4]
        return dca(om, mo, mh, ml, mc, i0s, ents, sides, np.array(lv), np.array(wt, np.float64), tp, sl, h * 60, fee_in)
    mode, sp, tp, h = ex
    return single(om, mo, mh, ml, mc, i0s, ents, sides, sd, mode == '固定', sp, tp, h * 60, fee_in)


def coin(job):
    name, root, c = job
    d = B.load(root, c)
    om = d['om'].astype(np.int64); mo, mh, ml, mc = (d[k].astype(np.float64) for k in 'ohlc')
    idx = {int(x): i for i, x in enumerate(om)}
    rng = np.random.default_rng(abs(hash(c + name)) % 2**32)
    split = SPLIT[name] // 60000
    out = {}
    for tf in (1, 5, 15):
        bars, tick = minute_bars(d, tf)
        pb = Playbook(tick, {'max_trades_day': 10**9, 'max_losses_day': 10**9})
        sig = {}
        for b in bars:
            for sg in pb.on_bar(b):
                m0 = b.t // 60000 + tf
                if m0 not in idx:
                    continue
                i = idx[m0]
                while i < len(om) and om[i] < m0 + 3 * tf:           # 3 根K线内碰到挂单价才成交
                    if (ml[i] <= sg.entry) if sg.side > 0 else (mh[i] >= sg.entry):
                        e = min(mo[i], sg.entry) if sg.side > 0 else max(mo[i], sg.entry)
                        sig.setdefault(sg.kind, []).append((i, e, float(sg.side), abs(sg.entry - sg.stop) / sg.entry))
                        break
                    i += 1
        for kind, L in sig.items():
            i0s = np.array([x[0] for x in L], np.int64); ents = np.array([x[1] for x in L]); sides = np.array([x[2] for x in L]); sd = np.array([x[3] for x in L])
            for ei, ex in enumerate(EXITS):
                r, t = run_exit(ex, om, mo, mh, ml, mc, i0s, ents, sides, sd, MAKER)
                gp = gl = 0.0
                for _ in range(3):
                    ri = np.sort(rng.integers(0, len(om) - 1, len(L)))
                    rr, _ = run_exit(ex, om, mo, mh, ml, mc, ri, mo[ri], rng.permutation(sides), rng.permutation(sd), MAKER)
                    gp += rr[rr > 0].sum(); gl -= rr[rr < 0].sum()
                out[(tf, kind, ei)] = (r.astype(np.float32), t >= split, gp, gl)
    return name, c, out


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    jobs = [(nm, r, c) for nm, L in SETS.items() for r, c in L if not os.path.exists(f'{OUT}/{nm}_{c}.pkl')]
    with Pool(4) as p:
        for nm, c, o in p.imap_unordered(coin, jobs):
            pickle.dump(o, open(f'{OUT}/{nm}_{c}.pkl', 'wb')); print(nm, c, len(o), flush=True)
