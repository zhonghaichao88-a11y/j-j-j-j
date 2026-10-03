"""币安官方存档 → freqtrade 欧易格式（同一个币，价格按 1000PEPE 这类倍数换算回来）。
5 分钟K线合成 15m/1h/4h/1d；1 小时标记价格；资金费率（币安真实结算记录）。2025-08 ~ 2026-09"""
import io, zipfile, json, os, sys, httpx, pandas as pd
from concurrent.futures import ThreadPoolExecutor
OUT = 'ft/data_all/okx/futures'
MONTHS = [f'{y}-{m:02d}' for y, ms in ((2025, range(8, 13)), (2026, range(1, 10))) for m in ms]
C = httpx.Client(timeout=30, limits=httpx.Limits(max_connections=40))
B = 'https://data.binance.vision/data/futures/um/monthly'
def get(u):
    for _ in range(3):
        try:
            r = C.get(u)
            if r.status_code == 404: return None
            if r.status_code == 200:
                z = zipfile.ZipFile(io.BytesIO(r.content)); return z.read(z.namelist()[0]).decode()
        except Exception: pass
    return None
def kl(path, sym, tf):
    with ThreadPoolExecutor(14) as ex:
        ts = list(ex.map(lambda m: get(f'{B}/{path}/{sym}/{tf}/{sym}-{tf}-{m}.zip'), MONTHS))
    rows = [l.split(',') for t in ts if t for l in t.strip().split('\n') if l and l[0].isdigit()]
    if not rows: return None
    d = pd.DataFrame([(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows],
                     columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
    d.loc[d.ts > 1e14, 'ts'] //= 1000
    d = d.drop_duplicates('ts').sort_values('ts')
    d['date'] = pd.to_datetime(d.ts, unit='ms', utc=True).astype('datetime64[ms, UTC]')
    return d[['date', 'open', 'high', 'low', 'close', 'volume']].reset_index(drop=True)
def res(d, rule):
    x = d.set_index('date').resample(rule, label='left', closed='left').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
    return x.reset_index()
def one(c):
    base = f'{OUT}/{c}_USDT_USDT'
    have_px = os.path.exists(base + '-5m-futures.feather')
    for mult, sym in ((1, f'{c}USDT'), (1000, f'1000{c}USDT'), (1_000_000, f'1000000{c}USDT'), (1_000_000, f'1M{c}USDT')):
        if get(f'{B}/fundingRate/{sym}/{sym}-fundingRate-2026-06.zip') or get(f'{B}/klines/{sym}/5m/{sym}-5m-2026-06.zip'):
            break
    else:
        return c, '币安没有', have_px
    fr = [get(f'{B}/fundingRate/{sym}/{sym}-fundingRate-{m}.zip') for m in MONTHS]
    rows = [l.split(',') for t in fr if t for l in t.strip().split('\n') if l and l[0].isdigit()]
    if rows:
        f = pd.DataFrame({'date': pd.to_datetime([int(r[0]) for r in rows], unit='ms', utc=True).floor('h').astype('datetime64[ms, UTC]'),
                          'funding_rate': [float(r[2]) for r in rows]}).drop_duplicates('date').sort_values('date')
        f.reset_index(drop=True).to_feather(base + '-1h-funding_rate.feather')
    if have_px:
        return c, '价格用欧易，补了资金费', len(rows)
    k = kl('klines', sym, '5m')
    if k is None or len(k) < 288 * 30: return c, '数据太少', None
    for col in ('open', 'high', 'low', 'close'): k[col] /= mult
    k['volume'] *= mult
    k.to_feather(base + '-5m-futures.feather')
    for tf, rule in (('15m', '15min'), ('1h', '1h'), ('4h', '4h'), ('1d', '1D')):
        res(k, rule).to_feather(f'{base}-{tf}-futures.feather')
    m = kl('markPriceKlines', sym, '1h')
    if m is None:
        m = res(k, '1h')                                   # 没有标记价格就用成交价（已经换算过倍数）
    elif mult != 1:
        for col in ('open', 'high', 'low', 'close'): m[col] /= mult
    m.to_feather(base + '-1h-mark.feather')
    return c, f'币安 {sym}', len(k)
pairs = [p.split('/')[0] for p in json.load(open('nfi/all_pairs.json'))]
with ThreadPoolExecutor(4) as ex:
    for r in ex.map(one, pairs): print(*r, flush=True)
print('CONV_DONE')
