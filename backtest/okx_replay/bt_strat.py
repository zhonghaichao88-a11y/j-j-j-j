"""V7 其余策略（非缠论）回放：逐根调用系统自己的 alpha_fast_v7.decide()（与实盘同一套开仓门槛、止损止盈），
出场用 bt_v7.manage（交易所止损/移动止损/止盈/持仓到期/收盘确认结构失效），每个策略各自独立持仓。
指标里的滑动均值/加权均值换成等价的向量写法（启动时抽样核对与原函数逐位一致）。
用法: python3 bt_strat.py 15m|1h [进程数]   → result_strat_<tf>.json
"""
import json, os, sys, time
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as swv
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_v7 as B
A, V = B.A, B.V
_orig_roll, _orig_wma = A.roll, A.wma

def roll(x, n, op=np.mean):
    x = np.asarray(x, float); m = len(x); out = np.empty(m)
    for i in range(min(n - 1, m)): out[i] = op(x[:i + 1])
    if m >= n: out[n - 1:] = op(swv(x, n), axis=1)
    return out

def wma(x, n):
    x = np.asarray(x, float); m = len(x); out = np.empty(m)
    for i in range(min(n - 1, m)): out[i] = np.average(x[:i + 1], weights=np.arange(1, i + 2))
    if m >= n:
        w = np.arange(1, n + 1, dtype=float); out[n - 1:] = np.multiply(swv(x, n), w).sum(axis=1) / w.sum()
    return out

def check():
    rng = np.random.default_rng(1); x = np.cumsum(rng.normal(size=300)) + 100
    for op in (np.mean, np.std, np.sum, np.min, np.max):
        for n in (9, 14, 20, 22):
            a, b = _orig_roll(x, n, op), roll(x, n, op)
            assert np.allclose(a, b, rtol=1e-12, atol=1e-12), (op, n, np.max(abs(a - b)))
    for n in (4, 10, 20): assert np.allclose(_orig_wma(x, n), wma(x, n), rtol=1e-12, atol=1e-12)

A.roll, A.wma = roll, wma
STRATS = [s for s in V.LABELS if s not in ('chan_quant', 'pine_import')]
TF = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].isdigit() else '15m'
MS = {'15m': 900000, '1h': 3600000}[TF]
CFG = dict(tf=TF, trail=True)
WIN = 300

def load(inst):
    out = []
    for suf in (('15m',) if TF == '15m' else ('15m_old', '15m')):      # 15m：最近半年；1h：约一年
        out += B.load(inst, TF, suf)
    return out

def sim(inst):
    params = {s: V.validate_params({'strategy': s, 'base_tf': TF, 'max_hold_bars': 48}) for s in STRATS}
    trades = []; stats = {s: dict(sig=0, entered=0) for s in STRATS}
    for f in load(inst):
        n = len(f['ts']); pos = {s: None for s in STRATS}
        for j in range(WIN, n):
            F = {k: v[j - WIN + 1:j + 1] for k, v in f.items()}; jj = WIN - 1
            for s in STRATS:
                p = pos[s]
                if p is not None:
                    done = B.manage(p, F, jj, dict(signals=[]), params[s], CFG, MS)
                    if done: done['strategy'] = s; trades.append(done); pos[s] = None
            data = dict(frames={TF: F}, ticker_last=float(F['close'][-1]), spread_bps=2, as_of_ms=int(F['ts'][-1]) + MS + 1000)
            for s in STRATS:
                if pos[s] is not None: continue
                out = V.decide(inst, data, params[s])
                if out['signal'] == 'FLAT':
                    if '未触发' not in out['reason']: stats[s]['sig'] += 1
                    continue
                stats[s]['sig'] += 1; stats[s]['entered'] += 1
                fs = out['fast_strategy']; d = 1 if out['signal'] == 'LONG' else -1; entry = float(F['close'][-1])
                p = dict(symbol=inst, side='long' if d == 1 else 'short', d=d, entry=entry, opened_ms=int(F['ts'][-1]) + MS,
                         sl=float(fs['sl_price']), tp=float(fs['tp_price']), original_tp_price=float(fs['tp_price']),
                         base_sl_pct=float(out['sl']), base_tp_pct=float(out['tp']), label=s, kind=s,
                         cost=float(fs['estimated_round_cost']), risk=abs(entry - float(fs['sl_price'])), **V.position_meta(out))
                p['opened_at'] = p['opened_ms'] / 1000
                if (p.get('v7_exit_config') or {}).get('runner'): p['tp'] = V.runner_target(p); p['runner'] = True
                p['trail_active_px'], p['trail_cb'] = V.native_trail_config(p)
                pos[s] = p
        for s, p in pos.items():
            if p is not None:
                t = B.close(p, float(f['close'][-1]), int(f['ts'][-1]), '数据结束'); t['strategy'] = s; trades.append(t)
    return inst, trades, stats

def run(inst):
    d = os.path.join(B.DATA, f'strat_cache_{TF}'); os.makedirs(d, exist_ok=True); fn = os.path.join(d, inst + '.json')
    if os.path.exists(fn): return
    try: r = sim(inst)
    except Exception as exc: print(inst, 'ERR', repr(exc), flush=True); return
    json.dump(dict(trades=r[1], stats=r[2]), open(fn, 'w'), ensure_ascii=False); print(inst, len(r[1]), flush=True)

if __name__ == '__main__':
    check(); print('向量化核对通过', flush=True)
    procs = int(sys.argv[-1]) if sys.argv[-1].isdigit() else 1
    insts = json.load(open(os.path.join(B.DATA, 'universe_5m40.json')))
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(procs) as ex: list(ex.map(run, insts))
    print('DONE', flush=True)
