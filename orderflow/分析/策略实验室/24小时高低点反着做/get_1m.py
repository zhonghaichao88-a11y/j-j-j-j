import sys, io, zipfile, os, urllib.request, pandas as pd, numpy as np
sys.path.insert(0, '/home/user/ext/long/lab'); import data
from concurrent.futures import ThreadPoolExecutor
cs = data.coins(); pick = cs[::4][:30]
months = [f'{y}-{m:02d}' for y in (2025, 2026) for m in range(1, 13) if (y, m) <= (2026, 8)]
def one(c):
    sym = data.SYMS[c]; out = f'{c}.parquet'
    if os.path.exists(out): return c, 'skip'
    parts = []
    for mo in months:
        u = f'https://data.binance.vision/data/futures/um/monthly/klines/{sym}/1m/{sym}-1m-{mo}.zip'
        try: b = urllib.request.urlopen(u, timeout=60).read()
        except Exception: continue
        z = zipfile.ZipFile(io.BytesIO(b)); f = z.open(z.namelist()[0])
        d = pd.read_csv(f, header=None, usecols=[0,1,2,3,4])
        if not str(d.iloc[0,0]).isdigit(): d = d.iloc[1:]
        d.columns = ['ts','o','h','l','c']; d = d.astype({'ts':'int64','o':'float64','h':'float64','l':'float64','c':'float64'})
        parts.append(d)
    if parts: pd.concat(parts).drop_duplicates('ts').sort_values('ts').to_parquet(out)
    return c, len(parts)
with ThreadPoolExecutor(6) as ex:
    for r in ex.map(one, pick): print(r, flush=True)
