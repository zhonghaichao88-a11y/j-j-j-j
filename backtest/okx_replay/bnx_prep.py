"""币安有、OKX 没有的 USDT 永续：从 data.binance.vision 列表拿合约，排除 OKX 上有的币（含 1000 前缀），
只要 2026-08 还有数据的（仍在交易）；下载最近 24 个月 1 小时K线（含主动买入额）→ {SYM}_bn1h.npz；列表写 universe_bnx.json。"""
import io, os, re, json, zipfile, urllib.request, ssl
import numpy as np, pandas as pd
from concurrent.futures import ThreadPoolExecutor
ctx = ssl.create_default_context(cafile='/root/.ccr/ca-bundle.crt')
opener = urllib.request.build_opener(urllib.request.ProxyHandler({'https': os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')}),
                                     urllib.request.HTTPSHandler(context=ctx))


def read(url, tries=3):
    for _ in range(tries):
        try: return opener.open(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 alpha-x-backtest'}), timeout=60).read()
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
okx = json.loads(read('https://www.okx.com/api/v5/public/instruments?instType=SWAP'))['data']
okx_base = {i['instId'].split('-')[0] for i in okx}


def base_of(s):
    b = s[:-4]
    for p in ('1000000', '10000', '1000', '100'):
        if b.startswith(p) and len(b) > len(p): return b[len(p):]
    return b


cand = sorted(s for s in set(syms) if s.endswith('USDT') and base_of(s) not in okx_base and base_of(s) not in {'BTCDOM', 'DEFI', 'USDC'})
print('币安 USDT 永续', sum(s.endswith('USDT') for s in set(syms)), '；OKX 没有的', len(cand), flush=True)
months = [(pd.Timestamp('2026-09-01') - pd.DateOffset(months=k)).strftime('%Y-%m') for k in range(24, 0, -1)]


def one(s):
    out = f'{s}_bn1h.npz'
    if os.path.exists(out): return s, 'skip'
    if read(f'https://data.binance.vision/data/futures/um/monthly/klines/{s}/1h/{s}-1h-2026-08.zip') is None: return s, 'not-live'
    ks = []
    for m in months:
        b = read(f'https://data.binance.vision/data/futures/um/monthly/klines/{s}/1h/{s}-1h-{m}.zip')
        if b is None: continue
        z = zipfile.ZipFile(io.BytesIO(b)); k = pd.read_csv(z.open(z.namelist()[0]), header=None)
        if not str(k.iloc[0, 0]).isdigit(): k = k.iloc[1:]
        ks.append(k.iloc[:, [0, 1, 2, 3, 4, 7, 10]].astype(float))
    if not ks: return s, 'none'
    K = pd.concat(ks).drop_duplicates(0).sort_values(0)
    np.savez_compressed(out, ts=K[0].values.astype(np.int64), open=K[1].values, high=K[2].values, low=K[3].values, close=K[4].values,
                        volume=K[7].values, tbuy=K[10].values)
    return s, f'bars={len(K)}'


with ThreadPoolExecutor(6) as ex: res = list(ex.map(one, cand))
live = [s for s, m in res if m.startswith('bars') or m == 'skip']
json.dump(live, open('universe_bnx.json', 'w'))
print('下载完成', len(live), flush=True); print('DONE', flush=True)
