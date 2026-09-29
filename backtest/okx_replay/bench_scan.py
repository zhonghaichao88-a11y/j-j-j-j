"""扫描性能模拟：本地真实 15m 行情 + 模拟网络延迟的假交易所，测一轮扫描的耗时与请求数。
用法: python3 bench_scan.py 币数 [延迟秒]
"""
import json, os, sys, tempfile, time as _time
import numpy as np
sys.path.insert(0, os.environ.get('ALPHA_REPO', os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))))
DATA = os.environ.get('ALPHA_OKX_DATA', os.path.dirname(os.path.abspath(__file__)))
N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
LAT = float(sys.argv[2]) if len(sys.argv) > 2 else 0.15
MS = 900000

cats = json.load(open(os.path.join(DATA, 'categories.json')))
insts = [i for i, c in cats.items() if c == '1' and os.path.exists(os.path.join(DATA, f'{i}_15m.npz'))]
insts = ['BTC-USDT-SWAP'] + [i for i in insts if i != 'BTC-USDT-SWAP']
BARS = {}
for i in insts:
    z = np.load(os.path.join(DATA, f'{i}_15m.npz')); BARS[i] = {k: z[k] for k in ('ts', 'open', 'high', 'low', 'close', 'volume')}
T0 = int(BARS['BTC-USDT-SWAP']['ts'][-1]) - 200 * MS                 # 模拟“现在”：数据末尾前 200 根
# 只选在“现在”之前已有 4000 根以上连续历史、且之后仍有数据的币
insts = [i for i in insts if BARS[i]['ts'][0] < T0 - 4000 * MS and BARS[i]['ts'][-1] > T0 + 10 * MS][:max(N, 1)]
BARS = {i: BARS[i] for i in insts}
REAL = _time.perf_counter; REAL0 = REAL(); OFFSET = [0.0]
def now_s(): return (T0 + MS) / 1000 + OFFSET[0] + (REAL() - REAL0)
STATS = dict(requests=0)


class FakeExchange:
    id = 'okx'
    def market(self, cs): return {'id': cs.replace('/USDT:USDT', '-USDT-SWAP')}
    def _rows(self, inst, before=None, limit=300):
        b = BARS[inst]; now = now_s() * 1000
        m = b['ts'] <= now if before is None else b['ts'] < before
        idx = np.flatnonzero(m)[-limit:][::-1]
        return [[str(int(b['ts'][k])), *(f'{b[f][k]:.10g}' for f in ('open', 'high', 'low', 'close', 'volume')), '0', '0',
                 '1' if b['ts'][k] + MS <= now else '0'] for k in idx]
    def request(self, path, api, method, args):
        _time.sleep(LAT); STATS['requests'] += 1
        before = int(args['after']) if 'after' in args else None
        return {'code': '0', 'data': self._rows(args['instId'], before, int(args.get('limit', 300)))}
    def fetch_ticker(self, cs):
        _time.sleep(LAT); STATS['requests'] += 1
        inst = cs.replace('/USDT:USDT', '-USDT-SWAP'); b = BARS[inst]; k = np.searchsorted(b['ts'], now_s() * 1000, 'right') - 1
        px = float(b['close'][k]); return {'last': px, 'bid': px * 0.9999, 'ask': px * 1.0001}
    def fetch_tickers(self, params=None):
        _time.sleep(LAT); STATS['requests'] += 1
        out = {}
        for inst, b in BARS.items():
            k = np.searchsorted(b['ts'], now_s() * 1000, 'right') - 1; px = float(b['close'][k])
            out[inst.replace('-USDT-SWAP', '/USDT:USDT')] = {'last': px, 'bid': px * 0.9999, 'ask': px * 1.0001, 'quoteVolume': 1e8, 'percentage': 1.0, 'info': {}}
        return out


import time
time.time = now_s                                  # 所有模块看到的都是模拟时钟（延迟照样计入）
import alpha_v7_feed as feed
feed.HISTORY_ROOT = __import__('pathlib').Path(tempfile.mkdtemp(prefix='anchors'))
from okx_client import okx_client
fx = FakeExchange(); okx_client._exchange = fx
okx_client._ensure_connected = lambda: None
okx_client.get_ticker = lambda s=None: dict(fx.fetch_ticker(s.replace('-USDT-SWAP', '/USDT:USDT') if s and s.endswith('-SWAP') else s))
import alpha_fast_mode as fm
import alpha_fast_v7 as v7
fm.FAST_ACTIVE_VERSION = 'v7'
v7._RUNTIME = v7.validate_params(json.loads(os.environ.get('BENCH_PARAMS', '{"chan_scheme1": 1}')))


DECISIONS = {}
def cycle(label):
    STATS['requests'] = 0; t = REAL(); sig = 0
    if hasattr(fm, 'prefetch_v7'): fm.prefetch_v7(insts)
    for s in insts:
        r = fm.predict(s); sig += r.get('signal') in ('LONG', 'SHORT')
        DECISIONS.setdefault(label, {})[s] = (r.get('signal'), r.get('reason'), round(float(r.get('sl') or 0), 10), round(float(r.get('tp') or 0), 10))
        if 'V7' not in (r.get('reason') or '') and 'FAST裸K扫描异常' in (r.get('reason') or ''): print('ERR', s, r.get('reason'))
    dt = REAL() - t
    print(f'  {label}: {dt:6.1f} 秒，请求 {STATS["requests"]} 次，信号 {sig}')
    return dt


print(f'币数 {len(insts)}，模拟网络延迟 {LAT}s/请求')
cycle('首轮（冷启动，下载历史）')
# 下一根 K 线收盘后 2 秒（所有币都有新 K 线）
nxt = (int(now_s() * 1000) // MS + 1) * MS; OFFSET[0] += (nxt + 2000) / 1000 - now_s()
cycle('K线刚收盘（全部重算）')
OFFSET[0] += 20
cycle('同一根K线内的下一轮')
for k in range(int(os.environ.get('BENCH_BARS', '0'))):          # 继续逐根推进，收集更多决策（含开仓信号）
    nxt = (int(now_s() * 1000) // MS + 1) * MS; OFFSET[0] += (nxt + 2000) / 1000 - now_s()
    cycle(f'第{k + 2}根K线收盘')

out = os.environ.get('BENCH_OUT')
if out: json.dump(DECISIONS, open(out, 'w'), ensure_ascii=False)
