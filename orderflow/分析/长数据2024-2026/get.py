"""币安 U 本位合约 5 分钟K线（带主动买入量），2024-01 ~ 2026-03，按月归档。"""
import httpx, os, time, io, zipfile, pandas as pd
from concurrent.futures import ThreadPoolExecutor
P=os.environ.get("HTTPS_PROXY")
coins=[f[:-8] for f in os.listdir('/home/user/ext/free/coins')]
months=[f"{y}-{m:02d}" for y in (2024,2025,2026) for m in range(1,13) if (y,m)<=(2026,3)]
cols=['ts','open','high','low','close','volume','ct','quote_volume','n','taker_buy_volume','taker_buy_quote_volume','ig']
def one(c):
    out=f'k/{c}.parquet'
    if os.path.exists(out): return c,'have'
    for sym in (f'{c}USDT',f'1000{c}USDT'):
        parts=[]
        for m in months:
            r=None
            for k in range(5):
                try:
                    r=httpx.get(f"https://data.binance.vision/data/futures/um/monthly/klines/{sym}/5m/{sym}-5m-{m}.zip",timeout=60,proxy=P); break
                except Exception:
                    time.sleep(3*(k+1))
            if r is None or r.status_code!=200: continue
            z=zipfile.ZipFile(io.BytesIO(r.content)); raw=z.read(z.namelist()[0]).decode()
            lines=[l for l in raw.splitlines() if l and l[0].isdigit()]
            parts.append(pd.read_csv(io.StringIO('\n'.join(lines)),header=None,names=cols))
        if parts:
            d=pd.concat(parts).drop(columns=['ct','n','ig'])
            d.to_parquet(out); return c,f'{sym} {len(parts)}个月'
    return c,'无'
os.makedirs('k',exist_ok=True)
with ThreadPoolExecutor(6) as ex:
    for c,s in ex.map(one,coins): print(c,s,flush=True)
