"""清洗接盘用的 2022-01 ~ 2023-12 数据（币安官方存档）：合约 / 现货 5 分钟K线、持仓量（metrics，每天一个文件）、资金费率"""
import io, zipfile, os, datetime as dt, httpx, pandas as pd
from concurrent.futures import ThreadPoolExecutor
FUT = dict(l.split() for l in open('/home/user/ext/long/syms.txt'))
coins = open('flush_old_coins.txt').read().split()
MONTHS = [f'{y}-{m:02d}' for y in (2022, 2023) for m in range(1, 13)]
DAYS = [dt.date(2022, 1, 1) + dt.timedelta(i) for i in range((dt.date(2024, 1, 1) - dt.date(2022, 1, 1)).days)]
C = httpx.Client(timeout=30, limits=httpx.Limits(max_connections=32))
B = 'https://data.binance.vision/data'
KC = ['ts', 'open', 'high', 'low', 'close', 'volume', 'ct', 'quote_volume', 'n', 'taker_buy_volume', 'taker_buy_quote_volume', 'ig']
def get(u):
    for _ in range(3):
        try:
            r = C.get(u)
            if r.status_code == 404: return None
            if r.status_code == 200:
                z = zipfile.ZipFile(io.BytesIO(r.content)); return z.read(z.namelist()[0]).decode()
        except Exception: pass
    return None
def kl(base, sym):
    ps = []
    for m in MONTHS:
        t = get(f'{B}/{base}/monthly/klines/{sym}/5m/{sym}-5m-{m}.zip')
        if t:
            rows = [l.split(',') for l in t.strip().split('\n') if l and l[0].isdigit()]
            ps.append(pd.DataFrame(rows, columns=KC).drop(columns=['ct', 'n', 'ig']).astype(float))
    if not ps: return None
    d = pd.concat(ps); d['ts'] = d.ts.astype('int64'); d.loc[d.ts > 1e14, 'ts'] //= 1000
    return d.drop_duplicates('ts').sort_values('ts')
def met(sym):
    def one(d):
        t = get(f'{B}/futures/um/daily/metrics/{sym}/{sym}-metrics-{d}.zip')
        return pd.read_csv(io.StringIO(t)) if t else None
    with ThreadPoolExecutor(16) as ex:
        ps = [p for p in ex.map(one, DAYS) if p is not None]
    return pd.concat(ps) if ps else None
def fund(sym):
    ps = []
    for m in MONTHS:
        t = get(f'{B}/futures/um/monthly/fundingRate/{sym}/{sym}-fundingRate-{m}.zip')
        if t: ps.append(pd.read_csv(io.StringIO(t)))
    return pd.concat(ps) if ps else None
for c in coins:
    sym = FUT[c]
    if os.path.exists(f'f5/met/{c}.parquet'): continue
    k = kl('futures/um', sym); s = kl('spot', f'{c}USDT'); m = met(sym); f = fund(sym)
    if k is not None: k.to_parquet(f'f5/k/{c}.parquet')
    if s is not None: s.to_parquet(f'f5/spot/{c}.parquet')
    if f is not None: f.to_parquet(f'f5/met/{c}_funding.parquet')
    if m is not None: m.to_parquet(f'f5/met/{c}.parquet')
    print(c, None if k is None else len(k), None if s is None else len(s), None if m is None else len(m), None if f is None else len(f), flush=True)
print('DONE')
