"""旧打法 + 分批补仓（类似 NFI 的越跌越补、按均价止盈），事先定好规则再看结果。
进场：订单流全部旧打法的信号（Detector 全开），5 / 15 / 60 分钟；K线收盘出信号，下一根第一分钟开盘价进第一笔。
补仓方案（每单最多用的钱 = 1 份，按比例分批；价格朝不利方向走到这么远就补，按挂单价成交）：
  A：3 笔 1:1:1，离第一笔 -2% / -4% 补
  B：3 笔 1:1:1，离第一笔 -3% / -6% 补
  C：3 笔 1:1:2，离第一笔 -3% / -6% 补（后面补得多）
  D：4 笔 1:1:1:1，离第一笔 -2% / -4% / -6% 补
止盈：按持仓均价赚 1% / 2% / 3% 全平
止损：离第一笔 10% / 15%（补满以后还继续亏）全平
最多拿：24 / 72 小时，到时间全平
同一分钟里：先看止损 → 再看补仓 → 这分钟没补仓才看止盈（往坏处算）。每个打法在每个币上同时只拿一单。
收益按"这一单最多能用的钱"算（没补的部分算 0），这样和不补仓的打法能直接比。
成本：每笔进出都算吃单 0.05% + 滑点 0.02%，止损再加 0.05%。
随机对照：同样的补仓和出场，随机时间进场，5 次。
数据和过关标准同前：挑选 23 币 2026-04~09；验证 1 同 20 山寨 2025-10~2026-03；验证 2 另 20 山寨 2026-04~09。
过关：≥100 笔、PF ≥ 1.1、前后半都 ≥ 1、比随机高 0.1、一半以上的币赚钱；三份都要过。"""
import sys, os, pickle, numpy as np
from numba import njit
from multiprocessing import Pool
sys.path.insert(0, '/home/user/j-j-j-j/orderflow')
import of_backtest as B
from of_core import Detector
FEE, SLIP, STOP_SLIP = 0.0005, 0.0002, 0.0005
SCHEMES = {'A': ([0.0, 0.02, 0.04], [1, 1, 1]), 'B': ([0.0, 0.03, 0.06], [1, 1, 1]),
           'C': ([0.0, 0.03, 0.06], [1, 1, 2]), 'D': ([0.0, 0.02, 0.04, 0.06], [1, 1, 1, 1])}
TPS = [0.01, 0.02, 0.03]
STOPS = [0.10, 0.15]
HOLDS = [24, 72]
CFG = [(s, tp, sl, h) for s in SCHEMES for tp in TPS for sl in STOPS for h in HOLDS]
A20 = 'DOGE XRP ADA AVAX LINK LTC BCH DOT SUI OP ARB APT NEAR FIL AAVE UNI TRX WLD INJ ETC'.split()
B20 = 'ZEC PUMP STRK HYPE PEPE TRUMP BNB ENA TAO ONDO ZRO GRASS VIRTUAL PENGU ICP HBAR XLM SHIB FET WIF'.split()
SETS = {'挑选': [('/home/user/ext/of/fp', c) for c in ('BTC', 'ETH', 'SOL')] + [('/home/user/ext/of/fp2', c) for c in A20],
        '验证1换时间': [('/home/user/ext/of/fp_old', c) for c in A20],
        '验证2换币': [('/home/user/ext/of/fp_alt2', c) for c in B20]}
SPLIT = {'挑选': 1782864000000, '验证1换时间': 1767225600000, '验证2换币': 1782864000000}
OUT = '/home/user/ext/of/dca'


@njit(cache=True)
def sim(om, mo, mh, ml, mc, i0s, sides, lv, wt, tp, sl, hold_min):
    n = len(om); k = len(i0s); W = wt.sum()
    rets = np.empty(k); ts = np.empty(k, np.int64); m = 0; busy = -1
    for q in range(k):
        i0 = i0s[q]
        if i0 <= busy or i0 >= n:
            continue
        s = sides[q]
        e0 = mo[i0] * (1 + s * SLIP)
        qty = wt[0]; cost = wt[0] * e0; fees = wt[0] * (FEE)
        nxt = 1
        st = e0 * (1 - s * sl)
        end = om[i0] + hold_min
        ex = -1.0; j = i0
        while j < n and om[j] < end:
            if (ml[j] <= st) if s > 0 else (mh[j] >= st):
                ex = (min(mo[j], st) if s > 0 else max(mo[j], st)) * (1 - s * STOP_SLIP)
                break
            added = False
            while nxt < len(lv):
                px = e0 * (1 - s * lv[nxt])
                if (ml[j] <= px) if s > 0 else (mh[j] >= px):
                    f = min(mo[j], px) if s > 0 else max(mo[j], px)
                    f = f * (1 + s * SLIP)
                    qty += wt[nxt]; cost += wt[nxt] * f; fees += wt[nxt] * FEE
                    nxt += 1; added = True
                else:
                    break
            avg = cost / qty
            tg = avg * (1 + s * tp)
            if not added and ((mh[j] >= tg) if s > 0 else (ml[j] <= tg)):
                ex = tg
                break
            j += 1
        if ex < 0:
            j = min(j, n) - 1
            ex = mc[j] * (1 - s * SLIP)
        avg = cost / qty
        pnl = qty * s * (ex / avg - 1) - fees - qty * FEE
        rets[m] = pnl / W; ts[m] = om[i0]; m += 1
        busy = j
    return rets[:m], ts[:m]


def coin(job):
    name, root, c = job
    d = B.load(root, c)
    om = d['om'].astype(np.int64); mo, mh, ml, mc = (d[k].astype(np.float64) for k in 'ohlc')
    idx = {int(x): i for i, x in enumerate(om)}
    rng = np.random.default_rng(abs(hash(c + name)) % 2**32)
    split = SPLIT[name] // 60000
    out = {}
    for tf in (5, 15, 60):
        bars, row = B.build_bars(d, tf)
        det = Detector(row, {}, enabled=None)
        sig = {}
        for bar in bars:
            for s in det.on_bar(bar):
                st = bar.t // 60000 + tf
                if st in idx:
                    sig.setdefault(s.kind, []).append((idx[st], s.side))
        for kind, L in sig.items():
            i0s = np.array([x[0] for x in L], np.int64); sides = np.array([x[1] for x in L], np.float64)
            for ci, (sc, tp, sl, h) in enumerate(CFG):
                lv = np.array(SCHEMES[sc][0]); wt = np.array(SCHEMES[sc][1], np.float64)
                r, t = sim(om, mo, mh, ml, mc, i0s, sides, lv, wt, tp, sl, h * 60)
                gp = gl = 0.0
                for _ in range(5):
                    ri = np.sort(rng.integers(0, len(om) - 1, len(L)))
                    rr, _ = sim(om, mo, mh, ml, mc, ri, rng.permutation(sides), lv, wt, tp, sl, h * 60)
                    gp += rr[rr > 0].sum(); gl -= rr[rr < 0].sum()
                out[(tf, kind, ci)] = (r.astype(np.float32), t >= split, gp, gl)
    return name, c, out


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    jobs = [(nm, r, c) for nm, L in SETS.items() for r, c in L if not os.path.exists(f'{OUT}/{nm}_{c}.pkl')]
    with Pool(4) as p:
        for nm, c, o in p.imap_unordered(coin, jobs):
            pickle.dump(o, open(f'{OUT}/{nm}_{c}.pkl', 'wb')); print(nm, c, len(o), flush=True)
