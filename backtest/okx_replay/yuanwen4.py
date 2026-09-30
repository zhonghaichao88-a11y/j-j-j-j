"""原文全套（第四版）：按周期分级别的区间套。
级别：大级别 = 日线（对照：4小时），本级别 = 30分钟（持仓管理、止损、卖点），次级别 = 5分钟，最小级别 = 1分钟。
每个周期用它自己的笔、笔中枢、买卖点（原文：30分钟图看30分钟级别，往下 5分钟、1分钟找精确点）。
区间套进场：
  三层 = 大级别背驰段候选 + 30分钟背驰段/三类候选 + 5分钟确认 1买（在 5分钟收盘成交）；
  四层 = 大级别 + 30分钟 + 5分钟也处在同向背驰段 + 1分钟确认 1买（在 1分钟收盘成交）。
  "无大级别"变体只用 30分钟 + 下级。
其余规则与第三版相同：一买只认趋势背驰（MACD面积 + 黄白线回抽0轴 + 斜率变小）、区间套早进场的二买/类二买、三买、
分批各 1/3、一卖卖一半、二卖/三卖/大级别卖点清仓、一买低点止损、只有三买时回中枢止损、中枢9段升级、小转大。
中枢震荡做差价（变体）、只做多（变体，原文A股只能做多）、同级别分解（30分钟笔，单独一套）。
不用移动止损、不看 BTC、不加均线过滤、不设最长持仓（原文：没有卖点就持有）。止损在最细的周期上逐根检查。
用法: python3 yuanwen4.py 5m    （只用 5m2y 数据：日线/4h/30m/5m，三层）
      python3 yuanwen4.py 1m    （用 1m2y 数据：再加 1分钟一层，四层）
"""
import json, os, sys
from multiprocessing import Pool
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_v7 as B
from qjt import macd, resample
from alpha_v7_chan import analyze
from yuanwen3 import Lv, Pos, div_ok, by_T
MODE = sys.argv[1] if len(sys.argv) > 1 else '5m'
MSOF = {'1m': 60000, '5m': 300000, '30m': 1800000, '4h': 14400000, '1d': 86400000}
V5 = {'三层_日线_30m_5m': dict(big='1d', conf='5m', mid=None, loose=False),
      '三层_日线_30m_5m_做差价': dict(big='1d', conf='5m', mid=None, loose=False, tday=True),
      '三层_日线_30m_5m_只做多': dict(big='1d', conf='5m', mid=None, loose=False, long_only=True),
      '三层_4h_30m_5m': dict(big='4h', conf='5m', mid=None, loose=False),
      '二层_30m_5m': dict(big=None, conf='5m', mid=None, loose=False),
      '含盘背_三层_日线_30m_5m': dict(big='1d', conf='5m', mid=None, loose=True)}
V1 = {'四层_日线_30m_5m_1m': dict(big='1d', conf='1m', mid='5m', loose=False),
      '四层_日线_30m_5m_1m_做差价': dict(big='1d', conf='1m', mid='5m', loose=False, tday=True),
      '四层_日线_30m_5m_1m_只做多': dict(big='1d', conf='1m', mid='5m', loose=False, long_only=True),
      '四层_4h_30m_5m_1m': dict(big='4h', conf='1m', mid='5m', loose=False),
      '三层_30m_5m_1m': dict(big=None, conf='1m', mid='5m', loose=False),
      '含盘背_四层_日线_30m_5m_1m': dict(big='1d', conf='1m', mid='5m', loose=True)}
VARIANTS = dict(V5) if MODE == '5m' else {**V5, **V1}
MAIN = '30m'


def load(inst):
    p = os.path.join(B.DATA, f'{inst}_{MODE}2y.npz')
    if not os.path.exists(p): return None
    base_ms = MSOF[MODE]
    z = np.load(p); f = {k: z[k] for k in ('ts', 'open', 'high', 'low', 'close', 'volume')}
    cuts = np.flatnonzero(np.diff(f['ts']) != base_ms) + 1
    a, b = max(zip(np.r_[0, cuts], np.r_[cuts, len(f['ts'])]), key=lambda x: x[1] - x[0])
    f = {k: v[a:b] for k, v in f.items()}
    k0 = int(np.flatnonzero(f['ts'] % 86400000 == 0)[0]) if np.any(f['ts'] % 86400000 == 0) else 0
    f = {k: v[k0:] for k, v in f.items()}
    if len(f['ts']) * base_ms < 200 * 86400000: return None
    frames = {MODE: f}
    for tf in ('5m', '30m', '4h', '1d'):
        if MSOF[tf] > base_ms: frames[tf] = resample(f, MSOF[tf] // base_ms, base_ms)
    return frames


class TF:
    def __init__(self, f, ms):
        self.f = f; self.ms = ms; self.ts = f['ts']
        self.dif, self.hist = macd(f['close'])
        r = analyze(f, self.hist, max_level=0, signal_level=0)
        self.L = Lv(f, self.dif, self.hist, r['levels'][0], ms)
        self.sig = by_T(r, ms).get(0, {})
        self.cache = {}
        self.known_units = {}
        for u, k in zip(self.L.units, self.L.known): self.known_units.setdefault(int(k), []).append(u)

    def idx(self, T): return int(np.searchsorted(self.ts + self.ms, T, side='right')) - 1

    def cand(self, T):
        if T not in self.cache:
            if len(self.cache) > 5000: self.cache.clear()
            i = self.idx(T)
            self.cache[T] = self.L.candidate(T, i) if i >= 0 else None
        return self.cache[T]


def one(inst):
    frames = load(inst)
    if frames is None: return None
    tfs = {k: TF(v, MSOF[k]) for k, v in frames.items()}
    base = frames[MODE]; bms = MSOF[MODE]
    ts = base['ts']; o, h, l, c = base['open'], base['high'], base['low'], base['close']
    out = {k: [] for k in list(VARIANTS) + ['同级别分解_30m']}
    main = tfs[MAIN]; seg = dict(P=None)
    st = {k: dict(P=None, p1={1: None, -1: None}, p1T={1: 0, -1: 0}, sold1=None, last=0, n2={1: 0, -1: 0}, got3={1: False, -1: False},
                  turn={1: [], -1: []}) for k in VARIANTS}
    def close_pos(name, P, px, t, why):
        P.sell(px, 1.0); out[name].append(dict(inst=inst, start=P.start, end=t, side=P.d, stages=P.stages, exit=why, pnl=P.pnl, gross=P.gross, used=P.used, small2big=P.small2big))
    for i in range(len(ts)):
        T = int(ts[i]) + bms
        newMain = main.known_units.get(T, []) if T % main.ms == 0 else []
        s_main = main.sig.get(T, []) if T % main.ms == 0 else []
        # 同级别分解（30分钟笔）：新笔确认就跟随方向，反向笔确认离场/反手；笔起点作保护止损
        P = seg['P']
        if P is not None and P.d * ((l[i] if P.d == 1 else h[i]) - P.stop) <= 0:
            close_pos('同级别分解_30m', P, o[i] if P.d * (o[i] - P.stop) <= 0 else P.stop, T, '止损'); seg['P'] = None
        for u in newMain:
            P = seg['P']; dd = u['side']
            if P is not None and P.d != dd:
                close_pos('同级别分解_30m', P, c[i], T, '反向笔确认'); seg['P'] = P = None
            if P is None:
                stop = u['a']['price']; risk = dd * (c[i] - stop) / c[i]
                if risk > 0.0005:
                    P = Pos(dd); P.start = T; P.stop = stop; P.buy(c[i], 1 / risk); P.used = 1.0; P.stages = 'S'; seg['P'] = P
        for name, V in VARIANTS.items():
            X = st[name]; P = X['P']
            conf = tfs[V['conf']]; cms = conf.ms
            for dd in (1, -1):
                if X['p1'][dd] is not None and dd * ((l[i] if dd == 1 else h[i]) - X['p1'][dd]) < 0:
                    X['p1'][dd] = None; X['n2'][dd] = 0; X['got3'][dd] = False
            # 止损（最细周期逐根）
            if P is not None:
                d = P.d; adv = l[i] if d == 1 else h[i]
                if d * (adv - P.stop) <= 0:
                    close_pos(name, P, o[i] if d * (o[i] - P.stop) <= 0 else P.stop, T, '止损'); X['P'] = P = None
            big = tfs[V['big']] if V['big'] else None
            s_big = big.sig.get(T, []) if big and T % big.ms == 0 else []
            # 本级别新确认的笔 → 小转大
            for u in newMain:
                dd = u['side']; a = u['a']['price']
                if any(abs(a - x) <= 1e-12 * max(1.0, abs(a)) for x in X['turn'][dd]):
                    if X['p1'][dd] is None:
                        X['p1'][dd] = a; X['p1T'][dd] = T; X['n2'][dd] = 0; X['got3'][dd] = False; X.setdefault('s2b', {})[dd] = True
                    if P is not None and P.d == -dd and X['sold1'] is None: X['sold1'] = a
            if T % cms != 0 and not s_big and not s_main:
                continue
            sub = conf.sig.get(T, []) if T % cms == 0 else []
            kinds = ('T1', 'T1P') if V['loose'] else ('T1',)
            SB = {dd: [e for e in sub if e['side'] == dd and e.get('kind') in kinds] for dd in (1, -1)}
            for dd in (1, -1):
                for e in [e for e in sub if e['side'] == dd and e.get('kind') in ('T1', 'T1P')]:
                    X['turn'][dd] = (X['turn'][dd] + [e['invalidation']])[-6:]
            if not (SB[1] or SB[-1] or s_big or s_main) and P is None:
                continue
            c1 = main.cand(T)
            c2 = big.cand(T) if big else None
            def mid_ok(d):
                if not V['mid']: return True
                cm = tfs[V['mid']].cand(T)
                return cm is not None and cm[1] == d and div_ok(cm, V['loose'])
            # 卖点
            if P is not None:
                d = P.d; ss = SB[-d] if mid_ok(-d) else []; confd = [e for e in s_main if e['side'] == -d]; act = None
                big_ok = c2 is not None and c2[1] == -d and (c2[0] == 'T3' or div_ok(c2, V['loose']))
                if any(e['side'] == -d for e in s_big) or (ss and big_ok): act = '大级别卖点'
                elif (ss and c1 and c1[1] == -d and c1[0] == 'T3') or any(e.get('kind') == 'T3' for e in confd): act = '3卖'
                elif X['sold1'] is not None and ((ss and d * (ss[0]['price'] - X['sold1']) < 0) or any(e.get('kind') in ('T2', 'T2S') for e in confd)): act = '2卖'
                elif X['sold1'] is None and ((ss and c1 and c1[1] == -d and div_ok(c1, V['loose']) and (not c1[3]['upgraded'] or big_ok))
                                             or any(e.get('kind') in kinds for e in confd)): act = '1卖'
                anysub = {dd: [e for e in sub if e['side'] == dd and e.get('kind') in ('T1', 'T1P')] for dd in (1, -1)}
                if act == '1卖':
                    P.sell(c[i], 0.5); X['sold1'] = ss[0]['price'] if ss else c[i]
                elif act:
                    close_pos(name, P, c[i], T, act); X['P'] = P = None
                elif V.get('tday') and X['sold1'] is None and anysub[-d] and not X.get('park') and d * (c[i] - P.avg) > 0:
                    X['park'] = P.N / 3; P.sell(c[i], 1 / 3)          # 中枢震荡做差价：次级别卖点先减 1/3
                elif V.get('tday') and X.get('park') and (anysub[d] or [e for e in sub if e['side'] == d and e.get('kind') in ('T2', 'T2S')]):
                    P.buy(c[i], X['park']); X['park'] = 0.0           # 次级别买点买回
            # 买点
            for d in (1, -1):
                if V.get('long_only') and d == -1: continue
                if not SB[d] or not mid_ok(d): continue
                if P is not None and (P.d != d or X['sold1'] is not None): continue
                e0 = SB[d][0]; stage = None; stop = None
                big_ok = c2 is not None and c2[1] == d and div_ok(c2, V['loose'])
                if c1 and c1[1] == d and div_ok(c1, V['loose']) and (not c1[3]['upgraded'] or big_ok) and (not V['big'] or big_ok):
                    stage = '1'; stop = e0['invalidation']; X['p1'][d] = stop; X['p1T'][d] = T; X['n2'][d] = 0; X['got3'][d] = False; X.setdefault('s2b', {})[d] = False
                elif c1 and c1[1] == d and c1[0] == 'T3':
                    stage = '3'; z = c1[2]; stop = z['high'] if d == 1 else z['low']; X['got3'][d] = True
                elif X['p1'][d] is not None and not X['got3'][d] and X['n2'][d] < 3 and d * (e0['invalidation'] - X['p1'][d]) > 0:
                    nn = main.L.n(T); last = main.L.units[nn - 1] if nn else None
                    if last is not None and last['side'] == d and main.L.known[nn - 1] > X['p1T'][d] and d * (e0['invalidation'] - (last['high'] if d == 1 else last['low'])) < 0:
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
                P.buy(c[i], (1 / 3) / risk); P.used += 1 / 3; P.stages += stage; P.last_T = T
                break
    T = int(ts[-1]) + bms
    for name, X in st.items():
        if X['P'] is not None: close_pos(name, X['P'], c[-1], T, '数据结束')
    if seg['P'] is not None: close_pos('同级别分解_30m', seg['P'], c[-1], T, '数据结束')
    return out


if __name__ == '__main__':
    uni = json.load(open(os.path.join(B.DATA, 'universe_5m40.json')))
    uni = [i for i in uni if os.path.exists(os.path.join(B.DATA, f'{i}_{MODE}2y.npz'))]
    with Pool(int(os.environ.get('PROCS', 4))) as p:
        parts = [x for x in p.map(one, uni) if x]
    res = {k: [r for part in parts for r in part[k]] for k in parts[0]}
    json.dump(res, open(os.path.join(B.DATA, f'yuanwen4_{MODE}.json'), 'w'), ensure_ascii=False)
    print(len(parts), {k: len(v) for k, v in res.items()})
