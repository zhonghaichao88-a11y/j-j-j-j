"""缠论“级别 + 区间套”研究（离线、只用当时已知的数据）。

大级别（4h 或 1d，由 30m 合成）：
  用“已确认的笔 + 正在形成的这一笔”判断背驰段候选：
  - 1类（趋势背驰/盘整背驰）：正在形成的笔离开最后中枢并创新低（高），
    其同向 MACD 面积小于进入该中枢的那一笔（b）的 0.9 倍；
    可选“中枢期间 MACD 快线(DIF)穿过 0 轴”（原文：黄白线回拉 0 轴）。
  - 3类：离开中枢后，正在形成的回抽笔没有回到中枢核心内。
小级别（30m 或 4h）：
  候选成立期间，小级别确认同方向的 1买 / 盘背1买（区间套里的“次级别背驰”）→ 在该K收盘入场。
止损：小级别信号的转折点外 0.5 个小级别 ATR；3类另加“大级别收盘回到中枢核心内”即认错。
出场（同时记录，事先定好，统一比较）：
  E1 同级别分解：大级别出现反向买卖点（任一类）才走；
  E2 出场也用区间套：小级别出现反向 1卖/盘背1卖 就走；
  E3 走势终完美：价格回到大级别中枢边界后（3类为盈利 1R 后），止损移到成本，改用 3 倍大级别 ATR 移动止损；
  以上都受止损约束，最长持有 60 天。
用法: python3 qjt.py 大级别(4h/1d)
"""
import json, os, sys
from multiprocessing import Pool
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_v7 as B
from alpha_v7_chan import analyze, zone_snapshot

DATA = B.DATA
M30 = 1800000
LEVELS = {'4h': (8, '30m'), '1d': (48, '4h')}   # 大级别: (合成倍数, 小级别)
MAX_DAYS = 60
COST = 0.002


def ema(x, n):
    out = np.empty(len(x)); out[0] = x[0]; a = 2 / (n + 1)
    for i in range(1, len(x)): out[i] = out[i - 1] + a * (x[i] - out[i - 1])
    return out


def atr(f, n=14):
    c, h, l = f['close'], f['high'], f['low']; pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc))); out = np.empty(len(c)); out[0] = tr[0]
    for i in range(1, len(c)): out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


def resample(f, k, ms):
    b = f['ts'] // (k * ms) * (k * ms)
    keys, start, cnt = np.unique(b, return_index=True, return_counts=True)
    keep = cnt == k
    s = start[keep]; c = cnt[keep]
    return dict(ts=keys[keep].astype(np.int64), open=f['open'][s],
                high=np.maximum.reduceat(f['high'], start)[keep], low=np.minimum.reduceat(f['low'], start)[keep],
                close=f['close'][s + c - 1], volume=np.add.reduceat(f['volume'], start)[keep])


def load30(inst):
    p = os.path.join(DATA, f'{inst}_30m.npz')
    if not os.path.exists(p): return None
    z = np.load(p); f = {k: z[k] for k in ('ts', 'open', 'high', 'low', 'close', 'volume')}
    cuts = np.flatnonzero(np.diff(f['ts']) != M30) + 1
    seg = max(zip(np.r_[0, cuts], np.r_[cuts, len(f['ts'])]), key=lambda x: x[1] - x[0])   # 最长连续段
    return {k: v[seg[0]:seg[1]] for k, v in f.items()}


def macd(c):
    dif = ema(c, 12) - ema(c, 26); return dif, dif - ema(dif, 9)


class Level:
    """一个级别的完整缠论结果 + 按“某时刻已知”查询的工具。"""
    def __init__(self, f, ms):
        self.f = f; self.ms = ms; self.ts = f['ts']
        self.dif, self.hist = macd(f['close']); self.atr = atr(f)
        self.r = analyze(f, self.hist, signal_level=0)
        self.units = self.r['levels'][0]['units']; self.zones = self.r['levels'][0]['centers']
        self.unit_known = np.array([u['known_at'] + ms for u in self.units])        # 收盘后才知道
        self.sig_by_known = {}
        for e in self.r['signals']: self.sig_by_known.setdefault(e['known_at'], []).append(e)

    def closed_upto(self, T):
        """时刻 T 之前已收盘的最后一根的下标。"""
        return int(np.searchsorted(self.ts + self.ms, T, side='right')) - 1

    def power(self, t0, t1, side):
        m = (self.ts >= t0) & (self.ts <= t1); h = self.hist[m]
        return float(np.sum(np.maximum(h, 0))) if side > 0 else float(np.sum(np.maximum(-h, 0)))

    def state(self, T):
        """时刻 T 的大级别状态：已确认笔、已知中枢、正在形成的一笔（起点 → 目前极值）。"""
        n = int(np.searchsorted(self.unit_known, T, side='right'))
        if n < 4: return None
        units = self.units[:n]; last = units[-1]
        i = self.closed_upto(T)
        if i < 0: return None
        side = -last['side']                                   # 形成中这一笔的方向
        start_t = last['to']; s = int(np.searchsorted(self.ts, start_t))
        seg_hi = self.f['high'][s:i + 1]; seg_lo = self.f['low'][s:i + 1]
        if not len(seg_hi): return None
        ext = float(seg_lo.min() if side < 0 else seg_hi.max())
        zones = [zone_snapshot(z, T - self.ms) for z in self.zones if z['known_at'] + self.ms <= T]
        zones = [z for z in zones if z]
        zones = [z for z in zones if z['start'] < n]
        return dict(n=n, units=units, side=side, start_t=start_t, start_px=last['b']['price'], ext=ext, i=i, zones=zones)


def big_candidates(big, T, zero_axis):
    """返回 (类型, 方向, 中枢) 或 None。类型：T1 趋势背驰 / T1P 盘整背驰 / T3 第三类回抽。"""
    st = big.state(T)
    if not st or not st['zones']: return None
    z = st['zones'][-1]; side = st['side']; units = st['units']; ext = st['ext']
    buy = side < 0                                          # 向下的形成笔 → 找买点
    # 1类：离开最后中枢并创新低/高，力度弱于进入段
    outside = ext < z['low'] if buy else ext > z['high']
    if outside:
        entry_i = z['start'] - 1
        if entry_i >= 0 and units[entry_i]['side'] == side:
            prior = units[-1 - 1] if len(units) >= 2 else None   # 上一根同向（已确认）笔
            new_ext = prior is not None and prior['side'] == side and (ext < prior['b']['price'] if buy else ext > prior['b']['price'])
            p_c = big.power(st['start_t'], big.ts[st['i']], side); p_b = big.power(units[entry_i]['ts'], units[entry_i]['to'], side)
            if new_ext and p_b > 0 and p_c < 0.9 * p_b:
                if zero_axis:
                    m = (big.ts >= z['ts']) & (big.ts <= z['to']); d = big.dif[m]
                    if not (len(d) and d.min() <= 0 <= d.max()): return None
                trend = False
                if len(st['zones']) >= 2:
                    p = st['zones'][-2]; trend = (z['gg'] < p['dd']) if buy else (z['dd'] > p['gg'])
                return ('T1' if trend else 'T1P', 1 if buy else -1, z)
    # 3类：上一根已确认笔离开中枢，形成中的回抽笔不回到核心
    dep = units[-1]
    if dep['side'] == -side and z['start'] + 2 <= len(units) - 2:
        left = dep['b']['price'] > z['high'] if dep['side'] > 0 else dep['b']['price'] < z['low']
        origin = dep['low'] <= z['high'] and dep['high'] >= z['low']
        held = ext > z['high'] if dep['side'] > 0 else ext < z['low']
        if left and origin and held:
            return ('T3', dep['side'], z)
    return None


def simulate_exit(small, big, j, d, stop, z, kind):
    """从小级别第 j 根收盘入场，按 E1/E2/E3 分别给出 (R, 持有小时数)。"""
    f = small.f; entry = f['close'][j]; risk = abs(entry - stop); n = len(f['close'])
    end = min(n - 1, j + int(MAX_DAYS * 86400000 / small.ms))
    res = {}
    for name in ('E1', 'E2', 'E3'):
        sl = stop; best = entry; trail = None; reached = False; out = None
        for k in range(j + 1, end + 1):
            o, h, l, c = f['open'][k], f['high'][k], f['low'][k], f['close'][k]; T = int(f['ts'][k]) + small.ms
            adverse = l if d == 1 else h; fav = h if d == 1 else l
            if d * (adverse - sl) <= 0:
                px = o if d * (o - sl) <= 0 else sl; out = (d * (px - entry) / risk, k); break
            if trail is not None and d * (adverse - trail) <= 0:
                px = o if d * (o - trail) <= 0 else trail; out = (d * (px - entry) / risk, k); break
            best = max(best, fav) if d == 1 else min(best, fav)
            if kind == 'T3':   # 3类失效：大级别收盘回到中枢核心内
                bi = big.closed_upto(T)
                if bi >= 0 and big.ts[bi] + big.ms == T and (big.f['close'][bi] < z['high'] if d == 1 else big.f['close'][bi] > z['low']):
                    out = (d * (c - entry) / risk, k); break
            if name == 'E1':
                bi = big.closed_upto(T)
                if bi >= 0 and big.ts[bi] + big.ms == T:
                    evs = [e for e in big.sig_by_known.get(int(big.ts[bi]), []) if e['side'] == -d]
                    if evs: out = (d * (c - entry) / risk, k); break
            elif name == 'E2':
                evs = [e for e in small.sig_by_known.get(int(f['ts'][k]), []) if e['side'] == -d and e.get('kind') in ('T1', 'T1P')]
                if evs: out = (d * (c - entry) / risk, k); break
            else:
                # 1类：回到大级别中枢边界（走势终完美的最低要求）；3类本来就在中枢外，改为盈利 1R 后启动
                target = (z['low'] if d == 1 else z['high']) if kind != 'T3' else entry + d * risk
                if not reached and d * (fav - target) >= 0:
                    reached = True; sl = entry if d * (entry - sl) > 0 else sl
                if reached:
                    bi = big.closed_upto(T); a = big.atr[bi] if bi >= 0 else small.atr[k] * 4
                    cand = best - d * 3 * a
                    trail = cand if trail is None else (max(trail, cand) if d == 1 else min(trail, cand))
        if out is None: out = (d * (f['close'][end] - entry) / risk, end)
        res[name] = (float(out[0]), float((out[1] - j) * small.ms / 3600000))
    return res, risk / entry


def run(args):
    inst, lev = args
    k, small_tf = LEVELS[lev]
    f30 = load30(inst)
    if f30 is None or len(f30['ts']) < 3000: return []
    if small_tf == '30m': fs, sms = f30, M30
    else: fs, sms = resample(f30, 8, M30), 8 * M30
    fb = resample(f30, k, M30); bms = k * M30
    if len(fb['ts']) < 200: return []
    small = Level(fs, sms); big = Level(fb, bms)
    rows = []; busy_until = -1
    for known, evs in sorted(small.sig_by_known.items()):
        j = int(np.searchsorted(fs['ts'], known))
        if j <= busy_until: continue                      # 同一币持仓期间不重复开
        T = known + sms
        cands = {z0: big_candidates(big, T, z0) for z0 in (False, True)}
        c = cands[False]
        if not c: continue
        kind, d, z = c
        ev = [e for e in evs if e['side'] == d and e.get('kind') in ('T1', 'T1P')]
        if not ev: continue
        e = ev[0]; a = small.atr[j]
        stop = e['invalidation'] - d * 0.5 * a
        if d * (fs['close'][j] - stop) <= 0: continue
        out, risk_pct = simulate_exit(small, big, j, d, stop, z, kind)
        rows.append(dict(inst=inst, ts=int(known), kind=kind, zero_axis=cands[True] is not None, side=d, small_kind=e['kind'],
                         risk=risk_pct, costR=COST / risk_pct, **{f'{k2}_R': v[0] for k2, v in out.items()},
                         **{f'{k2}_h': v[1] for k2, v in out.items()}))
        busy_until = j + int(out['E1'][1] * 3600000 / sms)   # 以 E1 的持有期作为占用（最保守的长持有）
    return rows


if __name__ == '__main__':
    lev = sys.argv[1] if len(sys.argv) > 1 else '4h'
    cats = json.load(open(os.path.join(DATA, 'categories.json')))
    uni = [i for i, c in cats.items() if c == '1']
    with Pool(4) as p:
        rows = [r for part in p.map(run, [(i, lev) for i in uni]) for r in part]
    json.dump(rows, open(os.path.join(DATA, f'qjt_{lev}.json'), 'w'))
    print(lev, 'entries', len(rows))
