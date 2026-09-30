"""方案二：按周期分级别的缠论区间套（研究脚本 backtest/okx_replay/yuanwen4.py 中
"三层_日线_30m_5m_实战只做二买" + 大盘宽度，逐根照搬到实盘）。

级别：大级别 = 日线，本级别 = 30 分钟，次级别 = 5 分钟；每个周期用自己的笔、笔中枢、买卖点。
一买（不开仓，只记一买低点）：日线同向背驰段候选 + 30 分钟趋势背驰（MACD 面积 < 90%、黄白线回抽 0 轴、
  离开段斜率变小）+ 5 分钟确认趋势背驰 1买。中枢延伸到 9 段视为升级，要求日线同时背驰。
小转大：30 分钟新确认的一笔起点正好是 5 分钟 1买/1卖 的转折点 → 该点当作一买低点。
二买（开仓 1/3 风险）：一买后 30 分钟已走出一段同向反弹，回抽中 5 分钟出现趋势背驰 1买，
  不破一买低点，也没超过反弹段高点；之后同样条件再出现为类二买（各加 1/3，最多两次）。
开仓时要求大盘宽度同向 ≥ 50%（日线收在 EMA50 上方的币占比，做空取反）。
卖：30 分钟背驰 + 5 分钟 1卖 → 卖一半（之后不再加仓）；已卖过一卖后 5 分钟 1卖高点更低或 30 分钟确认
  2卖/类2卖 → 清仓；30 分钟 3卖 → 清仓；日线卖点 → 清仓。止损：一买低点（交易所整仓止损单）。
  没有卖点就持有（不设最长持仓）。

实盘说明：每根 5 分钟收盘处理一次；一买低点、二买次数、小转大转折点等状态保存在 v7_scheme2_state.json，
重启不丢。持仓相关状态（已加的批次、是否卖过一卖）保存在引擎持仓记录里。
"""
from __future__ import annotations
import json, math, os, threading, time
from pathlib import Path
import numpy as np

M5, M30, D1 = 300000, 1800000, 86400000
TFS = ('5m', '30m', '1d')
MSOF = {'5m': M5, '30m': M30, '1d': D1}
STATE_FILE = Path(os.getenv('ALPHA_V7_SCHEME2_STATE', str(Path(__file__).with_name('v7_scheme2_state.json'))))
BREADTH_MIN = 0.5
LEG_RISK = 1 / 3
TP_R = 20.0              # 交易所保护止盈挂在 20R 外（原文无固定止盈，只作兜底）
# V7.7.3：首次处理一个币时，用更长的历史回放建立一买状态。回测里一买到二买中位数约 3 周，
# 只回放 1500 根 5 分钟（约 5 天）会漏掉大部分二买。
BOOT_5M = 17280          # 60 天 5 分钟
BOOT_30M = 3000          # 约 62 天 30 分钟
STATE_VER = 2
_LOCK = threading.RLock()


def ema(x, n):
    x = np.asarray(x, float); out = np.empty(len(x))
    if not len(x): return out
    out[0] = x[0]; a = 2 / (n + 1)
    for i in range(1, len(x)): out[i] = out[i - 1] + a * (x[i] - out[i - 1])
    return out


def macd(c):
    dif = ema(c, 12) - ema(c, 26)
    return dif, dif - ema(dif, 9)


class Level:
    """某个周期的笔级结构在时刻 T（K 线收盘时间）已知的部分。"""
    def __init__(self, f, ms):
        from alpha_v7_chan import analyze
        self.f = {k: np.asarray(v) for k, v in f.items()}; self.ms = ms; self.ts = self.f['ts'].astype(np.int64)
        self.dif, self.hist = macd(self.f['close'])
        r = analyze(self.f, self.hist, max_level=0, signal_level=0)
        lv = r['levels'][0] if r.get('levels') else {'units': [], 'centers': []}
        self.units = lv['units']; self.zones = lv['centers']
        self.known = np.array([u['known_at'] + ms for u in self.units], dtype=np.int64) if self.units else np.zeros(0, np.int64)
        self.sig = {}
        for e in r['signals']:
            self.sig.setdefault(int(e['known_at']) + ms, []).append(e)
        self.known_units = {}
        for u, k in zip(self.units, self.known): self.known_units.setdefault(int(k), []).append(u)
        self._cache = {}

    def n(self, T): return int(np.searchsorted(self.known, T, side='right'))

    def idx(self, T): return int(np.searchsorted(self.ts + self.ms, T, side='right')) - 1

    def power(self, t0, t1, side):
        m = (self.ts >= t0) & (self.ts <= t1); h = self.hist[m]
        return float(np.sum(np.maximum(h, 0))) if side > 0 else float(np.sum(np.maximum(-h, 0)))

    def cand(self, T):
        if T not in self._cache:
            if len(self._cache) > 4000: self._cache.clear()
            i = self.idx(T)
            self._cache[T] = self._candidate(T, i) if i >= 0 else None
        return self._cache[T]

    def _candidate(self, T, i):
        """('T1'|'T1P', 方向, 中枢, info) 背驰段候选，或 ('T3', 方向, 中枢, {})，或 None。"""
        from alpha_v7_chan import zone_snapshot
        n = self.n(T)
        if n < 4: return None
        units = self.units[:n]; last = units[-1]; side = -last['side']
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
                    m_in = (self.ts >= ent['ts']) & (self.ts <= ent['to']); m_z = (self.ts > ent['to']) & (self.ts <= last['to'])
                    dmax = float(np.max(np.abs(self.dif[m_in]))) if m_in.any() else 0.0
                    dz = self.dif[m_z]
                    zero_ok = bool(len(dz)) and (float(np.min(np.abs(dz))) <= 0.1 * dmax or bool(np.any(dz * side <= 0)))
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


def div_ok(c):
    """严格背驰：趋势背驰 + 黄白线回抽 0 轴 + 斜率变小。"""
    return bool(c and c[0] == 'T1' and c[3].get('zero_ok') and c[3].get('slope_ok'))


# ---------------------------------------------------------------- 状态

def _new_state():
    return dict(ver=STATE_VER, last_T=0, p1={'1': None, '-1': None}, p1T={'1': 0, '-1': 0}, n2={'1': 0, '-1': 0},
                got3={'1': False, '-1': False}, turn={'1': [], '-1': []}, s2b={'1': False, '-1': False})


_STATES = None


def _load():
    global _STATES
    if _STATES is None:
        try:
            _STATES = json.loads(STATE_FILE.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            _STATES = {}
    return _STATES


def _save():
    tmp = STATE_FILE.with_suffix('.tmp')
    try:
        tmp.write_text(json.dumps(_STATES, ensure_ascii=False), encoding='utf-8'); os.replace(tmp, STATE_FILE)
    except OSError:
        pass


def _stale(X, position):
    """旧版（只回放约 5 天）建立的状态：无持仓时丢弃，用长历史重新回放。有持仓的等平仓后再重建。"""
    return bool(X) and int(X.get('ver') or 1) < STATE_VER and not position


def needs_boot(symbol, position=None):
    """该币是否需要用长历史回放（首次处理，或旧版状态且当前无持仓）。"""
    with _LOCK:
        X = _load().get(symbol)
    return not X or not int(X.get('last_T') or 0) or _stale(X, position)


def reset_state(symbol=None):
    with _LOCK:
        st = _load()
        if symbol: st.pop(symbol, None)
        else: st.clear()
        _save()


# ---------------------------------------------------------------- 大盘宽度

_BREADTH = dict(day=None, value=None, coins=0, error='')


def market_breadth(exchange, symbols_ccxt, now_ms=None):
    """日线收盘在 EMA50 上方的币占比（按 UTC 日缓存；只读公开日线）。返回 (占比或 None, 参与币数)。"""
    now_ms = int(now_ms or time.time() * 1000); day = now_ms // D1
    with _LOCK:
        if _BREADTH['day'] == day and _BREADTH['value'] is not None:
            return _BREADTH['value'], _BREADTH['coins']
    from alpha_v7_feed import frame
    from concurrent.futures import ThreadPoolExecutor
    def one(cs):
        try:
            f = frame(exchange, cs, '1d', count=120, now_ms=now_ms)
            c = np.asarray(f['close'], float)
            if len(c) < 60: return None
            return bool(c[-1] > ema(c, 50)[-1])
        except Exception:
            return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        res = [x for x in pool.map(one, list(symbols_ccxt)) if x is not None]
    value = float(np.mean(res)) if len(res) >= 10 else None
    with _LOCK:
        _BREADTH.update(day=day if value is not None else None, value=value, coins=len(res),
                        error='' if value is not None else f'有效日线不足（{len(res)} 个币）')
    return value, len(res)


def breadth_status():
    with _LOCK:
        return dict(_BREADTH)


# ---------------------------------------------------------------- 逐根处理

def _stage_order(stage):
    return {'2': 2, 's': 2.5}.get(stage, 0)


def evaluate(symbol, frames, position=None, breadth=None, now_ms=None):
    """处理自上次以来新收盘的 5 分钟 K 线，返回最新一根的动作。

    frames: {'5m','30m','1d'} 已收盘 K 线；position: 引擎持仓（None=无持仓），
    使用 side / s2_stages / s2_sold1；breadth: 大盘宽度（None=未知，不开新仓）。
    返回 dict(T, entry=None|{side,stage,stop,ref}, action=None|{type,...}, notes=[...])。
    """
    ts_last = np.asarray(frames['5m']['ts'])
    if not len(ts_last): return dict(T=0, entry=None, action=None, notes=['5分钟K线为空'])
    latest_T = int(ts_last[-1]) + M5
    with _LOCK:
        cached = _LAST.get(symbol)
        X0 = _load().get(symbol) or {}
        if cached and cached['T'] == latest_T and int(X0.get('last_T') or 0) >= latest_T and not _stale(X0, position):
            return dict(cached)                     # 同一根K线内重复扫描：直接用本根的结果，不重算
    lv = {tf: _level(symbol, tf, frames[tf]) for tf in TFS}
    ts5 = lv['5m'].ts
    with _LOCK:
        states = _load(); X = states.get(symbol) or _new_state()
        if _stale(X, position): X = _new_state()
        start_T = int(X.get('last_T') or 0)
        first = start_T == 0
        idxs = [i for i in range(len(ts5)) if int(ts5[i]) + M5 > start_T]
        result = dict(T=latest_T, entry=None, action=None, notes=[])
        pos = position
        for i in idxs:
            T = int(ts5[i]) + M5
            live = (T == latest_T) and not first          # 只有最新一根能产生下单动作；首次启动只回放建状态
            out = _step(X, lv, i, T, pos, breadth, live)
            if live:
                result.update(out)
        X['last_T'] = latest_T
        states[symbol] = X; _save()
        if first:
            result['notes'].append('方案二首次处理该币：已用现有历史回放建立状态，从下一根K线开始交易')
        _LAST[symbol] = dict(result)
    return result


_LAST = {}
_LVL = {}


def _level(symbol, tf, f):
    """30分钟/日线只有收新K线时才重算（按最后一根时间+根数缓存）。"""
    ts = np.asarray(f['ts'])
    key = (int(ts[-1]) if len(ts) else 0, len(ts))
    hit = _LVL.get((symbol, tf))
    if hit and hit[0] == key:
        return hit[1]
    lv = Level(f, MSOF[tf])
    _LVL[(symbol, tf)] = (key, lv)
    return lv


def _step(X, lv, i, T, pos, breadth, live):
    main, big, conf = lv['30m'], lv['1d'], lv['5m']
    f = conf.f; lo, hi, c = float(f['low'][i]), float(f['high'][i]), float(f['close'][i])
    out = dict(entry=None, action=None, notes=[])
    for dd in (1, -1):
        k = str(dd); p1 = X['p1'][k]
        if p1 is not None and dd * ((lo if dd == 1 else hi) - p1) < 0:
            X['p1'][k] = None; X['n2'][k] = 0; X['got3'][k] = False
    d_pos = (1 if pos.get('side') == 'long' else -1) if pos else 0
    sold1 = pos.get('s2_sold1') if pos else None
    stages = str(pos.get('s2_stages') or '') if pos else ''
    new_main = main.known_units.get(T, []) if T % M30 == 0 else []
    s_main = main.sig.get(T, []) if T % M30 == 0 else []
    s_big = big.sig.get(T, []) if T % D1 == 0 else []
    # 小转大
    for u in new_main:
        dd = u['side']; k = str(dd); a = float(u['a']['price'])
        if any(abs(a - x) <= 1e-12 * max(1.0, abs(a)) for x in X['turn'][k]):
            if X['p1'][k] is None:
                X['p1'][k] = a; X['p1T'][k] = T; X['n2'][k] = 0; X['got3'][k] = False; X['s2b'][k] = True
            if pos and d_pos == -dd and sold1 is None:
                sold1 = a
                if live: out['action'] = dict(type='mark_sold1', price=a, reason='卖出方向小转大：之后按2卖/3卖离场')
    sub = conf.sig.get(T, [])
    SB = {dd: [e for e in sub if e['side'] == dd and e.get('kind') == 'T1'] for dd in (1, -1)}
    for dd in (1, -1):
        for e in [e for e in sub if e['side'] == dd and e.get('kind') in ('T1', 'T1P')]:
            X['turn'][str(dd)] = (X['turn'][str(dd)] + [float(e['invalidation'])])[-6:]
    if not (SB[1] or SB[-1] or s_big or s_main) and not pos:
        return out
    c1 = main.cand(T); c2 = big.cand(T)
    # 卖点
    if pos:
        d = d_pos; ss = SB[-d]; confd = [e for e in s_main if e['side'] == -d]; act = None
        big_ok = c2 is not None and c2[1] == -d and (c2[0] == 'T3' or div_ok(c2))
        if any(e['side'] == -d for e in s_big) or (ss and big_ok): act = '大级别卖点'
        elif (ss and c1 and c1[1] == -d and c1[0] == 'T3') or any(e.get('kind') == 'T3' for e in confd): act = '3卖'
        elif sold1 is not None and ((ss and d * (float(ss[0]['price']) - sold1) < 0) or any(e.get('kind') in ('T2', 'T2S') for e in confd)): act = '2卖'
        elif sold1 is None and ((ss and c1 and c1[1] == -d and div_ok(c1) and (not c1[3]['upgraded'] or big_ok))
                                or any(e.get('kind') == 'T1' for e in confd)): act = '1卖'
        if act == '1卖':
            sold1 = float(ss[0]['price']) if ss else c
            if live: out['action'] = dict(type='reduce_half', reason='方案二：1卖（30分钟背驰+5分钟确认），卖出一半', sold1=sold1)
        elif act:
            if live: out['action'] = dict(type='close', reason='方案二：' + act + '，清仓')
            return out
    # 买点（只做二买/类二买；一买、三买只更新状态）
    for d in (1, -1):
        k = str(d)
        if not SB[d]: continue
        if pos and (d_pos != d or sold1 is not None): continue
        e0 = SB[d][0]; stage = None; stop = None
        big_ok = c2 is not None and c2[1] == d and div_ok(c2)
        inv = float(e0['invalidation'])
        if c1 and c1[1] == d and div_ok(c1) and (not c1[3]['upgraded'] or big_ok) and big_ok:
            stage = '1'; X['p1'][k] = inv; X['p1T'][k] = T; X['n2'][k] = 0; X['got3'][k] = False; X['s2b'][k] = False
        elif c1 and c1[1] == d and c1[0] == 'T3':
            stage = '3'; X['got3'][k] = True
        elif X['p1'][k] is not None and not X['got3'][k] and X['n2'][k] < 3 and d * (inv - X['p1'][k]) > 0:
            nn = main.n(T); last = main.units[nn - 1] if nn else None
            if last is not None and last['side'] == d and main.known[nn - 1] > X['p1T'][k] and d * (inv - (last['high'] if d == 1 else last['low'])) < 0:
                stage = '2' if X['n2'][k] == 0 else 's'; stop = X['p1'][k]; X['n2'][k] += 1
        if stage is None or stage in '13': continue
        if pos and (_stage_order(stage) < max([_stage_order(x) for x in stages] or [0]) or (stage != 's' and stage in stages)): continue
        risk = d * (c - stop) / c
        if risk <= 0.0005: continue
        if not live: break
        if pos:
            out['action'] = dict(type='add', stage=stage, stop=float(stop), ref=c, side=d,
                                 reason=f'方案二：{"二买" if stage == "2" else "类二买"}加仓 1/3（止损=一买低点 {stop:.8g}）')
        else:
            b = None if breadth is None else (breadth if d == 1 else 1 - breadth)
            if b is None:
                out['notes'].append('大盘宽度未知，本次二买不开仓')
            elif b < BREADTH_MIN:
                out['notes'].append(f'大盘宽度 {b * 100:.0f}% < 50%，本次{"二买" if d == 1 else "二卖"}不开仓')
            else:
                out['entry'] = dict(side=d, stage=stage, stop=float(stop), ref=c, small2big=bool(X['s2b'][k]), breadth=b)
        break
    return out
