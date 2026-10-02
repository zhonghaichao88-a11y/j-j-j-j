"""V7 里所有币：币安有历史数据的，下载 2024-01 ~ 2026-03 的 5 分钟K线、现货、持仓量/多空比、资金费率。"""
import sys, os, io, zipfile, time, pandas as pd, httpx
sys.argv=['x','none']
exec(open('get_more.py').read().split('for x in')[0])          # get / unzip_csv / spot / metrics 等
from concurrent.futures import ThreadPoolExecutor
v7=pd.concat([pd.read_csv(f'/home/user/ext/v7rec/recorder_data/{d}.csv',usecols=['inst']) for d in ('2026-09-30','2026-10-01')])
coins=sorted({i.split('-')[0] for i in v7.inst.unique()})
todo=[c for c in coins if not os.path.exists(f'k/{c}.parquet')]
print('V7 共',len(coins),'个币，要补',len(todo),'个',flush=True)
def klines(c):
    for sym in (f'{c}USDT',f'1000{c}USDT',f'1000000{c}USDT'):
        with ThreadPoolExecutor(9) as ex:
            rs=list(ex.map(lambda m: get(f"https://data.binance.vision/data/futures/um/monthly/klines/{sym}/5m/{sym}-5m-{m}.zip"),months))
        parts=[unzip_csv(r,KC) for r in rs if r]
        if parts:
            d=pd.concat(parts).drop(columns=['ct','n','ig']); d.to_parquet(f'k/{c}.parquet'); return sym,len(parts)
    return None,0
def slim_metrics(c):
    p=f'met/{c}.parquet'
    if os.path.exists(p):
        m=pd.read_parquet(p); keep=[x for x in ('create_time','sum_open_interest_value','count_long_short_ratio','sum_toptrader_long_short_ratio') if x in m]
        m[keep].to_parquet(p)
def one(c):
    try:
        sym,n=klines(c)
        if not sym: print(c,'币安没有合约历史',flush=True); return
        FUT[c]=sym
        a=spot(c); b=metrics(c); slim_metrics(c)
        open('syms_new.txt','a').write(f'{c} {sym}\n')
        print(c,sym,f'{n}个月',a[1],b[1],flush=True)
    except Exception as e:
        print(c,'出错',repr(e),flush=True)
todo=[c for c in todo if not os.path.exists(f'k/{c}.parquet') or c not in dict(l.split() for l in open('syms_new.txt'))]
with ThreadPoolExecutor(4) as ex:
    list(ex.map(one,todo))
print('DONE',flush=True)
