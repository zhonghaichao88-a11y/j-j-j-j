"""豆包合约包（币安 5 分钟K线 2020-01 ~ 2026-09）→ freqtrade 欧易格式，给 NFI 长周期回测用。
选币：每一年按当年成交额排前 60 的币（当年要有 300 天以上的数据），模拟实盘"按成交额动态选币"。
价格：1000PEPE 这类按倍数换算成欧易的 PEPE；合成 15m/1h/4h/1d；1 小时标记价格用成交价代替；资金费率用币安真实结算记录。
输出：/home/user/ext/ft/data_long/okx/futures，选币表 /home/user/ext/nfi/long_pairs.json（每年一张）。"""
import io, os, json, zipfile, httpx, numpy as np, pandas as pd
from concurrent.futures import ThreadPoolExecutor
SRC = '/home/user/ext/bnk'; OUT = '/home/user/ext/ft/data_long/okx/futures'; os.makedirs(OUT, exist_ok=True)
YEARS = list(range(2020, 2027)); TOPN = 60
C = httpx.Client(timeout=30, limits=httpx.Limits(max_connections=30))
B = 'https://data.binance.vision/data/futures/um/monthly/fundingRate'
STABLE = {'USDC', 'BUSD', 'TUSD', 'FDUSD', 'USDP', 'DAI', 'USDE'}
CAT = json.load(open('/home/user/ext/long/lab/okx_category.json'))      # 欧易分类：1 = 加密币；股票、黄金等不测


def mult_of(c):
    for p, m in (('1000000', 1_000_000), ('1M', 1_000_000), ('1000', 1000)):
        if c.startswith(p) and len(c) > len(p): return c[len(p):], m
    return c, 1


def get(u):
    for _ in range(3):
        try:
            r = C.get(u)
            if r.status_code == 404: return None
            if r.status_code == 200:
                z = zipfile.ZipFile(io.BytesIO(r.content)); return z.read(z.namelist()[0]).decode()
        except Exception: pass
    return None


# ---- 选币：每年成交额前 60
vol = {}
for f in sorted(os.listdir(SRC)):
    if not f.endswith('.parquet'): continue
    c = f[:-8]
    if mult_of(c)[0] in STABLE or CAT.get(mult_of(c)[0], '1') != '1' or CAT.get(c, '1') != '1': continue
    d = pd.read_parquet(f'{SRC}/{f}', columns=['ts', 'quote_volume'])
    y = pd.to_datetime(d.ts, unit='ms').dt.year
    g = d.groupby(y).agg(n=('ts', 'size'), qv=('quote_volume', 'sum'))
    for yr, r in g.iterrows():
        if r.n >= 288 * 300 or (yr == 2026 and r.n >= 288 * 220): vol.setdefault(int(yr), {})[c] = float(r.qv)
pairs = {yr: sorted(v, key=v.get, reverse=True)[:TOPN] for yr, v in vol.items() if yr in YEARS}
need = sorted(set(c for v in pairs.values() for c in v) | {'BTC'})
json.dump({str(k): [f'{mult_of(c)[0]}/USDT:USDT' for c in v] for k, v in pairs.items()}, open('/home/user/ext/nfi/long_pairs.json', 'w'), indent=1)
print({k: len(v) for k, v in pairs.items()}, '要转换的币', len(need), flush=True)


def res(d, rule):
    return d.set_index('date').resample(rule, label='left', closed='left').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna().reset_index()


def one(c):
    name, mult = mult_of(c)
    base = f'{OUT}/{name}_USDT_USDT'
    if os.path.exists(base + '-1h-mark.feather'): return c, '已有'
    k = pd.read_parquet(f'{SRC}/{c}.parquet', columns=['ts', 'open', 'high', 'low', 'close', 'volume']).drop_duplicates('ts').sort_values('ts')
    k['date'] = pd.to_datetime(k.ts, unit='ms', utc=True).astype('datetime64[ms, UTC]')
    k = k[['date', 'open', 'high', 'low', 'close', 'volume']].astype({'open': float, 'high': float, 'low': float, 'close': float, 'volume': float}).reset_index(drop=True)
    for col in ('open', 'high', 'low', 'close'): k[col] /= mult
    k['volume'] *= mult
    k.to_feather(base + '-5m-futures.feather', compression='zstd')
    for tf, rule in (('15m', '15min'), ('1h', '1h'), ('4h', '4h'), ('1d', '1D')):
        res(k, rule).to_feather(f'{base}-{tf}-futures.feather', compression='zstd')
    res(k, '1h').to_feather(base + '-1h-mark.feather', compression='zstd')          # 标记价格用成交价代替
    months = pd.period_range(k.date.iloc[0].strftime('%Y-%m'), '2026-09', freq='M').strftime('%Y-%m')
    sym = f'{c}USDT'
    with ThreadPoolExecutor(8) as ex:
        fr = list(ex.map(lambda m: get(f'{B}/{sym}/{sym}-fundingRate-{m}.zip'), months))
    rows = [l.split(',') for t in fr if t for l in t.strip().split('\n') if l and l[0].isdigit()]
    if rows:
        f = pd.DataFrame({'date': pd.to_datetime([int(r[0]) for r in rows], unit='ms', utc=True).floor('h').astype('datetime64[ms, UTC]'),
                          'funding_rate': [float(r[2]) for r in rows]}).drop_duplicates('date').sort_values('date')
        f.reset_index(drop=True).to_feather(base + '-1h-funding_rate.feather', compression='zstd')   # 和之前能跑的格式一样（date + funding_rate）
    return c, f'{len(k)} 根，资金费 {len(rows)} 条'


with ThreadPoolExecutor(3) as ex:
    for r in ex.map(one, need): print(*r, flush=True)
print('CONV_DONE')
