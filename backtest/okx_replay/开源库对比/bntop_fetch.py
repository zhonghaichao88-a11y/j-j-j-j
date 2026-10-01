"""豆包口径：币安 U本位永续 1小时，2024-06 ~ 2026-09，当前成交额前100（这里用欧易前100对应的币安合约）。"""
import io, os, json, zipfile, urllib.request, ssl
import numpy as np, pandas as pd
from concurrent.futures import ThreadPoolExecutor
ctx = ssl.create_default_context(cafile='/root/.ccr/ca-bundle.crt')
op = urllib.request.build_opener(urllib.request.ProxyHandler({'https': os.environ.get('HTTPS_PROXY')}), urllib.request.HTTPSHandler(context=ctx))
def read(u):
    for _ in range(3):
        try: return op.open(urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'}), timeout=60).read()
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
        except Exception: pass
top = json.load(open('/home/user/ext/top100_now.json'))
syms = sorted({s.split('-')[0] + 'USDT' for s in top} | {'BTCUSDT'})
months = pd.period_range('2024-05', '2026-09', freq='M').strftime('%Y-%m')
def one(s):
    out = f'{s}.npz'
    if os.path.exists(out): return s, 'skip'
    ks = []
    for m in months:
        b = read(f'https://data.binance.vision/data/futures/um/monthly/klines/{s}/1h/{s}-1h-{m}.zip')
        if b is None: continue
        z = zipfile.ZipFile(io.BytesIO(b)); k = pd.read_csv(z.open(z.namelist()[0]), header=None)
        if not str(k.iloc[0, 0]).isdigit(): k = k.iloc[1:]
        ks.append(k.iloc[:, :5].astype(float))
    if not ks: return s, 'none'
    K = pd.concat(ks).drop_duplicates(0).sort_values(0)
    np.savez_compressed(out, ts=K[0].values.astype(np.int64), open=K[1].values, high=K[2].values, low=K[3].values, close=K[4].values)
    return s, len(K)
with ThreadPoolExecutor(8) as ex:
    for r in ex.map(one, syms): print(r, flush=True)
print('DONE')
