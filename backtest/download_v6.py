"""下载OKX公开永续5m已收盘K线和资金费历史；没有私钥，不会下单。
失败明确退出，绝不改用人工数据。第一次下载8币120天需要较长时间。
"""
import argparse, csv, datetime as dt, hashlib, json, time, urllib.request, urllib.parse
from pathlib import Path
BASE='https://www.okx.com'


def get(path,params):
    url=BASE+path+'?'+urllib.parse.urlencode(params)
    last=None
    for attempt in range(3):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'ALPHA-X-V6-research/1.0'})
            with urllib.request.urlopen(req,timeout=10) as res: body=json.load(res)
            if str(body.get('code'))!='0': raise RuntimeError(str(body.get('msg') or body))
            return body.get('data') or []
        except Exception as exc:
            last=exc
            if attempt<2: time.sleep(1+attempt)
    raise RuntimeError(f'公开行情读取失败：{last}')


def candles(inst,start,end):
    rows={}; cursor=end
    while cursor>start:
        batch=get('/api/v5/market/history-candles',{'instId':inst,'bar':'5m','limit':100,'after':cursor})
        if not batch: break
        oldest=min(int(x[0]) for x in batch)
        if oldest>=cursor: raise RuntimeError('K线分页无进展')
        for r in batch:
            ts=int(r[0])
            if start<=ts<end and len(r)>8 and str(r[8])=='1': rows[ts]=[ts]+[float(x) for x in r[1:6]]
        cursor=oldest
        if len(rows)%2000==0: print(inst,'已读取',len(rows),'根已收盘K线',flush=True)
        time.sleep(.13)
    ordered=[rows[t] for t in sorted(rows)]
    expected=(end-start)//300000
    if len(ordered)!=expected: raise RuntimeError(f'历史覆盖不足/缺口：期望{expected}根，取得{len(ordered)}根；本币不输出完整数据')
    return ordered


def funding(inst,start,end):
    rows={}; cursor=end
    while cursor>start:
        batch=get('/api/v5/public/funding-rate-history',{'instId':inst,'limit':100,'after':cursor})
        if not batch: break
        oldest=min(int(x['fundingTime']) for x in batch)
        if oldest>=cursor: raise RuntimeError('资金费分页无进展')
        for r in batch:
            ts=int(r['fundingTime']); rate=r.get('realizedRate')
            if rate in (None,''): rate=r.get('fundingRate')
            if rate in (None,''): raise RuntimeError('资金费字段缺失')
            if start<=ts<end: rows[ts]=float(rate)
        cursor=oldest; time.sleep(.13)
    # API通常限制历史覆盖；即便返回部分也不冒充完整。
    if not rows or min(rows)>start+12*3600000 or max(rows)<end-12*3600000:
        raise RuntimeError('资金费历史覆盖不足；请缩短日期，不能按0宣称已计费')
    return [[t,rows[t]] for t in sorted(rows)]


def save(path,header,rows):
    temp=path.with_suffix('.tmp')
    with temp.open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f); w.writerow(header); w.writerows(rows)
    temp.replace(path)


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--days',type=int,default=60)
    ap.add_argument('--symbols',default='BTC,ETH,SOL,XRP,WIF,SUI,SEI,PEPE')
    ap.add_argument('--out',type=Path,default=Path(__file__).parent/'v6_okx_data')
    a=ap.parse_args()
    if not 10<=a.days<=365: ap.error('days应为10至365')
    end=int(time.time()*1000)//300000*300000; start=end-a.days*86400000
    a.out.mkdir(parents=True,exist_ok=True); manifest=[]; failures=[]
    for sym in a.symbols.split(','):
        if not sym.isalnum(): raise ValueError('币种名无效')
        inst=sym+'-USDT-SWAP'
        try:
            rows=candles(inst,start,end); save(a.out/f'{sym}_5m.csv',['open_ms','open','high','low','close','vol'],rows)
            fpath=a.out/f'{sym}_funding.csv'
            try:
                fr=funding(inst,start,end); save(fpath,['ts','rate'],fr); status='已下载，需检查事件间隔'
            except Exception as exc:
                # 当前任务新输出目录；避免旧资金费误配到新K线。
                if fpath.exists(): fpath.rename(fpath.with_suffix('.stale.csv'))
                status='未完整取得:'+str(exc)
            manifest.append(dict(symbol=sym,instrument=inst,start_ms=start,end_exclusive_ms=end,
                                 candles=len(rows),funding_status=status,source='OKX公开历史接口',
                                 sha256=hashlib.sha256((a.out/f'{sym}_5m.csv').read_bytes()).hexdigest()))
            print(inst,len(rows),'根；',status,flush=True)
        except Exception as exc:
            failures.append(dict(symbol=sym,error=str(exc))); print(inst,'失败',str(exc),flush=True)
            # 一次网络超时即停止本批，不在8个币上继续长时间重试。
            if '公开行情读取失败' in str(exc): break
    (a.out/'download_manifest.json').write_text(json.dumps(dict(created_utc=dt.datetime.now(dt.timezone.utc).isoformat(),files=manifest,failed=failures),ensure_ascii=False,indent=2),encoding='utf-8')
    if failures: raise SystemExit(2)

if __name__=='__main__': main()
