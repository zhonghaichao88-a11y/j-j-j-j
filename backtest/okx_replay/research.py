"""缠论信号研究数据集：每个确认信号的入场时特征 + 多种出场方式的结果（R，已给出成本折合）。
只用信号确认那一刻及以前的数据算特征；结果用之后的K线。
用法: python3 research.py  → research_15m.npz / research_15m.json
"""
import json, os, sys
from multiprocessing import Pool
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_v7 as B

A = B.A
TF = '15m'; MS = 900000; H = 192          # 最多往后看 192 根（2天）
COST = 0.002                               # 往返成本（价格比例）


def ema(x, n):
    out = np.empty(len(x)); out[0] = x[0]; a = 2 / (n + 1)
    for i in range(1, len(x)): out[i] = out[i - 1] + a * (x[i] - out[i - 1])
    return out


def atr(f, n=14):
    c, h, l = f['close'], f['high'], f['low']; pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc))); out = np.empty(len(c)); out[0] = tr[0]
    for i in range(1, len(c)): out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


def higher_ema(f, k, n):
    """k 根 15m 合成一根高周期；返回每根 15m 收盘时“最后一根已收盘高周期”的 EMA 与其斜率方向。"""
    ts = f['ts']; step = k * MS; b = ts // step
    last_idx = np.flatnonzero(np.r_[b[1:] != b[:-1], True])      # 每个高周期桶的最后一根
    complete = [i for i in last_idx if (ts[i] + MS) % step == 0]
    closes = f['close'][complete]; e = ema(closes, n)
    val = np.full(len(ts), np.nan); slope = np.zeros(len(ts))
    for m, i in enumerate(complete):
        end = i + 1; nxt = complete[m + 1] + 1 if m + 1 < len(complete) else len(ts)
        val[end:nxt] = e[m]; slope[end:nxt] = np.sign(e[m] - e[m - 1]) if m else 0
    return val, slope


def outcomes(f, j, d, stop, a):
    """几种出场方式的 R 结果（未扣成本）。入场=第 j 根收盘。"""
    entry = f['close'][j]; risk = abs(entry - stop); n = len(f['close'])
    hi, lo, cl = f['high'], f['low'], f['close']
    res = {}
    def run(tp_r, hold, trail_atr=None, trail_after=1.0):
        best = entry; trail = None
        for k in range(j + 1, min(j + 1 + hold, n)):
            adverse = lo[k] if d == 1 else hi[k]; fav = hi[k] if d == 1 else lo[k]
            if d * (adverse - stop) <= 0: return -1.0 if d * (f['open'][k] - stop) > 0 else d * (f['open'][k] - entry) / risk
            if trail is not None and d * (adverse - trail) <= 0: return d * (trail - entry) / risk
            if tp_r and d * (fav - (entry + d * tp_r * risk)) >= 0: return float(tp_r)
            best = max(best, fav) if d == 1 else min(best, fav)
            if trail_atr and d * (best - entry) >= trail_after * risk:
                cand = best - d * trail_atr * a
                trail = cand if trail is None else (max(trail, cand) if d == 1 else min(trail, cand))
        k = min(j + hold, n - 1)
        return d * (cl[k] - entry) / risk
    res['tp2_h48'] = run(2, 48)
    res['tp3_h96'] = run(3, 96)
    res['trail2atr_h192'] = run(None, 192, trail_atr=2.0)
    res['hold96'] = run(None, 96)
    res['complete'] = j + 192 < n
    return res


def one(inst):
    rows = []
    try:
        chunks = B.load(inst, TF)
    except Exception:
        return rows
    btc = BTC
    for f in chunks:
        a14 = atr(f); e200 = ema(f['close'], 200); e50 = ema(f['close'], 50)
        h1, h1s = higher_ema(f, 4, 50); h4, h4s = higher_ema(f, 16, 50)
        vol20 = np.convolve(f['volume'], np.ones(20) / 20, 'full')[:len(f['volume'])]
        for s, a, b in B.anchors(len(f['ts'])):
            F = {k: v[s:b + 1] for k, v in f.items()}
            res = A._orig_chan(F, signal_level=0)
            idx = {int(t): i for i, t in enumerate(F['ts'])}
            for e in res['signals']:
                jj = idx.get(e['known_at'])
                if jj is None or not (a - s <= jj <= b - s): continue
                j = s + jj; d = e['side']; c = f['close'][j]; at = a14[j]
                pivot = e['invalidation']
                st = res['signal_status'].get(e['id'], {})
                if st.get('invalidated_at') is not None and st['invalidated_at'] <= e['known_at']: continue
                if d * (c - pivot) <= 0: continue
                t = int(f['ts'][j]); bi = btc.get(t)
                row = dict(inst=inst, ts=t, label=e['label'].replace('卖', '买'), kind=e.get('kind'), side=d,
                           lag=int(jj - idx[e['ts']]), move_atr=float(d * (c - pivot) / at), atr_pct=float(at / c),
                           trend200=float(np.sign(c - e200[j]) * d), trend50=float(np.sign(e50[j] - e50[max(0, j - 4)]) * d),
                           h1=float(np.sign(c - h1[j]) * d) if np.isfinite(h1[j]) else 0.0, h1s=float(h1s[j] * d),
                           h4=float(np.sign(c - h4[j]) * d) if np.isfinite(h4[j]) else 0.0, h4s=float(h4s[j] * d),
                           ret24=float(c / f['close'][j - 96] - 1) if j >= 96 else 0.0,
                           volr=float(f['volume'][j] / max(vol20[j], 1e-12)),
                           hour=int((t // 3600000) % 24),
                           btc=float(bi * d) if bi is not None else 0.0)
                for name, sb in (('s1', 0.35), ('s2', 1.0)):
                    stop = pivot - d * sb * at; risk = abs(c - stop) / c
                    o = outcomes(f, j, d, stop, at)
                    row[name + '_risk'] = risk; row[name + '_costR'] = COST / risk
                    for k2, v in o.items():
                        if k2 != 'complete': row[f'{name}_{k2}'] = v
                    row['complete'] = o['complete']
                rows.append(row)
    return rows


def btc_regime():
    f = B.load('BTC-USDT-SWAP', TF)
    out = {}
    for g in f:
        e = ema(g['close'], 200)
        for t, c, v in zip(g['ts'], g['close'], e): out[int(t)] = float(np.sign(c - v))
    return out


BTC = {}
if __name__ == '__main__':
    B._init()
    BTC = btc_regime()
    cats = json.load(open(os.path.join(B.DATA, 'categories.json')))
    uni = [i for i, c in cats.items() if c == '1']
    with Pool(4, initializer=B._init) as p:
        rows = [r for part in p.map(one, uni) for r in part]
    json.dump(rows, open(os.path.join(B.DATA, 'research_15m.json'), 'w'))
    print('signals', len(rows))
