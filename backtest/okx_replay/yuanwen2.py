"""（第二版：①类二买单独加仓 ②二买/类二买用引擎本级别已确认的信号 ③大级别卖点清仓）
按原文整套规则（15m 递归三个级别）：区间套进场、分批买、分批卖、1买低点止损、中枢震荡做差价。
不用移动止损、不看 BTC、不加均线过滤、不设最小止损距离。只用当时已知的数据。

级别：一次 15m 分析得到 L0=笔（次级别）、L1=线段（本级别，操作级别）、L2=线段的线段（大级别）。
区间套确认：本级别（L1）正在走的一段是背驰段/三类回抽候选，同时次级别（L0）在这根K线确认同向的
            1买/盘背1买（卖同理），在这根 15m 收盘成交。三层变体另外要求大级别（L2）同向背驰段候选（只对 1买）。
买（每批 1/3 风险）：
  1买：L1 背驰段候选（T1/T1P）+ L0 1买 → 买 1/3，记下 1买低点 P1；
  2买：P1 之后 L1 已走出一段反弹，回抽不破 P1，L0 出现 1买 → 加 1/3（没持仓也可以从这里开始）；
  3买：L1 离开中枢后第一次回抽不回中枢（T3 候选）+ L0 1买 → 加 1/3（没持仓也可以从这里开始）。
  每类最多一次，只能按 1→2→3 往后加；卖出一卖之后不再加。
卖：
  1卖：L1 反向背驰段候选 + L0 1卖（或 L1 已确认的 1卖）→ 卖一半；
  2卖：已卖过 1卖后，L0 再出现 1卖且高点不过 1卖（或 L1 已确认 2卖/类2卖）→ 全卖；
  3卖：L1 反向 T3 候选 + L0 1卖（或 L1 已确认 3卖）→ 全卖。
止损：有 1买/2买 仓位时，跌破 P1 全部离场；只有 3买 仓位时，回到中枢上沿（原文：3买后回中枢即失败）全部离场。
做差价（变体）：持仓且未到 1卖，L0 反向 1卖、有浮盈 → 卖 1/3；之后 L0 同向 1买/2买 → 买回。
其余：最长持有 60 天（从最后一次加仓算），数据结束按最后收盘价计。
成本：单边 0.1%（往返 0.2%）按实际成交量计。结果单位：R（每批 1/3R，汇总为“每用 1R 风险赚多少 R”）。
用法: python3 yuanwen.py 15m|15m_old|5m|5m2y（5m 为基础时：L0=5m笔、L1=5m线段、L2=5m线段的线段）
"""
import json, os, sys
SUF = sys.argv[1] if len(sys.argv) > 1 else '15m'
MS = 300000 if SUF.startswith('5m') else 900000
os.environ['QJT_BAR_MS'] = str(MS)
from multiprocessing import Pool
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_v7 as B
from qjt3 import Lv, signals_by_level
from qjt import macd
from alpha_v7_chan import analyze
SIDE_COST = 0.001; MAXH = 60 * 86400000 // MS
VARIANTS = {}
for lay in ('二层', '三层'):
    for t in (False, True):
        for lo in (False, True):
            VARIANTS[f'{lay}{"_做差价" if t else ""}{"_只做多" if lo else ""}'] = dict(three=lay == '三层', tday=t, long_only=lo)


def load(inst):
    p = os.path.join(B.DATA, f'{inst}_{SUF}.npz')
    if not os.path.exists(p): return []
    z = np.load(p); f = {k: z[k] for k in ('ts', 'open', 'high', 'low', 'close', 'volume')}
    cuts = np.flatnonzero(np.diff(f['ts']) != MS) + 1
    return [{k: v[a:b] for k, v in f.items()} for a, b in zip(np.r_[0, cuts], np.r_[cuts, len(f['ts'])]) if b - a >= 3000]


class Pos:
    def __init__(s, d, i): s.d = d; s.N = 0.0; s.avg = 0.0; s.pnl = 0.0; s.gross = 0.0; s.used = 0.0; s.stages = ''; s.stop = None
    def buy(s, px, notional):
        s.avg = (s.avg * s.N + px * notional) / (s.N + notional); s.N += notional; s.pnl -= SIDE_COST * notional
    def sell(s, px, frac):
        q = s.N * frac; g = q * s.d * (px / s.avg - 1); s.pnl += g - SIDE_COST * q; s.gross += g; s.N -= q


def one(inst):
    out = {k: [] for k in VARIANTS}
    for f in load(inst):
        dif, hist = macd(f['close'])
        r = analyze(f, hist, max_level=2, signal_level=0)
        if len(r['levels']) < 2: continue
        L = {k: Lv(f, hist, dif, r['levels'][k]) for k in range(min(3, len(r['levels'])))}
        sig = signals_by_level(r)
        ts = f['ts']; o, h, l, c = f['open'], f['high'], f['low'], f['close']; n = len(ts)
        pos = {k: None for k in VARIANTS}; aux = {k: dict(p1={1: None, -1: None}, p1_i={1: -1, -1: -1}, sold1=None, park=0.0, last=0) for k in VARIANTS}
        l1_known = L[1].known
        for i in range(n):
            t = int(ts[i]); T = t + MS
            s0 = sig[0].get(t, []); s1 = sig[1].get(t, []); s2 = sig.get(2, {}).get(t, [])
            l0buy = {d: [e for e in s0 if e['side'] == d and e.get('kind') in ('T1', 'T1P')] for d in (1, -1)}
            c1 = c2 = None
            if s0 or s1 or s2:
                c1 = L[1].candidate(T, i)
                c2 = L[2].candidate(T, i) if 2 in L else None
            for name, V in VARIANTS.items():
                P = pos[name]; X = aux[name]
                # 更新 1买低点有效性：创新低则 1买作废（对应方向）
                for d in (1, -1):
                    if X['p1'][d] is not None and d * ((l[i] if d == 1 else h[i]) - X['p1'][d]) < 0: X['p1'][d] = None
                # 1) 止损（盘中）
                if P is not None:
                    d = P.d; adv = l[i] if d == 1 else h[i]
                    if d * (adv - P.stop) <= 0:
                        px = o[i] if d * (o[i] - P.stop) <= 0 else P.stop
                        P.sell(px, 1.0); P.exit = '止损'; P.end = t; out[name].append(P.__dict__.copy()); pos[name] = P = None
                # 2) 卖点
                if P is not None:
                    d = P.d; sell0 = l0buy[-d]; conf1 = [e for e in s1 if e['side'] == -d]
                    act = None
                    if any(e['side'] == -d for e in s2) or (sell0 and c2 and c2[1] == -d):
                        act = '大级别卖点'
                    elif (sell0 and c1 and c1[1] == -d and c1[0] == 'T3') or any(e.get('kind') == 'T3' for e in conf1): act = '3卖'
                    elif X['sold1'] is not None and ((sell0 and d * (sell0[0]['price'] - X['sold1']) < 0) or any(e.get('kind') in ('T2', 'T2S') for e in conf1)): act = '2卖'
                    elif X['sold1'] is None and ((sell0 and c1 and c1[1] == -d and c1[0] in ('T1', 'T1P')) or any(e.get('kind') in ('T1', 'T1P') for e in conf1)): act = '1卖'
                    if act == '1卖':
                        P.sell(c[i], 0.5); X['sold1'] = sell0[0]['price'] if sell0 else c[i]
                    elif act:
                        P.sell(c[i], 1.0); P.exit = act; P.end = t; out[name].append(P.__dict__.copy()); pos[name] = P = None
                    elif V['tday'] and X['sold1'] is None and sell0 and not X['park'] and d * (c[i] - P.avg) > 0:
                        X['park'] = P.N / 3; P.sell(c[i], 1 / 3)
                    elif V['tday'] and X['park'] and l0buy[d] + [e for e in s0 if e['side'] == d and e.get('kind') in ('T2', 'T2S')]:
                        P.buy(c[i], X['park']); X['park'] = 0.0
                if P is not None and i - X['last'] >= MAXH:
                    P.sell(c[i], 1.0); P.exit = '到期'; P.end = t; out[name].append(P.__dict__.copy()); pos[name] = P = None
                # 3) 买点（区间套：L1 候选 + L0 同向 1买）
                for d in (1, -1):
                    if V['long_only'] and d == -1: continue
                    if not l0buy[d] and not any(e['side'] == d for e in s1): continue
                    if P is not None and (P.d != d or X['sold1'] is not None): continue
                    stage = None; stop = None; e0 = l0buy[d][0] if l0buy[d] else None
                    conf = [e for e in s1 if e['side'] == d and e.get('kind') in ('T2', 'T2S')]
                    if e0 and c1 and c1[1] == d and c1[0] in ('T1', 'T1P') and (not V['three'] or (c2 and c2[1] == d)):
                        stage = '1'; X['p1'][d] = e0['invalidation']; X['p1_i'][d] = i; stop = e0['invalidation']
                    elif e0 and c1 and c1[1] == d and c1[0] == 'T3':
                        stage = '3'; z = c1[2]; stop = z['high'] if d == 1 else z['low']
                    elif conf:
                        # 本级别（L1）引擎已确认的 2买 / 类2买；止损 = 对应 1买低点
                        e = conf[0]; stage = '2' if e['kind'] == 'T2' else 's'; stop = e['evidence']['first_price']
                    if stage is None: continue
                    ORD = {'1': 1, '2': 2, 's': 2.5, '3': 3}
                    if P is not None and (ORD[stage] < max(ORD[x] for x in P.stages) or (stage != 's' and stage in P.stages) or P.stages.count('s') >= 2): continue
                    risk = d * (c[i] - stop) / c[i]
                    if risk <= 0.0005: continue
                    if P is None:
                        P = pos[name] = Pos(d, i); P.inst = inst; P.start = t; P.stop = stop; X['sold1'] = None; X['park'] = 0.0
                    elif stage in '12s':
                        P.stop = stop if stage in '2s' else (X['p1'][d] if X['p1'][d] is not None else P.stop)
                    P.buy(c[i], (1 / 3) / risk); P.used += 1 / 3; P.stages += stage; X['last'] = i
                    break
        for name, P in pos.items():
            if P is not None:
                P.sell(c[-1], 1.0); P.exit = '数据结束'; P.end = int(ts[-1]); out[name].append(P.__dict__.copy())
    return out


if __name__ == '__main__':
    cats = json.load(open(os.path.join(B.DATA, 'categories.json')))
    uni = [i for i, c in cats.items() if c == '1']
    with Pool(4) as p:
        parts = p.map(one, uni)
    res = {k: [r for part in parts for r in part[k]] for k in VARIANTS}
    json.dump(res, open(os.path.join(B.DATA, f'yuanwen2_{SUF}.json'), 'w'), ensure_ascii=False)
    print({k: len(v) for k, v in res.items()})
