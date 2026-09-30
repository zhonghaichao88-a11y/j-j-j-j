"""方案一 + 原文分批建仓：一买 1/3 → 二买 1/3 → 三买 1/3（同一方向、同一波），15m 笔级。
每一批按自己的止损算风险，风险各占总风险的 1/3；成本往返 0.2%，每批单独计。
过滤：F全 = 每一批都要 4h EMA50 + BTC EMA200 顺势；F三 = 只有三买要顺势，一买二买不看（抄底本来就是逆 4h 趋势）。
       每一批止损距离都要 ≥1.72%（与方案一相同）。
出场：E方案一 = 移动止损（最近一批入场后浮盈≥最近一批的 1R 启动，回撤 2ATR，盘中触发）+ 距最后一次加仓 192 根到期；
      E原文   = 出现反向笔级买卖点（任意类型）平仓 + 距最后一次加仓 192 根到期。
两种出场都有止损：整笔止损 = 最近一批的止损（只上移不下移）。
基准：只做三买、满仓（=方案一），用来核对本脚本和系统回放一致。
用法: python3 staged.py [15m|15m_old]
"""
import json, os, sys
from multiprocessing import Pool
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_v7 as B
from research import ema, atr, higher_ema
A = B.A
MS = 900000; COST = 0.002; MIN_RISK = 0.0172; HOLD = 192
STAGE = {'T1': 1, 'T1P': 1, 'T2': 2, 'T2S': 2, 'T3': 3}
SUF = sys.argv[1] if len(sys.argv) > 1 else '15m'
VARIANTS = {'基准_只三买满仓': dict(base=True, filt='all', exit='trail'),
            'F全_E方案一': dict(filt='all', exit='trail'), 'F全_E原文': dict(filt='all', exit='opp'),
            'F三_E方案一': dict(filt='t3', exit='trail'), 'F三_E原文': dict(filt='t3', exit='opp')}


def load(inst):
    p = os.path.join(B.DATA, f'{inst}_{SUF}.npz')
    if not os.path.exists(p): return []
    z = np.load(p); f = {k: z[k] for k in ('ts', 'open', 'high', 'low', 'close', 'volume')}
    cuts = np.flatnonzero(np.diff(f['ts']) != MS) + 1
    return [{k: v[a:b] for k, v in f.items()} for a, b in zip(np.r_[0, cuts], np.r_[cuts, len(f['ts'])]) if b - a >= 1600]


BTC = {}
def btc_dir():
    for g in load('BTC-USDT-SWAP'):
        e = ema(g['close'], 200)
        for t, c, v in zip(g['ts'], g['close'], e): BTC[int(t)] = float(np.sign(c - v))


class Camp:
    def __init__(s, d): s.d = d; s.legs = []; s.stop = None; s.stages = set(); s.last = 0; s.trail = None; s.act = None; s.anchor = None; s.dist = None


def settle(c, px):
    """平掉整个持仓，返回 (R 合计, 用掉的风险 R, 批数, 各批阶段)。每批风险 = 1/3（基准为 1）。"""
    tot = 0.0; used = 0.0
    for L in c.legs:
        g = c.d * (px - L['px']) / L['px'] - COST
        tot += L['w'] * g / L['risk']; used += L['w']
    return dict(R=tot, used=used, legs=len(c.legs), stages=''.join(str(L['st']) for L in c.legs))


def one(inst):
    out = {k: [] for k in VARIANTS}
    for f in load(inst):
        a14 = atr(f); h4, _ = higher_ema(f, 16, 50); ts = f['ts']
        o, h, l, cl = f['open'], f['high'], f['low'], f['close']
        state = {k: None for k in VARIANTS}
        for s, a, b in B.anchors(len(ts)):
            F = {k: v[s:b + 1] for k, v in f.items()}
            res = A._orig_chan(F, signal_level=0)
            byj = {}
            for e in res['signals']:
                jj = int(np.searchsorted(F['ts'], e['known_at']))
                if jj >= len(F['ts']) or F['ts'][jj] != e['known_at'] or not (a - s <= jj <= b - s): continue
                st = res['signal_status'].get(e['id'], {})
                if st.get('invalidated_at') is not None and st['invalidated_at'] <= e['known_at']: continue
                if e['side'] * (F['close'][jj] - e['invalidation']) <= 0: continue
                byj.setdefault(s + jj, []).append(e)
            for j in range(a, b + 1):
                evs = byj.get(j, [])
                if len({e['side'] for e in evs}) > 1: evs = []     # 同一根多空冲突：跳过
                for name, V in VARIANTS.items():
                    c = state[name]
                    # 1) 管理持仓（本根内）
                    if c is not None:
                        d = c.d; adv = l[j] if d == 1 else h[j]; fav = h[j] if d == 1 else l[j]; px = None
                        if d * (adv - c.stop) <= 0: px = o[j] if d * (o[j] - c.stop) <= 0 else c.stop
                        elif c.trail is not None and d * (adv - c.trail) <= 0: px = o[j] if d * (o[j] - c.trail) <= 0 else c.trail
                        if px is None and V['exit'] == 'trail':
                            if c.anchor is None and d * (fav - c.act) >= 0: c.anchor = fav
                            elif c.anchor is not None: c.anchor = max(c.anchor, fav) if d == 1 else min(c.anchor, fav)
                            if c.anchor is not None:
                                c.trail = c.anchor - d * c.dist
                                if d * (cl[j] - c.trail) <= 0: px = c.trail           # 盘中触发
                        if px is None and j - c.last >= HOLD: px = cl[j]
                        if px is None and V['exit'] == 'opp' and any(e['side'] == -d for e in evs): px = cl[j]
                        if px is not None:
                            r = settle(c, px); r.update(inst=inst, ts=int(ts[c.legs[0]['j']]), side=d); out[name].append(r)
                            state[name] = c = None
                    # 2) 本根确认的买卖点：开仓或加仓
                    for e in evs:
                        d = e['side']; stg = STAGE.get(e.get('kind'))
                        if stg is None: continue
                        if V.get('base') and stg != 3: continue
                        if c is not None and (c.d != d or stg in c.stages or stg < max(c.stages)): continue
                        px = cl[j]; at = a14[j]; stop = e['invalidation'] - d * at; risk = d * (px - stop) / px
                        if risk < MIN_RISK or risk > 0.20: continue
                        need = V['filt'] == 'all' or stg == 3
                        if need:
                            if not np.isfinite(h4[j]) or np.sign(px - h4[j]) != d: continue
                            if BTC.get(int(ts[j])) != d: continue
                        if c is None: c = state[name] = Camp(d); c.stop = stop
                        else: c.stop = max(c.stop, stop) if d == 1 else min(c.stop, stop)
                        c.legs.append(dict(px=px, risk=risk, w=1.0 if V.get('base') else 1 / 3, st=stg, j=j))
                        c.stages.add(stg); c.last = j
                        # 移动止损按最近一批重新设定：浮盈达到最近一批 1R 启动，回撤 2ATR
                        c.act = px + d * risk * px; c.dist = 2 * at; c.anchor = None; c.trail = None
                        break
        for name, c in state.items():   # 数据结束仍持仓：不计（与研究一致，只统计完整结果）
            pass
    return out


if __name__ == '__main__':
    B._init(); btc_dir()
    cats = json.load(open(os.path.join(B.DATA, 'categories.json')))
    uni = [i for i, c in cats.items() if c == '1']
    with Pool(4, initializer=B._init) as p:
        parts = p.map(one, uni)
    res = {k: [r for part in parts for r in part[k]] for k in VARIANTS}
    json.dump(res, open(os.path.join(B.DATA, f'staged_{SUF}.json'), 'w'), ensure_ascii=False)
    print({k: len(v) for k, v in res.items()})
