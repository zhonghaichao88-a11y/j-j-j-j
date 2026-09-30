"""币安 U本位合约历史资金费率（按月文件），币安独有币（2024-09~2026-08）与更早年份币（2021-10~2024-09）→ bn_funding.json {SYM: [[ts, rate], ...]}"""
import io, os, json, zipfile, urllib.request, ssl
import pandas as pd
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


jobs = [(s, m) for s in json.load(open('universe_bnx.json')) for m in pd.period_range('2024-09', '2026-08', freq='M').strftime('%Y-%m')]
jobs += [(s, m) for s in json.load(open('universe_bnold.json')) for m in pd.period_range('2021-10', '2024-09', freq='M').strftime('%Y-%m')]


def one(j):
    s, m = j; b = read(f'https://data.binance.vision/data/futures/um/monthly/fundingRate/{s}/{s}-fundingRate-{m}.zip')
    if b is None: return s, []
    z = zipfile.ZipFile(io.BytesIO(b)); f = pd.read_csv(z.open(z.namelist()[0]), header=None)
    if not str(f.iloc[0, 0]).isdigit(): f = f.iloc[1:]
    return s, [[int(float(a)), float(c)] for a, c in zip(f.iloc[:, 0], f.iloc[:, 2])]


out = {}
with ThreadPoolExecutor(12) as ex:
    for s, rows in ex.map(one, jobs): out.setdefault(s, []).extend(rows)
for s in out: out[s].sort()
json.dump(out, open('bn_funding.json', 'w')); print('coins', len(out), 'rows', sum(len(v) for v in out.values())); print('DONE')
