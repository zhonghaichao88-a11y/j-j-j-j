"""新币检验：30 个没测过的加密币，最近一年 5 分钟K线。限速 4 次/秒（与正在进行的 1 分钟下载共用 OKX 限额）。"""
import json, os, time, threading, urllib.request
import numpy as np
BASE = 'https://www.okx.com/api/v5/'; GAP = 1 / 4; last = [0.0]; lock = threading.Lock()
def get(path):
    for k in range(10):
        with lock:
            w = last[0] + GAP - time.time()
            if w > 0: time.sleep(w)
            last[0] = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(BASE + path, headers={'User-Agent': 'Mozilla/5.0 alpha-x-backtest'}), timeout=20) as r:
                d = json.load(r)
            if d.get('code') == '0': return d['data']
            time.sleep(2)
        except Exception:
            time.sleep(1.5 * (k + 1))
    raise RuntimeError('failed ' + path)
end = int(time.time() * 1000) // 300000 * 300000; start = end - 365 * 86400000
for inst in json.load(open('universe_new30.json')):
    out = f'{inst}_5mnew.npz'
    if os.path.exists(out): continue
    rows = {}; after = end
    while True:
        page = get(f'market/history-candles?instId={inst}&bar=5m&limit=300&after={after}')
        if not page: break
        for r in page:
            if r[8] == '1': rows[int(r[0])] = [float(x) for x in r[1:6]]
        oldest = min(int(r[0]) for r in page)
        if oldest <= start or oldest >= after: break
        after = oldest
    ts = np.array(sorted(t for t in rows if t >= start), dtype=np.int64)
    if not len(ts): print(inst, 'EMPTY', flush=True); continue
    v = np.array([rows[t] for t in ts])
    np.savez_compressed(out + '.part.npz', ts=ts, open=v[:, 0], high=v[:, 1], low=v[:, 2], close=v[:, 3], volume=v[:, 4]); os.replace(out + '.part.npz', out)
    print(f'{inst} bars={len(ts)} days={len(ts) * 300000 / 86400000:.0f} gaps={int(np.sum(np.diff(ts) != 300000))}', flush=True)
print('DONE', flush=True)
