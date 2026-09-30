"""V7 缠论实盘规则回放（离线、只读）。

贴近实盘的地方：
- 用系统自己的 alpha_fast_v7.decide() 决定是否开仓、止损止盈；
- 历史锚点与 alpha_v7_feed.anchored_frame 一致：先 1500 根，攒到 3600 根时裁到最近 3000 根；
- 出场按 _paper_manage_v7 的顺序：交易所止损、原生移动止损(+趋势仓)、止盈、
  收盘确认结构失效、缠论反向点、持仓到期；
- 同一个币持仓期间不重复开仓；可选组合持仓上限。
为了速度：同一锚点窗口内缠论结构只算一次（CX-75 已测试“前缀不重画”，与逐根重算一致；
脚本启动时会抽样比对确认）。

用法: python3 bt_v7.py 配置名 [进程数]
"""
from __future__ import annotations
import glob, json, math, os, sys, time
from multiprocessing import Pool
import numpy as np

REPO = os.environ.get('ALPHA_REPO', '/home/user/j-j-j-j')
sys.path.insert(0, REPO)
os.environ.setdefault('ALPHA_V7_PARAMS_FILE', '/tmp/claude-0/bt_params_unused.json')
import alpha_v7_analysis as A
import alpha_fast_v7 as V
from alpha_v7_chan import select_signal

DATA = os.path.dirname(os.path.abspath(__file__))
CONFIGS = {
    # 推荐设置：笔级、持仓48根、多周期关、就近结构目标开、趋势仓开、全局移动止损开、分批止盈关
    '15m_rec':      dict(tf='15m', params=dict(chan_level=0, max_hold_bars=48), trail=True),
    '15m_norunner': dict(tf='15m', params=dict(chan_level=0, max_hold_bars=48, runner=0), trail=True),
    '15m_notrail':  dict(tf='15m', params=dict(chan_level=0, max_hold_bars=48), trail=False),
    '15m_seg':      dict(tf='15m', params=dict(chan_level=1, max_hold_bars=96), trail=True),
    '5m_rec':       dict(tf='5m',  params=dict(chan_level=0, max_hold_bars=48), trail=True),
    '1h_rec':       dict(tf='1h',  params=dict(chan_level=0, max_hold_bars=48), trail=True),
    '15m_gainers':  dict(tf='15m', params=dict(chan_level=0, max_hold_bars=48), trail=True, gainers=True),
    # 系统内的方案一（V7.6.12）：A/B 段用 180 天数据，C 段用更早的全新数据
    'scheme1':      dict(tf='15m', params=dict(chan_scheme1=1), trail=True, btc=True),
    'scheme1_old':  dict(tf='15m', params=dict(chan_scheme1=1), trail=True, btc=True, suffix='15m_old'),
    # 拆分测试：移动止损按固定价差（入场ATR×2）回撤，而不是按比例；只跑加密币（与研究同一批）
    'scheme1_ib':      dict(tf='15m', params=dict(chan_scheme1=1), trail=True, btc=True, intrabar=True, crypto=True),
    'scheme1_old_ib':  dict(tf='15m', params=dict(chan_scheme1=1), trail=True, btc=True, suffix='15m_old', intrabar=True, crypto=True),
    'scheme1_abs':      dict(tf='15m', params=dict(chan_scheme1=1), trail=True, btc=True, trail_abs=True, crypto=True),
    'scheme1_old_abs':  dict(tf='15m', params=dict(chan_scheme1=1), trail=True, btc=True, suffix='15m_old', trail_abs=True, crypto=True),
}
BASE = dict(strategy='chan_quant', chan_mtf=0)
TF_MS = {'5m': 300000, '15m': 900000, '1h': 3600000}

_CURRENT = {}


def _patched_chan(f, ind=None, **kw):
    return _CURRENT['result']


def load(inst, tf, suffix=None):
    src = '15m' if tf == '1h' else tf
    path = os.path.join(DATA, f'{inst}_{suffix or src}.npz')
    if not os.path.exists(path):
        return []
    z = np.load(path)
    f = {k: z[k] for k in ('ts', 'open', 'high', 'low', 'close', 'volume')}
    if tf == '1h':
        ms = 3600000; b = f['ts'] // ms * ms
        keys, start, cnt = np.unique(b, return_index=True, return_counts=True)
        keep = cnt == 4
        idx = [(s, c) for s, c, k in zip(start, cnt, keep) if k]
        f = dict(ts=keys[keep].astype(np.int64),
                 open=np.array([f['open'][s] for s, c in idx]),
                 high=np.array([f['high'][s:s + c].max() for s, c in idx]),
                 low=np.array([f['low'][s:s + c].min() for s, c in idx]),
                 close=np.array([f['close'][s + c - 1] for s, c in idx]),
                 volume=np.array([f['volume'][s:s + c].sum() for s, c in idx]))
    ms = TF_MS[tf]
    # 实盘遇到缺口会暂停并重新定锚：按缺口切成连续段分别回放
    cuts = np.flatnonzero(np.diff(f['ts']) != ms) + 1
    chunks = []
    for a, b in zip(np.r_[0, cuts], np.r_[cuts, len(f['ts'])]):
        if b - a >= 1600:
            chunks.append({k: v[a:b] for k, v in f.items()})
    return chunks


def anchors(n):
    """(窗口起点, 生效起点, 生效终点) —— 与 anchored_frame 的 1500 预热 / 3600 触发 / 3000 保留一致。"""
    s, a = 0, 1499
    while a < n:
        b = min(s + 3599, n - 1)
        yield s, a, b
        t = b + 1
        s, a = t - 2999, t


def simulate(inst, cfg, gainers=None):
    tf = cfg['tf']; ms = TF_MS[tf]
    p = V.validate_params({**BASE, 'base_tf': tf, **cfg['params']})
    trades = []; stats = dict(signals=0, entered=0, reasons={})
    A.chan = _patched_chan
    btc = None
    if cfg.get('btc'):
        z = np.load(os.path.join(DATA, f"BTC-USDT-SWAP_{cfg.get('suffix') or tf}.npz")); btc = {k: z[k] for k in ('ts', 'close')}
    for f in load(inst, tf, cfg.get('suffix')):
        n = len(f['ts']); pos = None
        for s, a, b in anchors(n):
            F = {k: v[s:b + 1] for k, v in f.items()}
            result = A.__dict__['_orig_chan'](F, signal_level=p['chan_level'], pen_mode=p['chan_pen'],
                                              macd_mode='same' if p['chan_macd'] else 'abs')
            _CURRENT['result'] = result
            ts = F['ts']; idx = {int(t): i for i, t in enumerate(ts)}
            known = sorted({idx[e['known_at']] for e in result['signals'] if e['known_at'] in idx})
            known_set = set(i for i in known if a - s <= i <= b - s)
            for j in range(a - s, b - s + 1):
                # 1) 先管理持仓（本根K线内）
                if pos is not None:
                    done = manage(pos, F, j, result, p, cfg, ms)
                    if done:
                        trades.append(done); pos = None
                # 2) 本根收盘确认了新信号且无持仓 → 按实盘 decide 决定是否开仓
                if pos is None and j in known_set:
                    stats['signals'] += 1
                    if gainers is not None and not gainers(inst, int(ts[j])):
                        stats['reasons']['不在涨幅榜'] = stats['reasons'].get('不在涨幅榜', 0) + 1
                        continue
                    sub = {k: v[:j + 1] for k, v in F.items()}
                    data = dict(frames={tf: sub}, ticker_last=float(sub['close'][-1]), spread_bps=2, as_of_ms=int(ts[j]) + ms + 1000)
                    if btc is not None:   # 与实盘一致：只给最近 1500 根已收盘 BTC 15m
                        kb = int(np.searchsorted(btc['ts'], int(ts[j]), side='right'))
                        data['btc_frame'] = {k: v[max(0, kb - 1500):kb] for k, v in btc.items()}
                    out = V.decide(inst, data, p)
                    if out['signal'] == 'FLAT':
                        r = out['reason'][3:15]; stats['reasons'][r] = stats['reasons'].get(r, 0) + 1
                        continue
                    stats['entered'] += 1
                    pos = open_position(inst, out, F, j, ms, cfg)
        if pos is not None:  # 数据结束仍持仓：按最后收盘价平
            trades.append(close(pos, float(f['close'][-1]), int(f['ts'][-1]), '数据结束'))
    return trades, stats


def open_position(inst, out, F, j, ms, cfg):
    fs = out['fast_strategy']; entry = float(F['close'][j]); d = 1 if out['signal'] == 'LONG' else -1
    p = dict(symbol=inst, side='long' if d == 1 else 'short', d=d, entry=entry, opened_ms=int(F['ts'][j]) + ms,
             sl=float(fs['sl_price']), tp=float(fs['tp_price']), original_tp_price=float(fs['tp_price']),
             base_sl_pct=float(out['sl']), base_tp_pct=float(out['tp']), label=fs['v7_chan_signal']['label'],
             kind=fs['v7_chan_signal'].get('kind'), cost=float(fs['estimated_round_cost']), risk=abs(entry - float(fs['sl_price'])),
             **V.position_meta(out))
    p['opened_at'] = p['opened_ms'] / 1000
    if cfg['trail'] and (p.get('v7_exit_config') or {}).get('runner'):
        # 与 _paper_manage_v7 / _arm_native_trail 一致：移动止损开启 + 趋势仓 → 止盈挪到趋势仓目标
        p['tp'] = V.runner_target(p); p['runner'] = True
    if cfg['trail']:
        p['trail_active_px'], p['trail_cb'] = V.native_trail_config(p)
        if cfg.get('trail_abs'):
            p['trail_abs'] = p['trail_cb'] * p['trail_active_px']
    return p


def close(p, px, t, reason):
    d = p['d']; gross = d * (px - p['entry'])
    return dict(symbol=p['symbol'], label=p['label'], kind=p['kind'], side=p['side'], entry=p['entry'], exit=px,
                opened_ms=p['opened_ms'], closed_ms=t, reason=reason,
                r=(gross - p['cost'] * p['entry']) / p['risk'], ret=gross / p['entry'] - p['cost'],
                risk_pct=p['risk'] / p['entry'])


def manage(p, F, j, result, params, cfg, ms):
    d = p['d']; o, h, l, c = (float(F[k][j]) for k in ('open', 'high', 'low', 'close')); t = int(F['ts'][j])
    adverse = l if d == 1 else h; favorable = h if d == 1 else l
    # 交易所止损（保守：同一根先判止损）
    if d * (adverse - p['sl']) <= 0:
        return close(p, o if d * (o - p['sl']) <= 0 else p['sl'], t, '止损')
    if p.get('trail_on'):
        stop = p['anchor'] - d * p['trail_abs'] if p.get('trail_abs') else p['anchor'] * (1 - d * p['trail_cb'])
        if d * (adverse - stop) <= 0:
            return close(p, o if d * (o - stop) <= 0 else stop, t, '移动止损')
    if d * (favorable - p['tp']) >= 0:
        return close(p, o if d * (o - p['tp']) >= 0 else p['tp'], t, '趋势仓目标' if p.get('runner') else '止盈')
    if cfg['trail']:
        if not p.get('trail_on') and d * (favorable - p['trail_active_px']) >= 0:
            p['trail_on'] = True; p['anchor'] = favorable
        elif p.get('trail_on'):
            p['anchor'] = max(p['anchor'], favorable) if d == 1 else min(p['anchor'], favorable)
        if cfg.get('intrabar') and p.get('trail_on'):
            # 交易所原生移动止损是盘中跟踪：本根创出新极值后，收盘已回撤到止损线外，
            # 说明本根内已经触发（极值在前、收盘在后），按止损线价格成交，而不是等下一根开盘。
            stop = p['anchor'] * (1 - d * p['trail_cb'])
            if d * (c - stop) <= 0:
                return close(p, stop, t, '移动止损')
    # 收盘后的规则：到期、结构失效、缠论反向点（与 exit_plan 相同条件）
    now_ms = t + ms
    if now_ms - p['opened_ms'] >= p['v7_max_seconds'] * 1000:
        return close(p, c, now_ms, '持仓到期')
    if (p.get('v7_exit_config') or {}).get('close_confirm') and d * (c - p['v7_strategy_stop']) < 0:
        return close(p, c, now_ms, '收盘确认结构失效')
    cc = p.get('v7_chan_config') or {}
    if cc.get('chan_exit_opposite'):
        ev = select_signal(result, t, {**V.PARAMS, **cc}, ms)
        if ev and ev['side'] == -d and ev['known_at'] + ms > p['opened_ms']:
            return close(p, c, now_ms, '缠论反向' + ev['label'])
    return None


def run_one(args):
    inst, name = args
    cfg = CONFIGS[name]
    try:
        tr, st = simulate(inst, cfg, GAINERS if cfg.get('gainers') else None)
    except Exception as exc:  # 单币失败不影响整体
        return inst, [], dict(error=repr(exc))
    return inst, tr, st


def _init():
    A.__dict__.setdefault('_orig_chan', A.chan)


GAINERS = None


def build_gainers(universe, top=20):
    """模拟实盘选币：每个整点，按过去24小时涨幅（只取上涨的）排前 top 名（用15m收盘价计算）。"""
    closes = {}
    for inst in universe:
        path = os.path.join(DATA, f'{inst}_15m.npz')
        if os.path.exists(path):
            z = np.load(path); closes[inst] = dict(zip(z['ts'].tolist(), z['close'].tolist()))
    hours = sorted({t // 3600000 * 3600000 for c in closes.values() for t in c})
    table = {}
    for h in hours:
        now = h - 900000; before = now - 86400000   # 该小时之前最后一根收盘 与 24h 前
        chg = [(c[now] / c[before] - 1, inst) for inst, c in closes.items() if now in c and before in c and c[before] > 0]
        table[h] = {inst for g, inst in sorted(chg, reverse=True)[:top] if g > 0}
    def ok(inst, ts):
        # 实盘每10分钟刷新一次榜单；这里用信号K所在小时的榜单
        return inst in table.get(ts // 3600000 * 3600000, set())
    return ok

if __name__ == '__main__':
    name = sys.argv[1]; procs = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    universe = json.load(open(os.path.join(DATA, 'universe.json')))
    extra = os.path.join(DATA, 'universe_extra.json')
    if os.path.exists(extra): universe += [i for i in json.load(open(extra)) if i not in universe]
    if CONFIGS[name].get('crypto'):
        cats = json.load(open(os.path.join(DATA, 'categories.json'))); universe = [i for i in universe if cats.get(i) == '1']
    t0 = time.time()
    if CONFIGS[name].get('gainers'):
        GAINERS = build_gainers(universe)   # fork 后子进程继承
    with Pool(procs, initializer=_init) as pool:
        rows = pool.map(run_one, [(i, name) for i in universe])
    out = dict(config=name, cfg=CONFIGS[name], trades=[], stats={})
    for inst, tr, st in rows:
        out['trades'] += tr; out['stats'][inst] = st
    json.dump(out, open(os.path.join(DATA, f'result_{name}.json'), 'w'), ensure_ascii=False)
    print(name, 'trades', len(out['trades']), 'seconds', round(time.time() - t0))
