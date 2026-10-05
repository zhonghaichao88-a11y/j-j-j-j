"""大单跟随的各种定义（事先定好，再看结果）：
  倍数：这根K线大单净买(卖) ≥ 过去 50 根平均的 2 / 3 / 5 / 10 倍
  确认：①收在K线上(下)四分之一 + 突破前 5 根高(低)点（程序现在用的）②只要收在上(下)四分之一 ③不要确认
  方向：跟着大单做 / 反着做
  周期：5 / 15 / 60 分钟
  出场：原版（K线低点下一格止损，止盈 1 / 2 / 3 倍，最多 48 根）+ 固定止损 3% / 5% × 止盈 不设 / 2% / 5% × 拿 12 / 48 小时
大单门槛：下载时定的（BTC 20 万U、ETH 10 万、SOL 5 万、山寨 2.5 万），数据里只存了每分钟合计，没法再改门槛。
挑选：23 个币（BTC ETH SOL + 20 山寨 A）2026-04 ~ 09
验证 1：20 山寨 A 2025-10 ~ 2026-03（换时间）；验证 2：另外 20 山寨 B 2026-04 ~ 09（换币）
过关：≥100 笔、PF ≥ 1.1、前后半都 ≥ 1、比随机进场高 0.1、一半以上的币赚钱；挑选和两个验证都要过。"""
import sys, os, pickle, numpy as np
from multiprocessing import Pool
sys.path.insert(0, '/home/user/j-j-j-j/orderflow'); sys.path.insert(0, '/home/user/j-j-j-j/orderflow/分析/策略实验室/旧打法完整重测')
import of_backtest as B
from of_core import auto_row_size
from full_bt import sim
MULTS = [2.0, 3.0, 5.0, 10.0]
CONFS = ['收高位+突破', '只收高位', '不要确认']
EXITS = [('原版', 0.0, rr, 0) for rr in (1.0, 2.0, 3.0)] + [('固定', sp, tp, h) for sp in (0.03, 0.05) for tp in (0.0, 0.02, 0.05) for h in (12, 48)]
A20 = 'DOGE XRP ADA AVAX LINK LTC BCH DOT SUI OP ARB APT NEAR FIL AAVE UNI TRX WLD INJ ETC'.split()
B20 = 'ZEC PUMP STRK HYPE PEPE TRUMP BNB ENA TAO ONDO ZRO GRASS VIRTUAL PENGU ICP HBAR XLM SHIB FET WIF'.split()
SETS = {'挑选': [('/home/user/ext/of/fp', c) for c in ('BTC', 'ETH', 'SOL')] + [('/home/user/ext/of/fp2', c) for c in A20],
        '验证1换时间': [('/home/user/ext/of/fp_old', c) for c in A20],
        '验证2换币': [('/home/user/ext/of/fp_alt2', c) for c in B20]}
SPLIT = {'挑选': 1782864000000, '验证1换时间': 1767225600000, '验证2换币': 1782864000000}
OUT = '/home/user/ext/of/bigvar'


def bars(d, tf):
    om = d['om']; k = om // tf
    u, first = np.unique(k, return_index=True)
    last = np.r_[first[1:], len(om)] - 1
    o = d['o'][first]; c = d['c'][last]
    h = np.maximum.reduceat(d['h'], first); l = np.minimum.reduceat(d['l'], first)
    bb = np.zeros(len(u)); bs = np.zeros(len(u))
    if len(d['big_m']):
        pos = np.searchsorted(u, d['big_m'] // tf)
        ok = (pos < len(u)) & (u[np.minimum(pos, len(u) - 1)] == d['big_m'] // tf)
        np.add.at(bb, pos[ok], d['big_buy'][ok]); np.add.at(bs, pos[ok], d['big_sell'][ok])
    return u, o, h, l, c, bb, bs


def coin(job):
    name, root, c = job
    d = B.load(root, c)
    om = d['om'].astype(np.int64); mo, mh, ml, mc = (d[k].astype(np.float64) for k in 'ohlc')
    idx = {int(x): i for i, x in enumerate(om)}
    rng = np.random.default_rng(abs(hash(c + name)) % 2**32)
    split = SPLIT[name] // 60000
    out = {}
    for tf in (5, 15, 60):
        u, o, h, l, cl, bb, bs = bars(d, tf)
        row = auto_row_size(list((h - l)[:7 * 1440 // tf]), float(d['tick']))
        net = bb - bs
        n = len(u)
        avg = np.full(n, np.nan)
        cs = np.r_[0, np.cumsum(np.abs(net))]
        for i in range(50, n):
            avg[i] = (cs[i] - cs[i - 50]) / 50
        hi5 = np.full(n, np.nan); lo5 = np.full(n, np.nan)
        for i in range(5, n):
            hi5[i] = h[i - 5:i].max(); lo5[i] = l[i - 5:i].min()
        rg = h - l
        for mult in MULTS:
            big = (avg > 0) & (np.abs(net) >= mult * avg)
            for conf in CONFS:
                top = cl >= h - 0.25 * rg; bot = cl <= l + 0.25 * rg
                if conf == '收高位+突破':
                    L = big & (net > 0) & top & (cl > hi5); S = big & (net < 0) & bot & (cl < lo5)
                elif conf == '只收高位':
                    L = big & (net > 0) & top; S = big & (net < 0) & bot
                else:
                    L = big & (net > 0); S = big & (net < 0)
                ent = []
                for i in np.where(L | S)[0]:
                    st = int(u[i]) * tf + tf
                    if st in idx and cl[i] > 0:
                        side = 1.0 if L[i] else -1.0
                        stop = l[i] - row if side > 0 else h[i] + row
                        ent.append((idx[st], side, max(abs(cl[i] - stop) / cl[i], 1e-4)))
                if not ent:
                    continue
                i0s = np.array([e[0] for e in ent], np.int64); sd = np.array([e[2] for e in ent])
                for dirn in ('跟着做', '反着做'):
                    sides = np.array([e[1] for e in ent]) * (1 if dirn == '跟着做' else -1)
                    for ei, (mode, sp, tp, hh) in enumerate(EXITS):
                        fixed = mode == '固定'; hold = hh * 60 if fixed else 48 * tf
                        r, t = sim(om, mo, mh, ml, mc, i0s, sides, sd, fixed, sp, tp, hold)
                        gp = gl = 0.0
                        for _ in range(5):
                            ri = np.sort(rng.integers(0, len(om) - 1, len(ent)))
                            rr_, _ = sim(om, mo, mh, ml, mc, ri, rng.permutation(sides), rng.permutation(sd), fixed, sp, tp, hold)
                            gp += rr_[rr_ > 0].sum(); gl -= rr_[rr_ < 0].sum()
                        out[(tf, mult, conf, dirn, ei)] = (r.astype(np.float32), t >= split, gp, gl)
    return name, c, out


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    jobs = [(nm, r, c) for nm, L in SETS.items() for r, c in L if not os.path.exists(f'{OUT}/{nm}_{c}.pkl')]
    with Pool(4) as p:
        for nm, c, o in p.imap_unordered(coin, jobs):
            pickle.dump(o, open(f'{OUT}/{nm}_{c}.pkl', 'wb')); print(nm, c, len(o), flush=True)
