"""旧打法完整重测（事先定好规则，再看结果）。
进场：订单流里全部旧打法的信号（Detector 全开），5 / 15 / 60 分钟三种周期；K线收盘出信号，下一根第一分钟开盘价进场。
出场（全部组合都测）：
  A. 原版止损（信号自己的结构止损），止盈 = 1 / 2 / 3 倍止损距离，最多拿 48 根K线
  B. 固定止损 2% / 3% / 5% × 止盈 不设 / 1% / 2% / 3% / 5% / 8% × 最多拿 4 / 12 / 24 / 48 小时
  同一分钟既碰止损又碰止盈，按止损算。每个打法在每个币上同时只拿一单。
成本：每边吃单 0.05% + 滑点 0.02%；止损再加 0.05% 滑点。
随机对照：同样的出场、同样单数、同样多空和止损距离，随机时间进场，10 次。
数据：欧易逐笔足迹 2026-04-01 ~ 2026-09-30。
  挑选：BTC / ETH / SOL（之前就在用的 3 个币）
  验证：20 个没用过的币（DOGE XRP ADA AVAX LINK LTC BCH DOT SUI OP ARB APT NEAR FIL AAVE UNI TRX WLD INJ ETC）
过关（事先定好）：
  挑选期：≥50 笔、PF ≥ 1.1、前半（7 月以前）和后半都 ≥ 1.0、比随机高 0.1 以上
  验证期：同一组参数原样拿到 20 个新币上，≥100 笔、PF ≥ 1.1、前后半都 ≥ 1.0、比随机高 0.1、一半以上的币赚钱"""
import sys, os, pickle, numpy as np
from numba import njit
from multiprocessing import Pool
sys.path.insert(0, '/home/user/j-j-j-j/orderflow')
import of_backtest as B
from of_core import Detector, SIGNAL_NAMES
FEE, SLIP, STOP_SLIP = 0.0005, 0.0002, 0.0005
SPLIT = 1782864000000 // 60000          # 2026-07-01，分钟
TRAIN = ['BTC', 'ETH', 'SOL']
TEST = ['DOGE', 'XRP', 'ADA', 'AVAX', 'LINK', 'LTC', 'BCH', 'DOT', 'SUI', 'OP', 'ARB', 'APT', 'NEAR', 'FIL', 'AAVE', 'UNI', 'TRX', 'WLD', 'INJ', 'ETC']
EXITS = [('原版', 0.0, rr, 0) for rr in (1.0, 2.0, 3.0)] + \
        [('固定', sp, tp, h) for sp in (0.02, 0.03, 0.05) for tp in (0.0, 0.01, 0.02, 0.03, 0.05, 0.08) for h in (4, 12, 24, 48)]
REPS = 10


@njit(cache=True)
def sim(om, mo, mh, ml, mc, i0s, sides, sdist, fixed, sp, tp, hold_min):
    n = len(om); k = len(i0s)
    rets = np.empty(k); ts = np.empty(k, np.int64); m = 0; busy = -1
    for q in range(k):
        i0 = i0s[q]
        if i0 <= busy or i0 >= n:
            continue
        side = sides[q]
        e = mo[i0] * (1 + side * SLIP)
        if fixed:
            st = e * (1 - side * sp)
            tg = e * (1 + side * tp) if tp > 0 else np.nan
        else:
            st = e * (1 - side * sdist[q])
            tg = e * (1 + side * tp * sdist[q])
        end = om[i0] + hold_min
        ex = np.nan; j = i0
        while j < n and om[j] < end:
            if (ml[j] <= st) if side > 0 else (mh[j] >= st):
                ex = (min(mo[j], st) if side > 0 else max(mo[j], st)) * (1 - side * STOP_SLIP)
                break
            if tg == tg and ((mh[j] >= tg) if side > 0 else (ml[j] <= tg)):
                ex = tg
                break
            j += 1
        if ex != ex:
            j = min(j, n) - 1
            ex = mc[j] * (1 - side * SLIP)
        rets[m] = side * (ex / e - 1) - 2 * FEE; ts[m] = om[i0]; m += 1
        busy = j
    return rets[:m], ts[:m]


def coin(args):
    sym, root = args
    d = B.load(root, sym)
    om = d['om'].astype(np.int64); mo, mh, ml, mc = (d[k].astype(np.float64) for k in 'ohlc')
    idx = {int(x): i for i, x in enumerate(om)}
    rng = np.random.default_rng(abs(hash(sym)) % 2**32)
    out = {}
    for tf in (5, 15, 60):
        bars, row = B.build_bars(d, tf)
        det = Detector(row, {}, enabled=None)
        sig = {}
        for bar in bars:
            for s in det.on_bar(bar):
                st = bar.t // 60000 + tf
                if st in idx and bar.c > 0:
                    sig.setdefault(s.kind, []).append((idx[st], s.side, abs(bar.c - s.stop) / bar.c))
        for kind, L in sig.items():
            i0s = np.array([x[0] for x in L], np.int64); sides = np.array([x[1] for x in L], np.float64)
            sd = np.array([x[2] for x in L], np.float64)
            for ei, (mode, sp, tp, h) in enumerate(EXITS):
                fixed = mode == '固定'
                hold = h * 60 if fixed else 48 * tf
                r, t = sim(om, mo, mh, ml, mc, i0s, sides, sd, fixed, sp, tp, hold)
                gp = gl = 0.0
                for _ in range(REPS):
                    ri = np.sort(rng.integers(0, len(om) - 1, len(L)))
                    rs = rng.permutation(sides); rd = rng.permutation(sd)
                    rr, _ = sim(om, mo, mh, ml, mc, ri, rs, rd, fixed, sp, tp, hold)
                    gp += rr[rr > 0].sum(); gl -= rr[rr < 0].sum()
                out[(tf, kind, ei)] = (r.astype(np.float32), (t >= SPLIT), gp, gl)
    return sym, out


if __name__ == '__main__':
    which = sys.argv[1]
    syms = TRAIN if which == 'train' else TEST
    root = '/home/user/ext/of/fp' if which == 'train' else '/home/user/ext/of/fp2'
    os.makedirs('/home/user/ext/of/full', exist_ok=True)
    jobs = [(s, root) for s in syms if not os.path.exists(f'/home/user/ext/of/full/{s}.pkl')]
    with Pool(int(sys.argv[2]) if len(sys.argv) > 2 else 3) as p:
        for sym, out in p.imap_unordered(coin, jobs):
            pickle.dump(out, open(f'/home/user/ext/of/full/{sym}.pkl', 'wb'))
            print(sym, 'done', len(out), flush=True)
