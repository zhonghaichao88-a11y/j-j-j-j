"""严格逐级递归 + 三层区间套 + 主观部分规则化（离线研究，只用当时已知的数据）。

一次 5m 缠论分析得到三个递归级别：L0 笔、L1 线段（5m 级别走势）、L2 线段的线段（更高一级）。
入场（区间套）：L2 形成中的一笔是背驰段候选（或第三类回抽）→ L1 形成中的一笔同向背驰
             → L0 确认同向 1买/盘背1买，在该 5m K 收盘入场。
变体：三层（L2+L1+L0）、只用大级别（L2+L0）、只用中级别（L1+L0）。
止损：L0 信号转折点外 0.5 个 5m ATR（区间套本来的精确止损）。
出场：X1 中级别同级别分解（L1 反向买卖点）、X2 大级别同级别分解（L2 反向买卖点）、
      X3 走势终完美（回到大级别中枢边界后保本 + 3 倍 1h ATR 移动止损），均最长 60 天。
主观规则（在 X1 基础上叠加，逐项比较）：
  P 分批：首笔 1/3，L1 出现同向 2买/类2买 加 1/3，L1 出现同向 3买 再加 1/3；整体止损不动，1买低点破位不再加仓。
  T 中枢做差价：持仓中 L0 反向 1卖/盘背1卖 且价格高于入场价 → 减 1/3，下一个 L0 同向 1买/2买 → 买回。
  S 强势选币与市场宽度：见 filters()。
结果以“首笔风险 R”计（分批时总风险仍按首笔止损距离×满仓计算，保持可比）。
用法: python3 qjt3.py
"""
import json, os, sys
from multiprocessing import Pool
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_v7 as B
from alpha_v7_chan import analyze, zone_snapshot
from qjt import ema, atr, macd, resample

DATA = B.DATA
M5 = int(os.environ.get('QJT_BAR_MS', 300000))          # 基础K线毫秒（默认5m；可设30m用于冒烟测试）
FILE_SUFFIX = os.environ.get('QJT_SUFFIX', '5m2y')
MIN_BARS = int(os.environ.get('QJT_MIN_BARS', 50000))
MAX_BARS = 60 * 86400000 // M5
COST = 0.002


def load5(inst):
    p = os.path.join(DATA, f'{inst}_{FILE_SUFFIX}.npz')
    if not os.path.exists(p): return None
    z = np.load(p); f = {k: z[k] for k in ('ts', 'open', 'high', 'low', 'close', 'volume')}
    cuts = np.flatnonzero(np.diff(f['ts']) != M5) + 1
    a, b = max(zip(np.r_[0, cuts], np.r_[cuts, len(f['ts'])]), key=lambda x: x[1] - x[0])
    return {k: v[a:b] for k, v in f.items()}


class Lv:
    """某个递归级别在时刻 T 的已知状态。units/zones 来自同一次分析；时间都用 5m K 的 ts，收盘后才算知道。"""
    def __init__(self, f, hist, dif, level):
        self.f = f; self.hist = hist; self.dif = dif; self.ts = f['ts']
        self.units = level['units']; self.zones = level['centers']
        self.known = np.array([u['known_at'] + M5 for u in self.units]) if self.units else np.zeros(0)

    def power(self, t0, t1, side):
        m = (self.ts >= t0) & (self.ts <= t1); h = self.hist[m]
        return float(np.sum(np.maximum(h, 0))) if side > 0 else float(np.sum(np.maximum(-h, 0)))

    def candidate(self, T, i):
        """时刻 T（第 i 根 5m 刚收盘）该级别的背驰段候选：('T1'|'T1P'|'T3', 方向, 中枢) 或 None。"""
        n = int(np.searchsorted(self.known, T, side='right'))
        if n < 4: return None
        units = self.units[:n]; last = units[-1]; side = -last['side']
        s = int(np.searchsorted(self.ts, last['to']))
        if s > i: return None
        ext = float(self.f['low'][s:i + 1].min() if side < 0 else self.f['high'][s:i + 1].max())
        zs = [zone_snapshot(z, T - M5) for z in self.zones if z['known_at'] + M5 <= T]
        zs = [z for z in zs if z and z['start'] < n]
        if not zs: return None
        z = zs[-1]; buy = side < 0
        if (ext < z['low']) if buy else (ext > z['high']):
            e_i = z['start'] - 1
            if e_i >= 0 and units[e_i]['side'] == side and len(units) >= 2:
                prior = units[-2]
                new_ext = prior['side'] == side and (ext < prior['b']['price'] if buy else ext > prior['b']['price'])
                pc = self.power(last['to'], self.ts[i], side); pb = self.power(units[e_i]['ts'], units[e_i]['to'], side)
                if new_ext and pb > 0 and pc < 0.9 * pb:
                    trend = len(zs) >= 2 and ((z['gg'] < zs[-2]['dd']) if buy else (z['dd'] > zs[-2]['gg']))
                    return ('T1' if trend else 'T1P', 1 if buy else -1, z)
        dep = last
        if dep['side'] == -side and z['start'] + 2 <= n - 2:
            left = dep['b']['price'] > z['high'] if dep['side'] > 0 else dep['b']['price'] < z['low']
            origin = dep['low'] <= z['high'] and dep['high'] >= z['low']
            held = ext > z['high'] if dep['side'] > 0 else ext < z['low']
            if left and origin and held: return ('T3', dep['side'], z)
        return None


def signals_by_level(r):
    out = {0: {}, 1: {}, 2: {}}
    for e in r['signals'] + r.get('display_signals', []):
        out.setdefault(e['level'], {}).setdefault(e['known_at'], []).append(e)
    return out


def manage(f, j, d, stop, z, sig, a1h, mode, part=False, tday=False):
    """从第 j 根收盘入场。mode: X1/X2/X3。part=分批，tday=中枢做差价。返回 (R, 持有小时)。"""
    entry = f['close'][j]; risk = abs(entry - stop); n = len(f['close']); end = min(n - 1, j + MAX_BARS)
    size = 1 / 3 if part else 1.0; avg = entry; realized = 0.0; adds = set(); turn = size   # turn=累计成交量（满仓=1）
    sl = stop; best = entry; trail = None; reached = False; parked = 0.0   # parked=做差价时暂时卖出的仓位
    low_ok = True
    def pnl(px): return d * (px - avg) * size / risk
    for k in range(j + 1, end + 1):
        o, h, l, c = f['open'][k], f['high'][k], f['low'][k], f['close'][k]; t = int(f['ts'][k])
        adverse = l if d == 1 else h; fav = h if d == 1 else l
        for lvl_stop in (sl, trail):
            if lvl_stop is not None and d * (adverse - lvl_stop) <= 0:
                px = o if d * (o - lvl_stop) <= 0 else lvl_stop
                return realized + pnl(px), (k - j) * M5 / 3600000, turn + size
        best = max(best, fav) if d == 1 else min(best, fav)
        s0 = sig[0].get(t, []); s1 = sig[1].get(t, []); s2 = sig[2].get(t, [])
        # 出场规则
        if mode == 'X1' and any(e['side'] == -d for e in s1): return realized + pnl(c), (k - j) * M5 / 3600000, turn + size
        if mode == 'X2' and any(e['side'] == -d for e in s2): return realized + pnl(c), (k - j) * M5 / 3600000, turn + size
        if mode == 'X3':
            target = z['low'] if d == 1 else z['high']
            if not reached and d * (fav - (target if z['kind'] != 'T3' else entry + d * risk)) >= 0:
                reached = True; sl = entry if d * (entry - sl) > 0 else sl
            if reached:
                cand = best - d * 3 * a1h[k]
                trail = cand if trail is None else (max(trail, cand) if d == 1 else min(trail, cand))
        # 分批加仓：L1 同向 2买/类2买、3买；1买低点未破
        if part and low_ok:
            for e in s1:
                if e['side'] != d: continue
                grp = '2' if e.get('kind') in ('T2', 'T2S') else '3' if e.get('kind') == 'T3' else None
                if grp and grp not in adds and size < 0.999 and d * (c - stop) > 0:
                    avg = (avg * size + c * (1 / 3)) / (size + 1 / 3); size += 1 / 3; adds.add(grp); turn += 1 / 3
        # 中枢做差价：L0 反向 1类且在盈利 → 减 1/3；L0 同向 1/2 类 → 买回
        if tday:
            if not parked and size > 0.34 and d * (c - avg) > 0 and any(e['side'] == -d and e.get('kind') in ('T1', 'T1P') for e in s0):
                parked = size / 3; realized += d * (c - avg) * parked / risk; size -= parked; turn += parked
            elif parked and any(e['side'] == d and e.get('kind') in ('T1', 'T1P', 'T2', 'T2S') for e in s0):
                avg = (avg * size + c * parked) / (size + parked); size += parked; turn += parked; parked = 0.0
    return realized + pnl(f['close'][end]), (end - j) * M5 / 3600000, turn + size


def run(inst):
    f = load5(inst)
    if f is None or len(f['ts']) < MIN_BARS: return []
    dif, hist = macd(f['close'])
    r = analyze(f, hist, max_level=2, signal_level=0)
    if len(r['levels']) < 3: return []
    L = {k: Lv(f, hist, dif, r['levels'][k]) for k in (0, 1, 2)}
    sig = signals_by_level(r); a5 = atr(f)
    h1 = resample(f, max(1, 3600000 // M5), M5); a1 = atr(h1); a1h = a1[np.clip(np.searchsorted(h1['ts'] + 3600000, f['ts'] + M5, side='right') - 1, 0, len(a1) - 1)]
    rows = []; busy = -1
    for known, evs in sorted(sig[0].items()):
        i = int(np.searchsorted(f['ts'], known))
        if i <= busy: continue
        T = known + M5
        c2 = L[2].candidate(T, i); c1 = L[1].candidate(T, i)
        for e in evs:
            if e.get('kind') not in ('T1', 'T1P'): continue
            d = e['side']
            combos = {'L2+L1+L0': c2 and c1 and c2[1] == d and c1[1] == d and c1[0] in ('T1', 'T1P'),
                      'L2+L0': c2 and c2[1] == d, 'L1+L0': c1 and c1[1] == d}
            if not any(combos.values()): continue
            base = c2 if combos['L2+L0'] else c1
            z = dict(base[2]); z['kind'] = base[0]
            stop = e['invalidation'] - d * 0.5 * a5[i]
            if d * (f['close'][i] - stop) <= 0: continue
            risk = abs(f['close'][i] - stop) / f['close'][i]
            row = dict(inst=inst, ts=int(known), side=d, big_kind=c2[0] if c2 and c2[1] == d else '', mid_kind=c1[0] if c1 and c1[1] == d else '',
                       risk=risk, costR=COST / risk, **{k: bool(v) for k, v in combos.items()})
            for name, mode, kw in (('X1', 'X1', {}), ('X2', 'X2', {}), ('X3', 'X3', {}), ('X1_P', 'X1', dict(part=True)),
                                   ('X1_T', 'X1', dict(tday=True)), ('X1_PT', 'X1', dict(part=True, tday=True))):
                g, hours, turn = manage(f, i, d, stop, z, sig, a1h, mode, **kw)
                # 成本按实际成交量：单边成本 COST/2 × 成交量 ÷ 首笔风险
                row[name] = g; row[name + '_h'] = hours; row[name + '_cost'] = COST / 2 * turn / risk
            rows.append(row); busy = i + int(row['X1_h'] * 3600000 / M5)
            break
    return rows


def market_features(rows, universe):
    """强势选币与市场宽度：只用信号时刻之前的日线收盘。"""
    daily = {}
    for inst in universe:
        f = load5(inst)
        if f is None: continue
        dly = resample(f, 86400000 // M5, M5)
        if len(dly['close']) < 60: continue          # 日线太少（数据有缺口或上市太晚）不参与排名
        daily[inst] = (dly['ts'], dly['close'], ema(dly['close'], 50))
    for r in rows:
        t = r['ts']; rets = {}; above = []
        for inst, (ts, c, e) in daily.items():
            k = int(np.searchsorted(ts + 86400000, t, side='right')) - 1
            if k >= 30: rets[inst] = c[k] / c[k - 30] - 1; above.append(c[k] > e[k])
        if r['inst'] in rets and len(rets) >= 10:
            rank = sorted(rets.values()).index(rets[r['inst']]) / (len(rets) - 1)
            r['rs'] = rank if r['side'] == 1 else 1 - rank           # 顺方向的相对强弱，1=最强
            r['breadth'] = float(np.mean(above)) if r['side'] == 1 else 1 - float(np.mean(above))
        else:
            r['rs'] = np.nan; r['breadth'] = np.nan
    return rows


if __name__ == '__main__':
    uni = json.load(open(os.path.join(DATA, 'universe_5m40.json')))
    with Pool(4) as p:
        rows = [r for part in p.map(run, uni) for r in part]
    rows = market_features(rows, uni)
    json.dump(rows, open(os.path.join(DATA, 'qjt3.json'), 'w'))
    print('entries', len(rows))
