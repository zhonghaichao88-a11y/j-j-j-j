"""下载 40 个币两年的 1 分钟K线（与 5m2y 同一时间窗口）。全局限速约 9 次/秒；已下完的币跳过，可断点续跑。"""
import json, os, time, threading, urllib.request
from concurrent.futures import ThreadPoolExecutor
import numpy as np
BASE = 'https://www.okx.com/api/v5/'
lock = threading.Lock(); last = [0.0]; GAP = 1 / 9
def get(path):
    for k in range(10):
        with lock:
            wait = last[0] + GAP - time.time()
            if wait > 0: time.sleep(wait)
            last[0] = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(BASE + path, headers={'User-Agent': 'Mozilla/5.0 alpha-x-backtest'}), timeout=20) as r:
                d = json.load(r)
            if d.get('code') == '0': return d['data']
            time.sleep(2)
        except Exception:
            time.sleep(1.5 * (k + 1))
    raise RuntimeError('failed ' + path)
def job(inst):
    out = f'{inst}_1m2y.npz'
    if os.path.exists(out): return
    z = np.load(f'{inst}_5m2y.npz'); start = int(z['ts'][0]); end = int(z['ts'][-1]) + 300000
    rows = {}; after = end
    while True:
        page = get(f'market/history-candles?instId={inst}&bar=1m&limit=300&after={after}')
        if not page: break
        for r in page:
            if r[8] == '1': rows[int(r[0])] = [float(x) for x in r[1:6]]
        oldest = min(int(r[0]) for r in page)
        if oldest <= start or oldest >= after: break
        after = oldest
    ts = np.array(sorted(t for t in rows if start <= t < end), dtype=np.int64)
    if not len(ts): print(inst, 'EMPTY', flush=True); return
    v = np.array([rows[t] for t in ts]); tmp = out + '.part.npz'
    np.savez_compressed(tmp, ts=ts, open=v[:, 0], high=v[:, 1], low=v[:, 2], close=v[:, 3], volume=v[:, 4]); os.replace(tmp, out)
    print(f'{inst} bars={len(ts)} days={len(ts) / 1440:.0f} gaps={int(np.sum(np.diff(ts) != 60000))}', flush=True)
u = json.load(open('universe_5m40.json'))
todo = [i for i in u if not os.path.exists(f'{i}_1m2y.npz')]
print('remaining', len(todo), flush=True)
with ThreadPoolExecutor(10) as ex:
    list(ex.map(job, todo))
print('DONE', flush=True)
