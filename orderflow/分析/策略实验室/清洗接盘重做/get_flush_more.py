"""清洗接盘补数据：币安官方存档 2021-12 ~ 2023-12，更多的币（原来 38 个以外，凡是那时有合约的都下）。
合约 / 现货 5 分钟K线、持仓量和多空比（metrics，每天一个文件）、资金费率。只存测试要用的列，压缩存。"""
import io, zipfile, os, sys, datetime as dt, httpx, pandas as pd
from concurrent.futures import ThreadPoolExecutor
have = set(open('flush_old_coins.txt').read().split())
FUT = dict(l.split() for l in open('/home/user/ext/long/syms.txt'))
for f in os.listdir('old'):                      # 2021~2023 年有过的币（币安合约代码）
    s = f[:-8]; c = s[:-4]
    for p in ('1000000', '1000'):
        if c.startswith(p): c = c[len(p):]
    FUT.setdefault(c, s)
coins = sorted(c for c in FUT if c not in have)
part, n = int(sys.argv[1]), int(sys.argv[2]); coins = coins[part::n]
MONTHS = [f'{y}-{m:02d}' for y, m in [(2021, 12)] + [(y, m) for y in (2022, 2023) for m in range(1, 13)]]
DAYS = [dt.date(2021, 12, 1) + dt.timedelta(i) for i in range((dt.date(2024, 1, 1) - dt.date(2021, 12, 1)).days)]
C = httpx.Client(timeout=30, limits=httpx.Limits(max_connections=32), proxy=os.environ.get('HTTPS_PROXY'))
B = 'https://data.binance.vision/data'
KC = ['ts', 'open', 'high', 'low', 'close', 'volume', 'ct', 'quote_volume', 'n', 'taker_buy_volume', 'taker_buy_quote_volume', 'ig']
def get(u):
    for _ in range(4):
        try:
            r = C.get(u)
            if r.status_code == 404: return None
            if r.status_code == 200:
                z = zipfile.ZipFile(io.BytesIO(r.content)); return z.read(z.namelist()[0]).decode()
        except Exception: pass
    return None
def kl(base, sym, cols):
    def one(m):
        t = get(f'{B}/{base}/monthly/klines/{sym}/5m/{sym}-5m-{m}.zip')
        if not t: return None
        rows = [l.split(',') for l in t.strip().split('\n') if l and l[0].isdigit()]
        return pd.DataFrame(rows, columns=KC)[cols].astype(float)
    with ThreadPoolExecutor(8) as ex:
        ps = [p for p in ex.map(one, MONTHS) if p is not None]
    if not ps: return None
    d = pd.concat(ps); d['ts'] = d.ts.astype('int64'); d.loc[d.ts > 1e14, 'ts'] //= 1000
    return d.drop_duplicates('ts').sort_values('ts')
def met(sym):
    def one(d):
        t = get(f'{B}/futures/um/daily/metrics/{sym}/{sym}-metrics-{d}.zip')
        if not t: return None
        x = pd.read_csv(io.StringIO(t))
        return x[[c for c in ('create_time', 'sum_open_interest_value', 'count_long_short_ratio', 'sum_toptrader_long_short_ratio') if c in x]]
    with ThreadPoolExecutor(16) as ex:
        ps = [p for p in ex.map(one, DAYS) if p is not None]
    return pd.concat(ps) if ps else None
def fund(sym):
    ps = []
    for m in MONTHS:
        t = get(f'{B}/futures/um/monthly/fundingRate/{sym}/{sym}-fundingRate-{m}.zip')
        if t: ps.append(pd.read_csv(io.StringIO(t)))
    return pd.concat(ps) if ps else None
os.makedirs('f6/k', exist_ok=True); os.makedirs('f6/spot', exist_ok=True); os.makedirs('f6/met', exist_ok=True)
for c in coins:
    sym = FUT[c]
    if os.path.exists(f'f6/done_{c}'): continue
    k = kl('futures/um', sym, ['ts', 'open', 'high', 'low', 'close', 'quote_volume', 'taker_buy_quote_volume'])
    if k is None or len(k) < 30000:                # 那时还没有合约（或太短）
        open(f'f6/done_{c}', 'w').write('无'); print(c, '无合约', flush=True); continue
    m = met(sym)
    if m is None:
        open(f'f6/done_{c}', 'w').write('无持仓'); print(c, '无持仓量', flush=True); continue
    s = kl('spot', f'{c}USDT', ['ts', 'close', 'quote_volume', 'taker_buy_quote_volume']); f = fund(sym)
    k.to_parquet(f'f6/k/{c}.parquet', compression='zstd'); m.to_parquet(f'f6/met/{c}.parquet', compression='zstd')
    if s is not None: s.to_parquet(f'f6/spot/{c}.parquet', compression='zstd')
    if f is not None: f.to_parquet(f'f6/met/{c}_funding.parquet', compression='zstd')
    open(f'f6/done_{c}', 'w').write('ok')
    print(c, len(k), None if s is None else len(s), len(m), None if f is None else len(f), flush=True)
print('DONE', flush=True)
