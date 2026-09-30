"""更早年份：币安 U本位合约，2021-10 ~ 2024-09 的 1 小时K线（2022-01 就已有数据的 USDT 永续），→ {SYM}_bnold1h.npz；列表 universe_bnold.json。"""
import io, os, re, json, zipfile, urllib.request, ssl
import numpy as np, pandas as pd
from concurrent.futures import ThreadPoolExecutor
ctx = ssl.create_default_context(cafile='/root/.ccr/ca-bundle.crt')
opener = urllib.request.build_opener(urllib.request.ProxyHandler({'https': os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')}),
                                     urllib.request.HTTPSHandler(context=ctx))


def read(url):
    for _ in range(3):
        try: return opener.open(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=60).read()
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
        except Exception: pass
    return None


syms, marker = [], ''
while True:
    x = read('https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?prefix=data/futures/um/monthly/klines/&delimiter=/' + (f'&marker={marker}' if marker else '')).decode()
    syms += re.findall(r'<Prefix>data/futures/um/monthly/klines/([A-Z0-9]+)/</Prefix>', x)
    m = re.search(r'<NextMarker>(.*?)</NextMarker>', x)
    if not m or '<IsTruncated>true' not in x: break
    marker = m.group(1)
syms = sorted(s for s in set(syms) if s.endswith('USDT') and not s.startswith(('BTCDOM', 'DEFI', 'USDC')))
months = [m.strftime('%Y-%m') for m in pd.period_range('2021-10', '2024-09', freq='M')]


def one(s):
    out = f'{s}_bnold1h.npz'
    if os.path.exists(out): return s, 'skip'
    if read(f'https://data.binance.vision/data/futures/um/monthly/klines/{s}/1h/{s}-1h-2022-01.zip') is None: return s, 'late'
    ks = []
    for m in months:
        b = read(f'https://data.binance.vision/data/futures/um/monthly/klines/{s}/1h/{s}-1h-{m}.zip')
        if b is None: continue
        z = zipfile.ZipFile(io.BytesIO(b)); k = pd.read_csv(z.open(z.namelist()[0]), header=None)
        if not str(k.iloc[0, 0]).isdigit(): k = k.iloc[1:]
        ks.append(k.iloc[:, [0, 1, 2, 3, 4]].astype(float))
    if not ks: return s, 'none'
    K = pd.concat(ks).drop_duplicates(0).sort_values(0)
    np.savez_compressed(out, ts=K[0].values.astype(np.int64), open=K[1].values, high=K[2].values, low=K[3].values, close=K[4].values)
    return s, f'bars={len(K)}'


with ThreadPoolExecutor(8) as ex: res = list(ex.map(one, syms))
ok = [s for s, m in res if m.startswith('bars') or m == 'skip']
json.dump(ok, open('universe_bnold.json', 'w')); print('coins', len(ok)); print('DONE', flush=True)
