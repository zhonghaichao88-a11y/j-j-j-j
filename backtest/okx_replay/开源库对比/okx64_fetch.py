"""欧易上方案三没测过的币（50 个没测过 + 14 个只用币安数据测过）：下载 2024-09-29 起的 1 小时K线（公开接口，只读）+ BTC 日线。"""
import ccxt, os, glob, json, time, threading
import numpy as np
from concurrent.futures import ThreadPoolExecutor
px = os.environ.get('HTTPS_PROXY')
ex = ccxt.okx({'proxies': {'http': px, 'https': px}}); ex.load_markets()
crypto = {m['id'] for m in ex.markets.values() if m.get('swap') and m.get('quote') == 'USDT' and m.get('settle') == 'USDT' and m.get('active') and str((m.get('info') or {}).get('instCategory') or '1') == '1'}
ft = {os.path.basename(p).split('_')[0] for p in glob.glob('/home/user/ext/ft/data/okx/futures/*-1h-futures.feather')}
okx = {os.path.basename(p).replace('_1h.npz', '').split('-')[0] for p in glob.glob('/home/user/okx_data/*_1h.npz') if '_bn' not in p} | ft
syms = sorted(i for i in crypto if i.split('-')[0] not in okx)
json.dump(syms, open('universe.json', 'w')); print('币数', len(syms), flush=True)
T0 = 1727568000000   # 2024-09-29 UTC
L = threading.Lock(); last = [0.0]
def req(args, hist):
    for k in range(6):
        with L:
            w = last[0] + 0.11 - time.monotonic()
            if w > 0: time.sleep(w)
            last[0] = time.monotonic()
        try:
            r = ex.request('market/history-candles' if hist else 'market/candles', 'public', 'GET', args)
            if str(r.get('code')) == '0': return r['data']
        except Exception: pass
        time.sleep(1 + k)
    raise RuntimeError('请求失败')
def one(inst, bar='1H', step=3600000, out=None):
    out = out or f'{inst}_1h.npz'
    if os.path.exists(out): return inst, 'skip'
    rows = {}; after = None
    while True:
        args = {'instId': inst, 'bar': bar, 'limit': '100'}
        if after: args['after'] = str(after)
        d = req(args, True)
        if not d: break
        for r in d:
            if str(r[8]) == '1': rows[int(r[0])] = [float(x) for x in r[1:6]]
        after = min(int(r[0]) for r in d)
        if after <= T0 - 60 * 86400000 * (bar == '1Dutc') : break
        if bar == '1H' and after <= T0: break
    if not rows: return inst, 'none'
    ts = np.array(sorted(rows), np.int64); a = np.array([rows[t] for t in ts])
    np.savez_compressed(out, ts=ts, open=a[:, 0], high=a[:, 1], low=a[:, 2], close=a[:, 3], volume=a[:, 4])
    return inst, len(ts)
print(one('BTC-USDT-SWAP', '1Dutc', 86400000, 'BTC_1d.npz'), flush=True)
with ThreadPoolExecutor(6) as p:
    for r in p.map(one, syms): print(r, flush=True)
print('DONE', flush=True)
