"""下载更早的 15m 数据作为全新检验段：从现有文件的第一根往前，到 2025-09-10（留出预热）。"""
import json,os,time,threading,datetime,urllib.request
from concurrent.futures import ThreadPoolExecutor
import numpy as np
BASE='https://www.okx.com/api/v5/';MS=900000
START=int(datetime.datetime(2025,9,10).timestamp()*1000)
lock=threading.Lock();last=[0.0];GAP=1/9
def get(path):
    for k in range(8):
        with lock:
            w=last[0]+GAP-time.time()
            if w>0:time.sleep(w)
            last[0]=time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(BASE+path,headers={'User-Agent':'Mozilla/5.0 alpha-x-backtest'}),timeout=20) as r:d=json.load(r)
            if d.get('code')=='0':return d['data']
            time.sleep(2)
        except Exception:time.sleep(1.5*(k+1))
    raise RuntimeError('failed '+path)
def job(inst):
    out=f'{inst}_15m_old.npz'
    if os.path.exists(out):return
    first=int(np.load(f'{inst}_15m.npz')['ts'][0]);after=first;rows={}
    while True:
        page=get(f'market/history-candles?instId={inst}&bar=15m&limit=100&after={after}')
        if not page:break
        for r in page:
            if r[8]=='1':rows[int(r[0])]=[float(x) for x in r[1:6]]
        oldest=min(int(r[0]) for r in page)
        if oldest<=START or oldest>=after:break
        after=oldest
    ts=np.array(sorted(t for t in rows if START<=t<first),dtype=np.int64)
    if len(ts)<100:print(inst,'too short',len(ts),flush=True);return
    v=np.array([rows[t] for t in ts]);tmp=out+'.part.npz'
    np.savez_compressed(tmp,ts=ts,open=v[:,0],high=v[:,1],low=v[:,2],close=v[:,3],volume=v[:,4]);os.replace(tmp,out)
    print(f'{inst} old bars={len(ts)} from {datetime.datetime.utcfromtimestamp(ts[0]/1000):%Y-%m-%d} gaps={int(np.sum(np.diff(ts)!=MS))}',flush=True)
cats=json.load(open('categories.json'));uni=[i for i,c in cats.items() if c=='1']
with ThreadPoolExecutor(8) as ex:
    for f in [ex.submit(job,i) for i in uni]:
        try:f.result()
        except Exception as e:print('ERR',e,flush=True)
print('DONE',flush=True)
