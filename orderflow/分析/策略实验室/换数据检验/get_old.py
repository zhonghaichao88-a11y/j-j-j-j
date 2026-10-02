"""币安官方存档：2021-01 ~ 2023-12 的 1 小时K线（U本位合约），老币 + 我们那 102 个币"""
import io, zipfile, glob, os, httpx, pandas as pd
from concurrent.futures import ThreadPoolExecutor
syms = set(os.path.basename(f).replace('_bnold1h.npz', '') for f in glob.glob('bn/*_bnold1h.npz'))
syms |= set(l.split()[1] for l in open('/home/user/ext/long/syms.txt'))
months = [f'{y}-{m:02d}' for y in (2021, 2022, 2023) for m in range(1, 13)]
C = httpx.Client(timeout=30)
def get(sym, m):
    u = f'https://data.binance.vision/data/futures/um/monthly/klines/{sym}/1h/{sym}-1h-{m}.zip'
    for _ in range(3):
        try:
            r = C.get(u)
            if r.status_code == 404: return None
            if r.status_code == 200:
                z = zipfile.ZipFile(io.BytesIO(r.content)); raw = z.read(z.namelist()[0]).decode()
                rows = [l.split(',') for l in raw.strip().split('\n') if l and l[0].isdigit()]
                return pd.DataFrame([(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[7]), float(x[10])) for x in rows],
                                    columns=['ts', 'o', 'h', 'l', 'c', 'qv', 'bq'])
        except Exception: pass
    return None
def one(sym):
    if os.path.exists(f'old/{sym}.parquet'): return sym, 'have'
    ps = [p for p in (get(sym, m) for m in months) if p is not None]
    if not ps: return sym, 0
    d = pd.concat(ps).drop_duplicates('ts').sort_values('ts'); d.to_parquet(f'old/{sym}.parquet'); return sym, len(d)
with ThreadPoolExecutor(8) as ex:
    for r in ex.map(one, sorted(syms)): print(*r, flush=True)
print('DONE')
