"""原文全套（第四版）：按周期分级别的区间套。
级别：大级别 = 日线（对照：4小时），本级别 = 30分钟（持仓管理、止损、卖点），次级别 = 5分钟，最小级别 = 1分钟。
每个周期用它自己的笔、笔中枢、买卖点（原文：30分钟图看30分钟级别，往下 5分钟、1分钟找精确点）。
区间套进场：
  三层 = 大级别背驰段候选 + 30分钟背驰段/三类候选 + 5分钟确认 1买（在 5分钟收盘成交）；
  四层 = 大级别 + 30分钟 + 5分钟也处在同向背驰段 + 1分钟确认 1买（在 1分钟收盘成交）。
  "无大级别"变体只用 30分钟 + 下级。
其余规则与第三版相同：一买只认趋势背驰（MACD面积 + 黄白线回抽0轴 + 斜率变小）、区间套早进场的二买/类二买、三买、
分批各 1/3、一卖卖一半、二卖/三卖/大级别卖点清仓、一买低点止损、只有三买时回中枢止损、中枢9段升级、小转大。
实战补充（搜集的实战做法，变体）：陆续卖（进入本级别背驰段先卖 1/3、宁早勿迟）、只做二买/类二买（最安全）、
中枢位置（做多在中枢中轴下方、做空在上方）。
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
MSOF = {'1m': 60000, '5m': 300000, '15m': 900000, '30m': 1800000, '4h': 14400000, '1d': 86400000}
BASE2 = dict(big='1d', conf='5m', mid=None, loose=False, only2=True)
V5 = {'方案二': dict(BASE2),
      '1_中枢上方二买': dict(BASE2, zabove=True),
      '2_MACD确认二买': dict(BASE2, macd2=True),
      '3_二买放宽': dict(BASE2, loose2=True),
      '4_一次买够': dict(BASE2, full=True),
      '5_强势不理一卖': dict(BASE2, hold_strong=True)}
V1 = {'四层_日线_30m_5m_1m': dict(big='1d', conf='1m', mid='5m', loose=False),
      '四层_日线_30m_5m_1m_做差价': dict(big='1d', conf='1m', mid='5m', loose=False, tday=True),
      '四层_日线_30m_5m_1m_只做多': dict(big='1d', conf='1m', mid='5m', loose=False, long_only=True),
      '四层_日线_30m_5m_1m_实战全部': dict(big='1d', conf='1m', mid='5m', loose=False, gradual=True, only2=True, zpos=True),
      '四层_日线_30m_5m_1m_只做二买': dict(big='1d', conf='1m', mid='5m', loose=False, only2=True),
      '四层_4h_30m_5m_1m_只做二买': dict(big='4h', conf='1m', mid='5m', loose=False, only2=True),
      '四层_4h_30m_5m_1m': dict(big='4h', conf='1m', mid='5m', loose=False),
      '三层_30m_5m_1m': dict(big=None, conf='1m', mid='5m', loose=False),
      '含盘背_四层_日线_30m_5m_1m': dict(big='1d', conf='1m', mid='5m', loose=True)}
# 检验用：100 个币的 15 分钟数据（没有 5 分钟），次级别换成 15 分钟
V15 = {'三层_日线_30m_15m': dict(big='1d', conf='15m', mid=None, loose=False),
       '三层_日线_30m_15m_只做二买': dict(big='1d', conf='15m', mid=None, loose=False, only2=True),
       '三层_日线_30m_15m_实战全部': dict(big='1d', conf='15m', mid=None, loose=False, gradual=True, only2=True, zpos=True),
       '二层_30m_15m': dict(big=None, conf='15m', mid=None, loose=False)}
FILE = {'5m': '5m2y', '1m': '1m2y', '15m': '15m', '15m_old': '15m_old', '5mnew': '5mnew'}[MODE]
BASE_TF = '15m' if MODE.startswith('15m') else ('5m' if MODE == '5mnew' else MODE)
VARIANTS = dict(V5)

MAIN = '30m'


def load(inst):
    p = os.path.join(B.DATA, f'{inst}_{FILE}.npz')
    if not os.path.exists(p): return None
    base_ms = MSOF[BASE_TF]
    z = np.load(p); f = {k: z[k] for k in ('ts', 'open', 'high', 'low', 'close', 'volume')}
    cuts = np.flatnonzero(np.diff(f['ts']) != base_ms) + 1
    a, b = max(zip(np.r_[0, cuts], np.r_[cuts, len(f['ts'])]), key=lambda x: x[1] - x[0])
    f = {k: v[a:b] for k, v in f.items()}
    k0 = int(np.flatnonzero(f['ts'] % 86400000 == 0)[0]) if np.any(f['ts'] % 86400000 == 0) else 0
    f = {k: v[k0:] for k, v in f.items()}
    if len(f['ts']) * base_ms < 120 * 86400000: return None
    frames = {BASE_TF: f}
    for tf in ('5m', '15m', '30m', '4h', '1d'):
        if MSOF[tf] > base_ms: frames[tf] = resample(f, MSOF[tf] // base_ms, base_ms)
    return frames


class TF:
    def __init__(self, f, ms):
        self.f = f; self.ms = ms; self.ts = f['ts']
        self.dif, self.hist = macd(f['close'])
        r = analyze(f, self.hist, max_level=0, signal_level=0, pen_mode=int(os.environ.get('PEN', 0)))
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


def _third_buy(T3S, tfs, main, T, i, o, h, l, c, close_pos):
    """原文第49课：6 激进 = 只在三买买入，新中枢形成就退出；7 稳健 = 三买买入持有，新中枢形成后中枢上方减半、下方买回，三卖离场。
    三买 = 30 分钟离开中枢后回抽不回中枢（候选）+ 5 分钟 1买/盘背1买 确认；止损 = 回到原中枢。"""
    conf = tfs['5m']
    sub = conf.sig.get(T, []) if T % conf.ms == 0 else []
    anyb = {dd: [e for e in sub if e['side'] == dd and e.get('kind') in ('T1', 'T1P')] for dd in (1, -1)}
    s_main = main.sig.get(T, []) if T % main.ms == 0 else []
    nz = sum(1 for z in main.L.zones if z['known_at'] + main.ms <= T)
    for name, X in T3S.items():
        P = X['P']
        if P is not None:
            d = P.d; adv = l[i] if d == 1 else h[i]
            if d * (adv - P.stop) <= 0:
                close_pos(name, P, o[i] if d * (o[i] - P.stop) <= 0 else P.stop, T, '止损'); X['P'] = P = None
        if P is not None:
            d = P.d; new_center = nz > X['nz0']
            c1 = main.cand(T) if (sub or s_main) else None
            third_sell = (anyb[-d] and c1 and c1[1] == -d and c1[0] == 'T3') or any(e['side'] == -d and e.get('kind') == 'T3' for e in s_main)
            if name == '6_三买激进' and new_center:
                close_pos(name, P, c[i], T, '新中枢形成'); X['P'] = None; continue
            if name == '7_三买稳健':
                if third_sell:
                    close_pos(name, P, c[i], T, '3卖'); X['P'] = None; continue
                if new_center:
                    z = [z for z in main.L.zones if z['known_at'] + main.ms <= T][-1]
                    if not X.get('park') and anyb[-d] and d * (c[i] - (z['high'] if d == 1 else z['low'])) > 0:
                        X['park'] = P.N / 2; P.sell(c[i], 0.5)                     # 中枢上方减仓
                    elif X.get('park') and anyb[d] and d * (c[i] - (z['low'] + z['high']) / 2) < 0:
                        P.buy(c[i], X['park']); X['park'] = 0.0                     # 中枢下方回补
                    elif X.get('park') and c1 and c1[1] == d and c1[0] == 'T3' and anyb[d]:
                        P.buy(c[i], X['park']); X['park'] = 0.0                     # 新的三买：满仓持有
            continue
        for d in (1, -1):
            if not anyb[d]: continue
            c1 = main.cand(T)
            if not (c1 and c1[1] == d and c1[0] == 'T3'): continue
            z = c1[2]; stop = z['high'] if d == 1 else z['low']; risk = d * (c[i] - stop) / c[i]
            if risk <= 0.0005: continue
            P = X['P'] = Pos(d); P.start = T; P.stop = stop; P.buy(c[i], 1 / risk); P.used = 1.0; P.stages = '3'
            X['nz0'] = nz; X['park'] = 0.0
            break


def one(inst):
    frames = load(inst)
    if frames is None: return None
    tfs = {k: TF(v, MSOF[k]) for k, v in frames.items()}
    base = frames[BASE_TF]; bms = MSOF[BASE_TF]
    ts = base['ts']; o, h, l, c = base['open'], base['high'], base['low'], base['close']
    out = {k: [] for k in list(VARIANTS) + ['同级别分解_30m', '6_三买激进', '7_三买稳健']}
    main = tfs[MAIN]; seg = dict(P=None)
    st = {k: dict(P=None, p1={1: None, -1: None}, p1T={1: 0, -1: 0}, sold1=None, last=0, n2={1: 0, -1: 0}, got3={1: False, -1: False},
                  turn={1: [], -1: []}, pend={1: None, -1: None}) for k in VARIANTS}
    T3S = {k: dict(P=None) for k in ('6_三买激进', '7_三买稳健')}
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
        _third_buy(T3S, tfs, main, T, i, o, h, l, c, close_pos)
        for name, V in VARIANTS.items():
            X = st[name]; P = X['P']
            conf = tfs[V['conf']]; cms = conf.ms
            for dd in (1, -1):
                if X['p1'][dd] is not None and dd * ((l[i] if dd == 1 else h[i]) - X['p1'][dd]) < 0:
                    if V.get('loose2') and not X['got3'][dd] and X['n2'][dd] < 3:
                        X['pend'][dd] = dict(p1T=X['p1T'][dd], n=0)      # 原文：跌破一买低点但盘整背驰，仍可构成二买
                    X['p1'][dd] = None; X['n2'][dd] = 0; X['got3'][dd] = False
                if X['pend'][dd] is not None:
                    X['pend'][dd]['n'] += sum(1 for u in newMain if u['side'] == dd)
                    if X['pend'][dd]['n'] >= 1: X['pend'][dd] = None
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
                if act is None and V.get('gradual') and not X.get('g1') and c1 and c1[1] == -d and div_ok(c1, V['loose']):
                    X['g1'] = True; P.sell(c[i], 1 / 3)       # 实战：一进入本级别背驰段就先卖 1/3（宁早勿迟），之后不再加仓
                if act == '1卖' and V.get('hold_strong') and big is not None:
                    k = big.idx(T); hh = big.hist
                    if k >= 1 and d * hh[k] > 0 and d * (hh[k] - hh[k - 1]) > 0: act = None   # 原文：大级别强势时小级别背驰可以不管
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
                if P is not None and (P.d != d or X['sold1'] is not None or X.get('g1')): continue
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
                if stage is None and V.get('loose2') and X['pend'][d] is not None and not X['got3'][d]:
                    anyb = [e for e in sub if e['side'] == d and e.get('kind') in ('T1', 'T1P')]
                    nn = main.L.n(T); last = main.L.units[nn - 1] if nn else None
                    if anyb and last is not None and last['side'] == -d:
                        e0 = anyb[0]; stop = e0['invalidation']; stage = '2' if X['n2'][d] == 0 else 's'
                        X['p1'][d] = stop; X['p1T'][d] = X['pend'][d]['p1T']; X['pend'][d] = None; X['n2'][d] += 1
                if stage is None: continue
                if V.get('only2') and stage in '13': continue
                if V.get('zabove') and stage in '2s':
                    zs = [z for z in main.L.zones if z['known_at'] + main.ms <= T]
                    if not zs or d * (e0['invalidation'] - (zs[-1]['high'] if d == 1 else zs[-1]['low'])) <= 0: continue   # 原文：中枢上方的二买最强
                if V.get('macd2') and stage in '2s':
                    k = main.idx(T); k0 = int(np.searchsorted(main.ts, X['p1T'][d] - main.ms))
                    seg_d = main.dif[k0:k + 1] * d
                    if not len(seg_d) or not np.any(seg_d > 0): continue          # 黄白线已第一次上 0 轴
                    mx = float(np.max(np.abs(seg_d)))
                    if not (-0.1 * mx <= seg_d[-1] <= 0.3 * mx): continue          # 并在 0 轴附近横住
                if V.get('full') and P is not None: continue                        # 原文：一开始就买够，不加仓          # 实战：二买/类二买最安全，只做这两类
                if V.get('zpos') and stage in '2s':
                    zs = [z for z in main.L.zones if z['known_at'] + main.ms <= T]
                    if zs:
                        mz = (zs[-1]['low'] + zs[-1]['high']) / 2
                        if d * (c[i] - mz) > 0: continue              # 实战：做多在中枢中轴下方，做空在上方
                ORD = {'1': 1, '2': 2, 's': 2.5, '3': 3}
                if P is not None and (ORD[stage] < max(ORD[x] for x in P.stages) or (stage != 's' and stage in P.stages)): continue
                risk = d * (c[i] - stop) / c[i]
                if risk <= 0.0005: continue
                if P is None:
                    P = X['P'] = Pos(d); P.start = T; P.stop = stop; X['sold1'] = None; X['park'] = 0.0; X['g1'] = False
                    P.small2big = bool(X.get('s2b', {}).get(d)) and stage in '2s3'
                elif stage in '12s':
                    P.stop = X['p1'][d] if X['p1'][d] is not None else P.stop
                w = 1.0 if V.get('full') else 1 / 3
                P.buy(c[i], w / risk); P.used += w; P.stages += stage; P.last_T = T
                break
    T = int(ts[-1]) + bms
    for name, X in st.items():
        if X['P'] is not None: close_pos(name, X['P'], c[-1], T, '数据结束')
    if seg['P'] is not None: close_pos('同级别分解_30m', seg['P'], c[-1], T, '数据结束')
    for name, X in T3S.items():
        if X['P'] is not None: close_pos(name, X['P'], c[-1], T, '数据结束')
    return out


def _one_cached(inst):
    """每个币单独存盘，可断点续跑；已有结果直接读取。"""
    tag = os.environ.get('OUTTAG', '')
    d = os.path.join(B.DATA, f'yw5_cache_{MODE}{tag}'); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, inst + '.json')
    if os.path.exists(p): return json.load(open(p))
    r = one(inst)
    json.dump(r, open(p + '.tmp', 'w'), ensure_ascii=False); os.replace(p + '.tmp', p)
    return r


if __name__ == '__main__':
    if MODE.startswith('15m'):
        cats = json.load(open(os.path.join(B.DATA, 'categories.json'))); uni = [i for i, cc in cats.items() if cc == '1']
    elif MODE == '5mnew':
        uni = json.load(open(os.path.join(B.DATA, 'universe_new30.json')))
    else:
        uni = json.load(open(os.path.join(B.DATA, 'universe_5m40.json')))
    uni = [i for i in uni if os.path.exists(os.path.join(B.DATA, f'{i}_{FILE}.npz'))]
    from concurrent.futures import ProcessPoolExecutor
    from concurrent.futures.process import BrokenProcessPool
    procs = int(os.environ.get('PROCS', 4)); parts = {}
    while True:
        todo = [i for i in uni if i not in parts]
        if not todo: break
        try:
            # 每个进程只算一个币就退出（释放内存）；进程被系统杀掉时报错而不是卡住，然后减少并行数重试
            with ProcessPoolExecutor(max_workers=procs, max_tasks_per_child=1) as ex:
                for inst, r in zip(todo, ex.map(_one_cached, todo)):
                    parts[inst] = r
        except BrokenProcessPool:
            print('有计算进程被系统结束（多半是内存不够），并行数降为', max(1, procs - 1), flush=True)
            procs = max(1, procs - 1)
            for inst in todo:
                p = os.path.join(B.DATA, f'yw5_cache_{MODE}{os.environ.get("OUTTAG", "")}', inst + '.json')
                if os.path.exists(p): parts[inst] = json.load(open(p))
    parts = [parts[i] for i in uni if parts.get(i)]
    res = {k: [r for part in parts for r in part[k]] for k in parts[0]}
    json.dump(res, open(os.path.join(B.DATA, f'yuanwen5_{MODE}{os.environ.get("OUTTAG", "")}.json'), 'w'), ensure_ascii=False)
    print(len(parts), {k: len(v) for k, v in res.items()})
