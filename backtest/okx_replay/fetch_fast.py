"""并发下载剩余文件；全局限速约 9 次/秒（OKX 历史K线接口上限 10 次/秒）。"""
import json,os,time,threading,urllib.request
from concurrent.futures import ThreadPoolExecutor
import numpy as np
BASE='https://www.okx.com/api/v5/';PLAN={'15m':(900000,180),'5m':(300000,60)}
lock=threading.Lock();last=[0.0];GAP=1/9
def get(path):
    for k in range(8):
        with lock:
            wait=last[0]+GAP-time.time()
            if wait>0:time.sleep(wait)
            last[0]=time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(BASE+path,headers={'User-Agent':'Mozilla/5.0 alpha-x-backtest'}),timeout=20) as r:d=json.load(r)
            if d.get('code')=='0':return d['data']
            time.sleep(2)
        except Exception:time.sleep(1.5*(k+1))
    raise RuntimeError('failed '+path)
now=int(time.time()*1000)
def job(inst,tf):
    ms,days=PLAN[tf];out=f'{inst}_{tf}.npz'
    if os.path.exists(out):return
    start=now-days*86400000;rows={};after=None
    while True:
        page=get(f'market/history-candles?instId={inst}&bar={tf}&limit=100'+(f'&after={after}' if after else ''))
        if not page:break
        for r in page:
            if r[8]=='1':rows[int(r[0])]=[float(x) for x in r[1:6]]
        oldest=min(int(r[0]) for r in page)
        if oldest<=start or (after and oldest>=after):break
        after=oldest
    ts=np.array(sorted(t for t in rows if t>=start),dtype=np.int64)
    if not len(ts):print(inst,tf,'EMPTY',flush=True);return
    v=np.array([rows[t] for t in ts]);tmp=out+'.part.npz'
    np.savez_compressed(tmp,ts=ts,open=v[:,0],high=v[:,1],low=v[:,2],close=v[:,3],volume=v[:,4]);os.replace(tmp,out)
    print(f'{inst} {tf} bars={len(ts)} days={len(ts)*ms/86400000:.0f} gaps={int(np.sum(np.diff(ts)!=ms))}',flush=True)
u=json.load(open('universe.json'))+json.load(open('universe_extra.json'))
jobs=[(i,tf) for i in u for tf in PLAN if not os.path.exists(f'{i}_{tf}.npz')]
print('remaining',len(jobs),flush=True)
with ThreadPoolExecutor(8) as ex:
    for f in [ex.submit(job,*j) for j in jobs]:
        try:f.result()
        except Exception as e:print('ERR',e,flush=True)
print('DONE',flush=True)
