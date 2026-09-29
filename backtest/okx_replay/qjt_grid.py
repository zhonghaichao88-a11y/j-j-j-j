"""全参数网格：严格递归（5m 一次分析出 L0/L1/L2）+ 区间套 + 主观规则。
外层（需重跑缠论）：笔模式 × 背驰面积 × 背驰力度阈值；
每个候选入场记录全部入场特征，并对 3 种止损缓冲 × 6 种出场/仓位管理 各模拟一次，
过滤类参数（层数、点位类型、MACD回0轴、强势、宽度、方向）在评估时组合。
输出 grid_rows.parquet（每行 = 一个结构变体下的一个候选入场）。
"""
import json, os, sys, itertools, time
from multiprocessing import Pool
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qjt3 as Q
from alpha_v7_chan import analyze, zone_snapshot

M5 = Q.M5
# 笔模式, 背驰面积, 背驰阈值, 线段模式(0严格/1宽松)。严格线段在 5m 两年上大级别只有约 2 个中枢，只保留一组做对照。
STRUCT = [(p, m, r, 1) for p, m, r in itertools.product((0, 1), ('same', 'abs'), (0.7, 0.8, 0.9))] + [(0, 'same', 0.9, 0)]
STOPS = (0.25, 0.5, 1.0)
MGMT = (('X1', 'X1', {}), ('X2', 'X2', {}), ('X3', 'X3', {}), ('X1_P', 'X1', dict(part=True)),
        ('X1_T', 'X1', dict(tday=True)), ('X1_PT', 'X1', dict(part=True, tday=True)))


class Lv(Q.Lv):
    def __init__(self, f, hist, dif, level, macd_mode, ratio):
        super().__init__(f, hist, dif, level); self.mode = macd_mode; self.ratio = ratio

    def power(self, t0, t1, side):
        m = (self.ts >= t0) & (self.ts <= t1); h = self.hist[m]
        if self.mode == 'abs': return float(np.sum(np.abs(h)))
        return float(np.sum(np.maximum(h, 0))) if side > 0 else float(np.sum(np.maximum(-h, 0)))

    def candidate(self, T, i):
        """同 qjt3，但阈值可调，并额外返回“中枢期间 DIF 是否穿过 0 轴”。"""
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
        m = (self.ts >= z['ts']) & (self.ts <= z['to']); d = self.dif[m]
        zero = bool(len(d) and d.min() <= 0 <= d.max())
        if (ext < z['low']) if buy else (ext > z['high']):
            e_i = z['start'] - 1
            if e_i >= 0 and units[e_i]['side'] == side and len(units) >= 2:
                prior = units[-2]
                new_ext = prior['side'] == side and (ext < prior['b']['price'] if buy else ext > prior['b']['price'])
                pc = self.power(last['to'], self.ts[i], side); pb = self.power(units[e_i]['ts'], units[e_i]['to'], side)
                if new_ext and pb > 0 and pc < self.ratio * pb:
                    trend = len(zs) >= 2 and ((z['gg'] < zs[-2]['dd']) if buy else (z['dd'] > zs[-2]['gg']))
                    return ('T1' if trend else 'T1P', 1 if buy else -1, z, zero)
        dep = last
        if dep['side'] == -side and z['start'] + 2 <= n - 2:
            left = dep['b']['price'] > z['high'] if dep['side'] > 0 else dep['b']['price'] < z['low']
            origin = dep['low'] <= z['high'] and dep['high'] >= z['low']
            held = ext > z['high'] if dep['side'] > 0 else ext < z['low']
            if left and origin and held: return ('T3', dep['side'], z, zero)
        return None


def run(args):
    inst, (pen, mode, ratio, seg) = args
    f = Q.load5(inst)
    if f is None or len(f['ts']) < Q.MIN_BARS: return []
    t0 = time.time()
    dif, hist = Q.macd(f['close'])
    r = analyze(f, hist, max_level=2, signal_level=0, divergence_ratio=ratio, pen_mode=pen, macd_mode=mode, seg_mode=seg)
    if len(r['levels']) < 3: return []
    L = {k: Lv(f, hist, dif, r['levels'][k], mode, ratio) for k in (1, 2)}
    sig = Q.signals_by_level(r); a5 = Q.atr(f)
    h1 = Q.resample(f, max(1, 3600000 // M5), M5); a1 = Q.atr(h1)
    a1h = a1[np.clip(np.searchsorted(h1['ts'] + 3600000, f['ts'] + M5, side='right') - 1, 0, len(a1) - 1)]
    rows = []
    for known, evs in sorted(sig[0].items()):
        i = int(np.searchsorted(f['ts'], known)); T = known + M5
        c2 = L[2].candidate(T, i); c1 = L[1].candidate(T, i)
        for e in evs:
            if e.get('kind') not in ('T1', 'T1P'): continue
            d = e['side']
            big = c2 if c2 and c2[1] == d else None; mid = c1 if c1 and c1[1] == d else None
            if not big and not mid: continue
            base = big or mid; z = dict(base[2]); z['kind'] = base[0]
            row = dict(inst=inst, ts=int(known), pen=pen, macd=mode, ratio=ratio, seg=seg, side=d, small=e['kind'],
                       big=big[0] if big else '', mid=mid[0] if mid else '',
                       big_zero=bool(big and big[3]), mid_zero=bool(mid and mid[3]))
            for sb in STOPS:
                stop = e['invalidation'] - d * sb * a5[i]
                if d * (f['close'][i] - stop) <= 0: continue
                risk = abs(f['close'][i] - stop) / f['close'][i]; row[f's{sb}_risk'] = risk
                for name, ex, kw in MGMT:
                    g, h, turn = Q.manage(f, i, d, stop, z, sig, a1h, ex, **kw)
                    row[f's{sb}_{name}'] = g; row[f's{sb}_{name}_h'] = h; row[f's{sb}_{name}_c'] = Q.COST / 2 * turn / risk
            rows.append(row)
            break
    print(f'{inst} pen={pen} {mode} {ratio} seg={seg}: {len(rows)} 候选 {time.time() - t0:.0f}s', flush=True)
    return rows


if __name__ == '__main__':
    import pandas as pd
    uni = json.load(open(os.path.join(Q.DATA, 'universe_5m40.json')))
    uni = [u for u in uni if os.path.exists(os.path.join(Q.DATA, f'{u}_{Q.FILE_SUFFIX}.npz'))]
    jobs = [(u, s) for u in uni for s in STRUCT]
    with Pool(4) as p:
        rows = [r for part in p.imap_unordered(run, jobs) for r in part]
    pd.DataFrame(rows).to_parquet(os.path.join(Q.DATA, 'grid_rows_raw.parquet'))   # 先落盘，后面出错也不丢
    rows = Q.market_features(rows, uni)
    pd.DataFrame(rows).to_parquet(os.path.join(Q.DATA, 'grid_rows.parquet'))
    print('rows', len(rows))
