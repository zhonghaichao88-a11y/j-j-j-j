"""压缩释放突破（squeeze_breakout）验证：老40币/新30币 × 1小时/5分钟，两套出场设置。
与 bt_strat.py 同一套：逐根用系统 alpha_fast_v7.decide()（288 根窗口）决定开仓，bt_v7.manage 出场。
加速：先在整段数据上用同样的指标算出“压缩后释放并突破布林带”的候选K线，只在候选K线上调用 decide()；
其余K线只做持仓管理。EMA/ATR 在 288 根窗口与整段上的差异 < 1e-9，不影响候选（启动时抽样核对）。
设置：default = 回测用的系统默认（趋势仓/就近目标/收盘确认 开）；page = 用户页面（三项都关，RR=2），移动止损都开。
用法: python3 bt_squeeze.py 数据集 设置 [进程数]；数据集 old40_1h / new30_1h / old40_5m / new30_5m
"""
import json, os, sys
import numpy as np
_argv = list(sys.argv); sys.argv = [sys.argv[0], '1h']
import bt_strat as S           # 向量化 roll/wma 已替换
sys.argv = _argv
B, A, V = S.B, S.A, S.V
DS, SET = sys.argv[1], sys.argv[2]
PROCS = int(sys.argv[3]) if len(sys.argv) > 3 else 1
TF = DS.split('_')[1]; MS = {'1h': 3600000, '5m': 300000}[TF]
EXTRA = {'default': {}, 'page': dict(runner=0, dynamic_tp=0, close_confirm=0)}[SET]
PARAMS = V.validate_params({'strategy': 'squeeze_breakout', 'base_tf': TF, 'max_hold_bars': 48, **EXTRA})
CFG = dict(tf=TF, trail=True); WIN = 300


def resample(f, k, ms):
    b = f['ts'] // (k * ms) * (k * ms)
    keys, start, cnt = np.unique(b, return_index=True, return_counts=True); keep = cnt == k
    s = start[keep]
    return dict(ts=keys[keep].astype(np.int64), open=f['open'][s], high=np.maximum.reduceat(f['high'], start)[keep],
                low=np.minimum.reduceat(f['low'], start)[keep], close=f['close'][s + cnt[keep] - 1],
                volume=np.add.reduceat(f['volume'], start)[keep])


def chunks(f, ms):
    cuts = np.flatnonzero(np.diff(f['ts']) != ms) + 1
    return [{k: v[a:b] for k, v in f.items()} for a, b in zip(np.r_[0, cuts], np.r_[cuts, len(f['ts'])]) if b - a >= 600]


def load(inst):
    grp = DS.split('_')[0]
    if grp == 'bnx':
        p = os.path.join(B.DATA, f'{inst}_bn1h.npz')
        if not os.path.exists(p): return []
        z = np.load(p); return chunks({k: z[k] for k in ('ts', 'open', 'high', 'low', 'close', 'volume')}, 3600000)
    if grp == 'more':
        p = os.path.join(B.DATA, f'{inst}_1h.npz')
        if not os.path.exists(p): return []
        z = np.load(p); return chunks({k: z[k] for k in ('ts', 'open', 'high', 'low', 'close', 'volume')}, 3600000)
    if grp in ('old40', 'rest34') and TF == '1h':
        return sum((B.load(inst, '1h', suf) for suf in ('15m_old', '15m')), [])
    p = os.path.join(B.DATA, f"{inst}_{'5m2y' if grp == 'old40' else '5mnew'}.npz")
    if not os.path.exists(p): return []
    z = np.load(p); f = {k: z[k] for k in ('ts', 'open', 'high', 'low', 'close', 'volume')}
    out = []
    for c in chunks(f, 300000):
        out += [c] if TF == '5m' else chunks(resample(c, 12, 300000), 3600000)
    return out


def candidates(f):
    ind = A.indicators(f); sq = ind['squeeze']; c = f['close']
    rel = np.zeros(len(c), bool)
    for j in range(6, len(c)): rel[j] = sq[j - 5:j].any() and not sq[j]
    return set(np.flatnonzero(rel & ((c > ind['boll_up']) | (c < ind['boll_low']))).tolist())


def sim(inst):
    trades = []; checked = entered = 0
    for f in load(inst):
        n = len(f['ts']); pos = None; cand = candidates(f)
        for j in range(WIN, n):
            F = {k: v[j - WIN + 1:j + 1] for k, v in f.items()}; jj = WIN - 1
            if pos is not None:
                done = B.manage(pos, F, jj, dict(signals=[]), PARAMS, CFG, MS)
                if done: trades.append(done); pos = None
            if pos is not None or j not in cand: continue
            checked += 1
            out = V.decide(inst, dict(frames={TF: F}, ticker_last=float(F['close'][-1]), spread_bps=2,
                                      as_of_ms=int(F['ts'][-1]) + MS + 1000), PARAMS)
            if out['signal'] == 'FLAT': continue
            entered += 1
            fs = out['fast_strategy']; d = 1 if out['signal'] == 'LONG' else -1; entry = float(F['close'][-1])
            pos = dict(symbol=inst, side='long' if d == 1 else 'short', d=d, entry=entry, opened_ms=int(F['ts'][-1]) + MS,
                       sl=float(fs['sl_price']), tp=float(fs['tp_price']), original_tp_price=float(fs['tp_price']),
                       base_sl_pct=float(out['sl']), base_tp_pct=float(out['tp']), label='squeeze', kind='squeeze',
                       cost=float(fs['estimated_round_cost']), risk=abs(entry - float(fs['sl_price'])), **V.position_meta(out))
            pos['opened_at'] = pos['opened_ms'] / 1000
            if (pos.get('v7_exit_config') or {}).get('runner'): pos['tp'] = V.runner_target(pos); pos['runner'] = True
            pos['trail_active_px'], pos['trail_cb'] = V.native_trail_config(pos)
        if pos is not None: trades.append(B.close(pos, float(f['close'][-1]), int(f['ts'][-1]), '数据结束'))
    return trades, dict(checked=checked, entered=entered)


def run(inst):
    d = os.path.join(B.DATA, f'sq_cache_{DS}_{SET}'); os.makedirs(d, exist_ok=True); fn = os.path.join(d, inst + '.json')
    if os.path.exists(fn): return
    try: tr, st = sim(inst)
    except Exception as exc: print(inst, 'ERR', repr(exc), flush=True); return
    json.dump(dict(trades=tr, stats=st), open(fn, 'w')); print(inst, len(tr), st, flush=True)


if __name__ == '__main__':
    S.check()
    grp = DS.split('_')[0]
    insts = json.load(open(os.path.join(B.DATA, {'old40': 'universe_5m40.json', 'new30': 'universe_new30.json', 'rest34': 'universe_rest.json', 'more': 'universe_more.json', 'bnx': 'universe_bnx.json'}[grp])))
    insts = [i if isinstance(i, str) else i['inst'] for i in insts]
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(PROCS) as ex: list(ex.map(run, insts))
    print('DONE', flush=True)
