"""Download a fresh, non-overlapping OUT-OF-SAMPLE dataset: Binance USD-M perpetual
5m klines + funding rates, May+Jun 2026 (61 days), ending before the original OKX
window (starts 2026-07-20). Public data, no keys, no orders.

Output schema matches the original data_v62 so the same replay engine can run it.
PEPE/BONK/FLOKI use Binance's 1000x perpetual contract codes; prices are 1000x but
percentage changes (all the strategy uses) are identical.
"""
import io, os, sys, zipfile, urllib.request, datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd

BASE='https://data.binance.vision/data/futures/um/monthly'
MONTHS=sys.argv[2:] if len(sys.argv)>2 else ['2026-05','2026-06']
SYMS="AAVE ADA APT ARB AVAX BOME BONK BTC DOGE DOT ENA ETH FLOKI INJ JUP LINK NEAR ONDO OP ORDI PEPE PYTH RUNE SAND SEI SOL STX SUI TIA UNI WIF WLD XRP".split()
ZIP_DIR='/tmp/oos_zips'
OUT=sys.argv[1] if len(sys.argv)>1 else 'data_oos_futures'
os.makedirs(ZIP_DIR,exist_ok=True); os.makedirs(OUT,exist_ok=True)

def fsym(s): return ('1000'+s) if s in ('PEPE','BONK','FLOKI') else s

def download(kind, s, m):
    cs=fsym(s)+'USDT'
    if kind=='klines':
        url=f'{BASE}/klines/{cs}/5m/{cs}-5m-{m}.zip'; local=f'{ZIP_DIR}/{cs}__klines__{m}.zip'
    else:
        url=f'{BASE}/fundingRate/{cs}/{cs}-fundingRate-{m}.zip'; local=f'{ZIP_DIR}/{cs}__funding__{m}.zip'
    if os.path.exists(local) and os.path.getsize(local)>0: return local
    last=None
    for a in range(4):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
            data=urllib.request.urlopen(req,timeout=30).read()
            assert zipfile.is_zipfile(io.BytesIO(data))
            open(local,'wb').write(data); return local
        except Exception as e:
            last=e
    raise RuntimeError(f'download failed {url}: {last}')

jobs=[(k,s,m) for s in SYMS for m in MONTHS for k in ('klines','funding')]
results={}
with ThreadPoolExecutor(max_workers=8) as pool:
    futs={pool.submit(download,k,s,m):(k,s,m) for k,s,m in jobs}
    done=0
    for f in as_completed(futs):
        k,s,m=futs[f]; results[(k,s,m)]=f.result(); done+=1
        if done%20==0: print(f'下载 {done}/{len(jobs)}',flush=True)
print('全部 zip 下载完成',flush=True)

def read_zip(path):
    with zipfile.ZipFile(path) as z:
        name=z.namelist()[0]
        return pd.read_csv(z.open(name))

problems=[]
for s in SYMS:
    # klines: merge 2 months
    ks=[read_zip(results[('klines',s,m)]) for m in MONTHS]
    k=pd.concat(ks,ignore_index=True).drop_duplicates('open_time').sort_values('open_time')
    utc=pd.to_datetime(k.open_time,unit='ms',utc=True).dt.strftime('%Y-%m-%d %H:%M:%S')
    out=pd.DataFrame({
        'open_time_ms':k.open_time.astype('int64'),
        'open_time_utc':utc.values,
        'open':k.open,'high':k.high,'low':k.low,'close':k.close,
        'volume':k.volume,'quote_volume':k.quote_volume,'trades':k['count']})
    # continuity check: exactly 61 days, 5m spacing
    ts=out.open_time_ms.to_numpy(); gaps=int((pd.Series(ts).diff().dropna()!=300000).sum())
    if len(out)!=17568 or gaps:
        problems.append((s,len(out),gaps))
    out.to_csv(f'{OUT}/{s}USDT_5m.csv',index=False)
    # funding: merge 2 months -> ts,rate (ms)
    fs=[read_zip(results[('funding',s,m)]) for m in MONTHS]
    f=pd.concat(fs,ignore_index=True).drop_duplicates('calc_time').sort_values('calc_time')
    fo=pd.DataFrame({'ts':f.calc_time.astype('int64'),'rate':f.last_funding_rate})
    fo.to_csv(f'{OUT}/{s}_funding.csv',index=False)

print('转换完成 ->',OUT)
print('问题:', problems if problems else '无：33币各17568根(61天)、连续无缺口')
import glob
print('文件数:', len(glob.glob(OUT+'/*USDT_5m.csv')), 'klines +', len(glob.glob(OUT+'/*_funding.csv')), 'funding')
