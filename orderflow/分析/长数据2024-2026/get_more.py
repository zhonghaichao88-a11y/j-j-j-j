"""补充数据（2024-01 ~ 2026-03）：币安现货 5 分钟K线（主动买）、合约 metrics（持仓量/多空比，5 分钟）、资金费率、Coinbase 5 分钟K线。"""
import httpx, os, io, zipfile, time, datetime as dt, pandas as pd, sys
from concurrent.futures import ThreadPoolExecutor
P=os.environ.get("HTTPS_PROXY")
FUT=dict(l.split() for l in open('syms.txt'))
months=[f"{y}-{m:02d}" for y in (2024,2025,2026) for m in range(1,13) if (y,m)<=(2026,3)]
days=[dt.date(2024,1,1)+dt.timedelta(i) for i in range((dt.date(2026,4,1)-dt.date(2024,1,1)).days)]
def get(url):
    for k in range(5):
        try:
            r=httpx.get(url,timeout=60,proxy=P)
            return r if r.status_code==200 else None
        except Exception: time.sleep(2*(k+1))
def unzip_csv(r,names=None):
    z=zipfile.ZipFile(io.BytesIO(r.content)); raw=z.read(z.namelist()[0]).decode()
    if names:
        lines=[l for l in raw.splitlines() if l and l[0].isdigit()]
        return pd.read_csv(io.StringIO('\n'.join(lines)),header=None,names=names)
    return pd.read_csv(io.StringIO(raw))
KC=['ts','open','high','low','close','volume','ct','quote_volume','n','taker_buy_volume','taker_buy_quote_volume','ig']
def spot(c):
    out=f'spot/{c}.parquet'
    if os.path.exists(out): return c,'have'
    sym=f'{c}USDT'; parts=[]
    for m in months:
        r=get(f"https://data.binance.vision/data/spot/monthly/klines/{sym}/5m/{sym}-5m-{m}.zip")
        if r: parts.append(unzip_csv(r,KC))
    if not parts: return c,'无现货'
    d=pd.concat(parts).drop(columns=['ct','n','ig'])
    d.loc[d.ts>1e14,'ts']//=1000          # 2025 年起现货时间戳是微秒
    d.to_parquet(out); return c,f'现货{len(parts)}个月'
def metrics(c):
    out=f'met/{c}.parquet'
    if os.path.exists(out): return c,'have'
    sym=FUT[c]
    with ThreadPoolExecutor(24) as ex:
        parts=[p for p in ex.map(lambda d: (lambda r: unzip_csv(r) if r else None)(get(f"https://data.binance.vision/data/futures/um/daily/metrics/{sym}/{sym}-metrics-{d:%Y-%m-%d}.zip")),days) if p is not None]
    fr=[]
    for m in months:
        r=get(f"https://data.binance.vision/data/futures/um/monthly/fundingRate/{sym}/{sym}-fundingRate-{m}.zip")
        if r: fr.append(unzip_csv(r))
    if fr: pd.concat(fr).to_parquet(f'met/{c}_funding.parquet')
    if not parts: return c,'无metrics'
    pd.concat(parts).to_parquet(out); return c,f'metrics{len(parts)}天 资金费率{len(fr)}个月'
def coinbase(c):
    out=f'cb/{c}.parquet'
    if os.path.exists(out): return c,'have'
    rows=[]; t=dt.datetime(2024,1,1); end=dt.datetime(2026,4,1)
    while t<end:
        t2=min(t+dt.timedelta(minutes=5*300),end)
        for k in range(5):
            try:
                r=httpx.get(f"https://api.exchange.coinbase.com/products/{c}-USD/candles",params={"granularity":300,"start":t.isoformat()+"Z","end":t2.isoformat()+"Z"},timeout=30,proxy=P)
                if r.status_code==200: rows+=r.json(); break
                time.sleep(1+k)
            except Exception: time.sleep(2+k)
        t=t2; time.sleep(0.15)
    d=pd.DataFrame(rows,columns=['t','low','high','open','close','volume']).drop_duplicates('t').sort_values('t')
    d['ts']=d.t*1000; d.to_parquet(out); return c,f'coinbase {len(d)}根'
for x in ('spot','met','cb'): os.makedirs(x,exist_ok=True)
what=sys.argv[1]
if what=='cb':
    for c in ('BTC','ETH','SOL'): print(*coinbase(c),flush=True)
elif what=='spot':
    with ThreadPoolExecutor(6) as ex:
        for r in ex.map(spot,list(FUT)): print(*r,flush=True)
else:
    part,n=int(sys.argv[2]),int(sys.argv[3])
    for c in list(FUT)[part::n]: print(*metrics(c),flush=True)
