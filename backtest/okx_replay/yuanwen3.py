"""原文全套（第三版）：15m 为本级别，5m 线段级别走势为次级别，15m 线段的线段为大级别。
相对第二版的改动（全部按原文）：
 1. 二买/类二买：区间套早进场——1买后本级别走出一段反弹，回抽段里次级别出现 1买且不破 1买低点即进（类2买最多两次，且低点不高于反弹段高点）。
 2. 一买只认趋势背驰（本级别两个不重叠中枢 + 次级别也是趋势背驰 1买）；"含盘背"作为对照变体。
 3. 背驰判断：MACD 同向面积 < 进入段的 90%，且黄白线(DIF)在中枢期间回抽 0 轴附近，且离开段斜率（幅度/时间）小于进入段。
 4. （撤回）次级别改用 5m 线段：实测 5m 线段与 15m 线段大小相近（ETH 两年 610 段 vs 372 段），不是次级别；
    原文级别按结构递归（笔→线段→线段的线段），同一周期内递归本来就是对的，所以仍用 L0 笔级为次级别。
 5. 中枢延伸到 9 段 = 升级为大一级中枢：此时本级别 1买/1卖 必须大级别同时是背驰段才算。
    中枢扩展（两中枢 GG/DD 重叠）按原文不算趋势，只能是盘整背驰（严格版不做）。
 6. 小转大：本级别没有背驰，但次级别 1买 的低点后来成了本级别新一段的起点 → 这个低点当作 1买 低点，
    之后按 2买/3买 介入（原文：小转大只能在 2、3 类买点介入）；卖出方向同理，按 2卖/3卖 离场。
 7. 同级别分解：另一套操作（只按 15m 线段：向上线段确认做多、向下线段确认离场/做空）。
卖出、止损、分批、做差价同第二版；大级别卖点清仓。不用移动止损、不看 BTC、不加均线过滤。
主观部分的规则化（在结果上筛选/组合模拟，见 yw3_eval.py）：市场宽度（大盘环境）、相对强度（板块/强势）、资金轮动（组合上限内优先最强）。
用法: python3 yuanwen3.py 5m2y|15m|15m_old（L0=笔 次级别，L1=线段 本级别，L2=线段的线段 大级别）
"""
import json, os, sys
from multiprocessing import Pool
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_v7 as B
from qjt import macd, resample
from alpha_v7_chan import analyze, zone_snapshot
SUF = sys.argv[1] if len(sys.argv) > 1 else '5m2y'
MS = 300000 if SUF.startswith('5m') else 900000; SIDE_COST = 0.001; MAXH = 60 * 86400000 // MS
VARIANTS = {'严格_二层': dict(loose=False, three=False, tday=False),
            '严格_三层': dict(loose=False, three=True, tday=False),
            '严格_二层_做差价': dict(loose=False, three=False, tday=True),
            '含盘背_二层': dict(loose=True, three=False, tday=False),
            '含盘背_三层': dict(loose=True, three=True, tday=False)}


def load(inst):
    p = os.path.join(B.DATA, f'{inst}_{SUF}.npz')
    if not os.path.exists(p): return []
    z = np.load(p); f = {k: z[k] for k in ('ts', 'open', 'high', 'low', 'close', 'volume')}
    cuts = np.flatnonzero(np.diff(f['ts']) != MS) + 1
    return [{k: v[a:b] for k, v in f.items()} for a, b in zip(np.r_[0, cuts], np.r_[cuts, len(f['ts'])]) if b - a >= 3000]


class Lv:
    """某一级别在时刻 T（K 线收盘时间）已知的结构。ms=该级别所在周期。"""
    def __init__(self, f, dif, hist, level, ms):
        self.f = f; self.dif = dif; self.hist = hist; self.ts = f['ts']; self.ms = ms
        self.units = level['units']; self.zones = level['centers']
        self.known = np.array([u['known_at'] + ms for u in self.units], dtype=np.int64) if self.units else np.zeros(0, np.int64)

    def n(self, T): return int(np.searchsorted(self.known, T, side='right'))

    def power(self, t0, t1, side):
        m = (self.ts >= t0) & (self.ts <= t1); h = self.hist[m]
        return float(np.sum(np.maximum(h, 0))) if side > 0 else float(np.sum(np.maximum(-h, 0)))

    def candidate(self, T, i):
        """第 i 根（本周期）收盘时：('T1'|'T1P', 方向, 中枢, info) 背驰段候选，或 ('T3', 方向, 中枢, {})。"""
        n = self.n(T)
        if n < 4: return None
        units = self.units[:n]; last = units[-1]; side = -last['side']      # side: 正在走的这段的方向
        s = int(np.searchsorted(self.ts, last['to']))
        if s > i: return None
        ext = float(self.f['low'][s:i + 1].min() if side < 0 else self.f['high'][s:i + 1].max())
        zs = [zone_snapshot(z, T - self.ms) for z in self.zones if z['known_at'] + self.ms <= T]
        zs = [z for z in zs if z and z['start'] < n]
        if not zs: return None
        z = zs[-1]; buy = side < 0
        if (ext < z['low']) if buy else (ext > z['high']):
            e_i = z['start'] - 1
            if e_i >= 0 and units[e_i]['side'] == side and len(units) >= 2:
                prior = units[-2]; ent = units[e_i]
                new_ext = prior['side'] == side and (ext < prior['b']['price'] if buy else ext > prior['b']['price'])
                pc = self.power(last['to'], self.ts[i], side); pb = self.power(ent['ts'], ent['to'], side)
                if new_ext and pb > 0 and pc < 0.9 * pb:
                    trend = len(zs) >= 2 and ((z['gg'] < zs[-2]['dd']) if buy else (z['dd'] > zs[-2]['gg']))
                    # 黄白线回抽 0 轴：中枢期间 DIF 回到进入段最大偏离的 10% 以内或穿越 0 轴
                    m_in = (self.ts >= ent['ts']) & (self.ts <= ent['to']); m_z = (self.ts > ent['to']) & (self.ts <= last['to'])
                    dmax = float(np.max(np.abs(self.dif[m_in]))) if m_in.any() else 0.0
                    dz = self.dif[m_z]
                    zero_ok = bool(len(dz)) and (float(np.min(np.abs(dz))) <= 0.1 * dmax or bool(np.any(dz * side <= 0)))
                    # 力度（斜率）：离开段每根K线的幅度 < 进入段
                    bars_in = max(1, int(np.searchsorted(self.ts, ent['to']) - np.searchsorted(self.ts, ent['ts'])))
                    slope_in = abs(ent['b']['price'] - ent['a']['price']) / bars_in
                    slope_out = abs(ext - last['b']['price']) / max(1, i - s + 1)
                    info = dict(zero_ok=zero_ok, slope_ok=slope_out < slope_in, upgraded=(z['end'] - z['start'] + 1) >= 9)
                    return ('T1' if trend else 'T1P', 1 if buy else -1, z, info)
        dep = last
        if dep['side'] == -side and z['start'] + 2 <= n - 2:
            left = dep['b']['price'] > z['high'] if dep['side'] > 0 else dep['b']['price'] < z['low']
            origin = dep['low'] <= z['high'] and dep['high'] >= z['low']
            held = ext > z['high'] if dep['side'] > 0 else ext < z['low']
            if left and origin and held: return ('T3', dep['side'], z, {})
        return None


def by_T(r, ms):
    out = {}
    for e in r['signals'] + r.get('display_signals', []):
        out.setdefault(e['level'], {}).setdefault(e['known_at'] + ms, []).append(e)
    return out


class Pos:
    def __init__(s, d): s.d = d; s.N = 0.0; s.avg = 0.0; s.pnl = 0.0; s.gross = 0.0; s.used = 0.0; s.stages = ''; s.stop = None; s.small2big = False
    def buy(s, px, q):
        s.avg = (s.avg * s.N + px * q) / (s.N + q); s.N += q; s.pnl -= SIDE_COST * q
    def sell(s, px, frac):
        q = s.N * frac; g = q * s.d * (px / s.avg - 1); s.pnl += g - SIDE_COST * q; s.gross += g; s.N -= q


def div_ok(c, loose):
    """本级别背驰段候选是否算 1买/1卖：严格=趋势背驰 + 回抽0轴 + 斜率变小。"""
    if c is None or c[0] not in ('T1', 'T1P'): return False
    if not loose and c[0] != 'T1': return False
    info = c[3]
    return loose or (info['zero_ok'] and info['slope_ok'])


def one(inst):
    out = {k: [] for k in list(VARIANTS) + ['同级别分解']}
    for f in load(inst):
        run(inst, f, out)
    return out


def run(inst, f, out):
    dif, hist = macd(f['close'])
    r = analyze(f, hist, max_level=2, signal_level=0)
    if len(r['levels']) < 2: return
    S = by_T(r, MS); SUB = S.get(0, {})
    L1 = Lv(f, dif, hist, r['levels'][1], MS)
    L2 = Lv(f, dif, hist, r['levels'][2], MS) if len(r['levels']) > 2 else None
    ts = f['ts']; o, h, l, c = f['open'], f['high'], f['low'], f['close']
    known1 = {}
    for u, k in zip(L1.units, L1.known): known1.setdefault(int(k), []).append(u)
    st = {k: dict(P=None, p1={1: None, -1: None}, p1T={1: 0, -1: 0}, sold1=None, park=0.0, last=0, n2={1: 0, -1: 0}, got3={1: False, -1: False}) for k in VARIANTS}
    subturn = {1: [], -1: []}          # 最近的次级别 1买低点 / 1卖高点（小转大用）
    seg = dict(P=None)                 # 同级别分解
    def close_pos(name, P, px, t, why):
        P.sell(px, 1.0); out[name].append(dict(inst=inst, start=P.start, end=t, side=P.d, stages=P.stages, exit=why, pnl=P.pnl, gross=P.gross, used=P.used, small2big=P.small2big))
    for i in range(len(ts)):
        T = int(ts[i]) + MS
        sub = SUB.get(T, []); s1 = S.get(1, {}).get(T, []); s2 = S.get(2, {}).get(T, [])
        newL1 = known1.get(T, [])
        subB = {dd: [e for e in sub if e['side'] == dd and e.get('kind') == 'T1'] for dd in (1, -1)}
        subBL = {dd: [e for e in sub if e['side'] == dd and e.get('kind') in ('T1', 'T1P')] for dd in (1, -1)}
        for dd in (1, -1):
            for e in subBL[dd]: subturn[dd] = (subturn[dd] + [e['invalidation']])[-6:]
        need = sub or s1 or s2 or newL1
        c1 = L1.candidate(T, i) if need else None
        c2 = L2.candidate(T, i) if need and L2 is not None else None
        # —— 同级别分解：线段确认即跟随；该线段起点作保护止损 ——
        P = seg['P']
        if P is not None and P.d * ((l[i] if P.d == 1 else h[i]) - P.stop) <= 0:
            close_pos('同级别分解', P, o[i] if P.d * (o[i] - P.stop) <= 0 else P.stop, T, '止损'); seg['P'] = None
        for u in newL1:
            P = seg['P']; dd = u['side']
            if P is not None and P.d != dd:
                close_pos('同级别分解', P, c[i], T, '反向线段确认'); seg['P'] = P = None
            if P is None:
                stop = u['a']['price']; risk = dd * (c[i] - stop) / c[i]
                if risk > 0.0005:
                    P = Pos(dd); P.start = T; P.stop = stop; P.buy(c[i], 1 / risk); P.used = 1.0; P.stages = 'S'; seg['P'] = P
        for name, V in VARIANTS.items():
            X = st[name]; P = X['P']; SB = subBL if V['loose'] else subB
            for dd in (1, -1):
                if X['p1'][dd] is not None and dd * ((l[i] if dd == 1 else h[i]) - X['p1'][dd]) < 0: X['p1'][dd] = None; X['n2'][dd] = 0; X['got3'][dd] = False
            # 小转大：新确认的本级别线段起点正好是次级别 1买/1卖 的转折点
            for u in newL1:
                dd = u['side']; a = u['a']['price']
                if any(abs(a - x) <= 1e-12 * max(1.0, abs(a)) for x in subturn[dd]):
                    if X['p1'][dd] is None:
                        X['p1'][dd] = a; X['p1T'][dd] = T; X['n2'][dd] = 0; X['got3'][dd] = False; X.setdefault('s2b', {})[dd] = True
                    if P is not None and P.d == -dd and X['sold1'] is None: X['sold1'] = a      # 卖出方向的小转大：之后按 2卖/3卖 离场
            # 止损
            if P is not None:
                d = P.d; adv = l[i] if d == 1 else h[i]
                if d * (adv - P.stop) <= 0:
                    close_pos(name, P, o[i] if d * (o[i] - P.stop) <= 0 else P.stop, T, '止损'); X['P'] = P = None
            # 卖点
            if P is not None:
                d = P.d; ss = SB[-d]; conf = [e for e in s1 if e['side'] == -d]; act = None
                big_ok = c2 is not None and c2[1] == -d and (c2[0] == 'T3' or div_ok(c2, V['loose']))
                if any(e['side'] == -d for e in s2) or (ss and big_ok): act = '大级别卖点'
                elif (ss and c1 and c1[1] == -d and c1[0] == 'T3') or any(e.get('kind') == 'T3' for e in conf): act = '3卖'
                elif X['sold1'] is not None and ((ss and d * (ss[0]['price'] - X['sold1']) < 0) or any(e.get('kind') in ('T2', 'T2S') for e in conf)): act = '2卖'
                elif X['sold1'] is None and ((ss and c1 and c1[1] == -d and div_ok(c1, V['loose']) and (not c1[3]['upgraded'] or big_ok))
                                             or any(e.get('kind') in (('T1', 'T1P') if V['loose'] else ('T1',)) for e in conf)): act = '1卖'
                if act == '1卖':
                    P.sell(c[i], 0.5); X['sold1'] = ss[0]['price'] if ss else c[i]
                elif act:
                    close_pos(name, P, c[i], T, act); X['P'] = P = None
                elif V['tday'] and X['sold1'] is None and subBL[-d] and not X['park'] and d * (c[i] - P.avg) > 0:
                    X['park'] = P.N / 3; P.sell(c[i], 1 / 3)
                elif V['tday'] and X['park'] and (subBL[d] or [e for e in sub if e['side'] == d and e.get('kind') in ('T2', 'T2S')]):
                    P.buy(c[i], X['park']); X['park'] = 0.0
            if P is not None and i - X['last'] >= MAXH:
                close_pos(name, P, c[i], T, '到期'); X['P'] = P = None
            # 买点（区间套：本级别位置 + 次级别 1买）
            for d in (1, -1):
                if not SB[d]: continue
                if P is not None and (P.d != d or X['sold1'] is not None): continue
                e0 = SB[d][0]; stage = None; stop = None
                big_ok = c2 is not None and c2[1] == d and div_ok(c2, V['loose'])
                if c1 and c1[1] == d and div_ok(c1, V['loose']) and (not c1[3]['upgraded'] or big_ok) and (not V['three'] or big_ok):
                    stage = '1'; stop = e0['invalidation']; X['p1'][d] = stop; X['p1T'][d] = T; X['n2'][d] = 0; X['got3'][d] = False; X.setdefault('s2b', {})[d] = False
                elif c1 and c1[1] == d and c1[0] == 'T3':
                    stage = '3'; z = c1[2]; stop = z['high'] if d == 1 else z['low']; X['got3'][d] = True
                elif X['p1'][d] is not None and not X['got3'][d] and X['n2'][d] < 3 and d * (e0['invalidation'] - X['p1'][d]) > 0:
                    nn = L1.n(T); last = L1.units[nn - 1] if nn else None
                    # 1买后本级别已走出一段同向反弹（已确认），现在是回抽段
                    if last is not None and last['side'] == d and L1.known[nn - 1] > X['p1T'][d] and d * (e0['invalidation'] - (last['high'] if d == 1 else last['low'])) < 0:
                        stage = '2' if X['n2'][d] == 0 else 's'; stop = X['p1'][d]; X['n2'][d] += 1
                if stage is None: continue
                ORD = {'1': 1, '2': 2, 's': 2.5, '3': 3}
                if P is not None and (ORD[stage] < max(ORD[x] for x in P.stages) or (stage != 's' and stage in P.stages)): continue
                risk = d * (c[i] - stop) / c[i]
                if risk <= 0.0005: continue
                if P is None:
                    P = X['P'] = Pos(d); P.start = T; P.stop = stop; X['sold1'] = None; X['park'] = 0.0
                    P.small2big = bool(X.get('s2b', {}).get(d)) and stage in '2s3'
                elif stage in '12s':
                    P.stop = X['p1'][d] if X['p1'][d] is not None else P.stop
                P.buy(c[i], (1 / 3) / risk); P.used += 1 / 3; P.stages += stage; X['last'] = i
                break
    T = int(ts[-1]) + MS
    for name, X in st.items():
        if X['P'] is not None: close_pos(name, X['P'], c[-1], T, '数据结束')
    if seg['P'] is not None: close_pos('同级别分解', seg['P'], c[-1], T, '数据结束')


if __name__ == '__main__':
    cats = json.load(open(os.path.join(B.DATA, 'categories.json')))
    uni = [i for i, cc in cats.items() if cc == '1' and os.path.exists(os.path.join(B.DATA, f'{i}_{SUF}.npz'))]
    with Pool(4) as p:
        parts = [x for x in p.map(one, uni) if x]
    res = {k: [r for part in parts for r in part[k]] for k in parts[0]}
    json.dump(res, open(os.path.join(B.DATA, f'yuanwen3_{SUF}.json'), 'w'), ensure_ascii=False)
    print(len(parts), {k: len(v) for k, v in res.items()})
