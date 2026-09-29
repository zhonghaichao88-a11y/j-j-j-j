"""V7 图表研究与行情工具；所有接口不创建交易订单。"""
from __future__ import annotations
import json, math, re, threading, time, uuid
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import numpy as np
from okx_client import okx_client
import alpha_fast_v7 as strategy

router=APIRouter(prefix='/tv/api',tags=['V7 图表工具'])
TF={'1m':('1m',60000),'5m':('5m',300000),'15m':('15m',900000),'1h':('1H',3600000),'4h':('4H',14400000),'1d':('1Dutc',86400000)}
LOCK=threading.RLock(); CACHE={}; ALERT_FILE=Path(__file__).with_name('tv_alerts.json')
ALERTS={'rules':[],'events':[]}; WORKER=None
try:
    if ALERT_FILE.exists(): ALERTS.update(json.loads(ALERT_FILE.read_text(encoding='utf-8')))
except (ValueError,OSError): pass

def exchange():
    if not okx_client.is_connected: okx_client.connect()
    if okx_client._exchange is None: raise RuntimeError('行情连接尚未建立')
    return okx_client._exchange

def symbol_ok(s):
    s=str(s).upper().strip()
    if not re.fullmatch(r'[A-Z0-9]+-USDT-SWAP',s): raise ValueError('请选择USDT永续合约')
    return s

def bars(symbol,tf='5m',before=None,limit=300):
    symbol=symbol_ok(symbol)
    if tf not in TF: raise ValueError('不支持该周期')
    limit=max(1,min(int(limit),300));key=(symbol,tf,before,limit);now=time.time()
    with LOCK: hit=CACHE.get(key)
    if hit and now-hit[0]<(60 if before else 3): return hit[1]
    args={'instId':symbol,'bar':TF[tf][0],'limit':str(limit)}
    if before:args['after']=str(int(before))
    raw=exchange().request('market/history-candles' if before else 'market/candles','public','GET',args)
    if str((raw or {}).get('code'))!='0':raise RuntimeError('K线读取失败：'+str((raw or {}).get('msg','未知响应')))
    clean={}
    for r in raw.get('data',[]):
        if len(r)<6:continue
        vals=list(map(float,r[:6]));ts=int(vals[0])
        if not all(math.isfinite(x) for x in vals):continue
        if before and ts>=int(before):continue
        clean[ts]=dict(timestamp=ts,open=vals[1],high=vals[2],low=vals[3],close=vals[4],volume=vals[5],confirmed=(str(r[8])=='1' if len(r)>8 else ts+TF[tf][1]<=now*1000))
    result=sorted(clean.values(),key=lambda x:x['timestamp'])
    with LOCK:
        if len(CACHE)>400:CACHE.clear()
        CACHE[key]=(now,result)
    return result

@router.get('/bars')
def get_bars(symbol:str,tf:str='5m',before:int|None=None,limit:int=300):
    try:
        data=bars(symbol,tf,before,limit)
        return {'success':True,'bars':data,'more':len(data)>=min(limit,300),'server_ms':int(time.time()*1000)}
    except Exception as e:raise HTTPException(502,str(e))

@router.get('/markets')
def markets(basis:str='24h',sort:str='gainers',q:str='',limit:int=20):
    if basis not in ('24h','utc8','utc0'):raise HTTPException(400,'涨幅口径无效')
    key=('markets',);now=time.time()
    with LOCK:hit=CACHE.get(key)
    if hit and now-hit[0]<10:raw=hit[1];stamp=hit[0]
    else:
        try:raw=exchange().request('market/tickers','public','GET',{'instType':'SWAP'})
        except Exception as e:raise HTTPException(502,str(e))
        if str(raw.get('code'))!='0':raise HTTPException(502,'榜单行情读取失败')
        stamp=now
        with LOCK:CACHE[key]=(now,raw)
    result=[];field={'24h':'open24h','utc8':'sodUtc8','utc0':'sodUtc0'}[basis]
    for x in raw.get('data',[]):
        sym=x.get('instId','')
        if not sym.endswith('-USDT-SWAP') or q.upper().strip() not in sym:continue
        try:
            last=float(x['last']);ref=float(x.get(field) or 0)
            if last<=0 or ref<=0:continue
            # Derivatives volCcy24h is BASE currency; convert to an estimated USDT turnover.
            turnover=float(x.get('volCcy24h') or 0)*last
            result.append(dict(symbol=sym,last=last,pct=(last/ref-1)*100,turnover_estimate=turnover,reference=ref))
        except (ValueError,TypeError,KeyError):continue
    result.sort(key=lambda x:x['pct'],reverse=sort!='losers')
    return {'success':True,'rows':result[:max(1,min(limit,500))],'updated_at':stamp,'basis':basis,'note':'行情榜不做成交额筛选；自动交易仍使用原独立选币规则。成交额为币量×最新价估算。'}

class BacktestRequest(BaseModel):
    symbol:str
    count:int=Field(default=2000,ge=150,le=5000)
    params:dict=Field(default_factory=dict)
    capital:float=Field(default=10000,gt=0,le=1e9)
    trailing:bool=False
    partial:bool=False

@router.post('/backtest')
def backtest(req:BacktestRequest):
    try:
        strategy.validate_params(req.params)
        # V7.6.6 主级别随 params.base_tf（缺省 5m，保持旧行为）：分页拉取与回放都用该周期。
        bt=req.params.get('base_tf','5m')
        if bt not in ('5m','15m','1h'):raise ValueError('回测主级别仅支持5m/15m/1h')
        allrows=[];before=None
        while len(allrows)<req.count:
            page=bars(req.symbol,bt,before,min(300,req.count-len(allrows)))
            if not page:break
            oldest=page[0]['timestamp']
            if before is not None and oldest>=before:break
            allrows=page+allrows;before=oldest
        # replay 按主级别自行构建高周期帧；确保参数里带 base_tf（缺省补 5m）。
        params=dict(req.params);params.setdefault('base_tf',bt)
        from alpha_v7_replay import simulate as replay_v72
        return replay_v72(allrows,params,req.capital,req.trailing,req.partial)
    except Exception as e:raise HTTPException(400,str(e))

def persist_alerts():
    with LOCK:
        tmp=ALERT_FILE.with_suffix('.tmp');tmp.write_text(json.dumps(ALERTS,ensure_ascii=False),encoding='utf-8');tmp.replace(ALERT_FILE)

class AlertRequest(BaseModel):
    symbol:str
    kind:str='above'
    price:float=0

def check_alert(rule,last,rows=None):
    if rule['kind']=='above':return last>=rule['price']
    if rule['kind']=='below':return last<=rule['price']
    if rows and len(rows)>=60:
        c=np.asarray([r['close'] for r in rows if r.get('confirmed')]);a=strategy.ema_arr(c,9);b=strategy.ema_arr(c,21)
        return bool(strategy.cross_up(a,b) if rule['kind']=='ema_up' else strategy.cross_dn(a,b))
    return False

def alert_worker():
    while True:
        with LOCK:rules=[dict(r) for r in ALERTS['rules'] if r.get('enabled')]
        for rule in rules:
            try:
                cs=rule['symbol'].replace('-USDT-SWAP','/USDT:USDT');last=float(exchange().fetch_ticker(cs)['last'])
                rows=bars(rule['symbol'],'5m',limit=100) if rule['kind'].startswith('ema_') else None
                if check_alert(rule,last,rows):
                    with LOCK:
                        live=next((r for r in ALERTS['rules'] if r['id']==rule['id'] and r.get('enabled')),None)
                        if live:
                            live['enabled']=False
                            ALERTS['events'].append({'id':uuid.uuid4().hex,'symbol':rule['symbol'],'time':time.time(),'last':last,'kind':rule['kind']});ALERTS['events']=ALERTS['events'][-100:]
                            persist_alerts()
            except Exception as e:
                with LOCK:
                    live=next((r for r in ALERTS['rules'] if r['id']==rule['id']),None)
                    if live:live['error']=str(e)[:160]
        time.sleep(10)

def ensure_worker():
    global WORKER
    with LOCK:
        if WORKER is None or not WORKER.is_alive():
            WORKER=threading.Thread(target=alert_worker,daemon=True,name='tv-alerts');WORKER.start()

@router.get('/alerts')
def get_alerts():
    ensure_worker()
    with LOCK:return {'success':True,**json.loads(json.dumps(ALERTS))}

@router.post('/alerts')
def add_alert(req:AlertRequest):
    try:symbol=symbol_ok(req.symbol)
    except ValueError as e:raise HTTPException(400,str(e))
    if req.kind not in ('above','below','ema_up','ema_down'):raise HTTPException(400,'提醒条件无效')
    if not math.isfinite(req.price) or (req.kind in ('above','below') and req.price<=0):raise HTTPException(400,'提醒价格必须为正数')
    with LOCK:
        if len(ALERTS['rules'])>=50:raise HTTPException(400,'最多50条提醒，请先删除旧提醒')
        ALERTS['rules'].append({'id':uuid.uuid4().hex,'symbol':symbol,'kind':req.kind,'price':req.price,'enabled':True})
        persist_alerts()
    ensure_worker();return {'success':True}

@router.delete('/alerts/{rule_id}')
def delete_alert(rule_id:str):
    with LOCK:
        ALERTS['rules']=[r for r in ALERTS['rules'] if r['id']!=rule_id];persist_alerts()
    return {'success':True}

class AnalysisRequest(BaseModel):
    chan_level:int=1
    rows:list[dict]=Field(default_factory=list,max_length=1500)
    tf:str='5m'
    as_of_ms:int|None=None

def closed_frame(rows,tf='5m',as_of_ms=None):
    if tf not in TF:raise ValueError('周期无效')
    now=min(int(time.time()*1000),as_of_ms or int(time.time()*1000))
    rows=sorted({int(r['timestamp']):r for r in rows if r.get('confirmed',True) and int(r['timestamp'])+TF[tf][1]<=now}.values(),key=lambda r:r['timestamp'])
    if len(rows)<60:raise ValueError('至少需要60根已收盘K线')
    if any(b['timestamp']-a['timestamp']!=TF[tf][1] for a,b in zip(rows,rows[1:])):raise ValueError('可见K线有缺口')
    f={k:np.asarray([float(r['timestamp' if k=='ts' else k]) for r in rows]) for k in ('ts','open','high','low','close','volume')}
    if not all(np.isfinite(v).all() for v in f.values()):raise ValueError('K线包含非有限数字')
    if any(r['low']<=0 or r['low']>min(r['open'],r['close']) or r['high']<max(r['open'],r['close']) or r['volume']<0 for r in rows):raise ValueError('K线价格/成交量无效')
    return f

@router.post('/analysis')
def analysis_snapshot(req:AnalysisRequest):
    from alpha_v7_analysis import analyze
    try:
        f=closed_frame(req.rows,req.tf,req.as_of_ms)
        if req.chan_level not in range(4):raise ValueError('缠论级别须为0至3')
        result=analyze(f,{**chart_chan_context(),'chan_level':req.chan_level})
        return {'success':True,**result,'history_bars':len(f['close'])}
    except (ValueError,TypeError,KeyError) as exc:raise HTTPException(400,str(exc))


@router.get('/analysis-catalog')
def analysis_catalog():
    return {'success':True,'modules':json.loads(Path(__file__).with_name('V72_MODULES.json').read_text(encoding='utf-8'))}

class PineRequest(BaseModel):
    source:str=Field(max_length=64000)
    rows:list[dict]=Field(default_factory=list,max_length=1500)
    tf:str='5m'
    symbol:str=Field(default='',max_length=80)
    inputs:dict=Field(default_factory=dict)
    save:bool=False
    as_of_ms:int|None=None

@router.post('/pine')
def pine_import(req:PineRequest):
    from alpha_v7_pine import run,save,PineError
    try:
        frame=closed_frame(req.rows,req.tf,req.as_of_ms)
        frame={k:v[-288:] for k,v in frame.items()}
        result=run(req.source,frame,req.inputs,timeframe=req.tf,symbol=req.symbol,frames={req.tf:frame})
        if req.save:result['script_id']=save(req.source,req.inputs)
        from alpha_v7_pine import compile_source
        from pine_v5.live_contract import live_capability, json_safe
        result['live_capability']=live_capability(compile_source(req.source), req.tf)
        result['timeframe']=req.tf
        result['timestamps']=list(frame['ts'])
        return json_safe({'success':True,**result})
    except (ValueError,TypeError,KeyError,IndexError,RecursionError) as exc:raise HTTPException(400,str(exc))

@router.get('/pine/{identifier}')
def pine_source(identifier:str):
    from alpha_v7_pine import load_profile,PineError
    try:
        source,inputs=load_profile(identifier)
        return {'success':True,'source':source,'inputs':inputs}
    except (PineError,OSError) as exc:raise HTTPException(400,str(exc))

def chart_chan_context():
    # 画图与交易使用同一套笔模式/背驰面积/盘背开关。
    from alpha_fast_v7 import get_runtime_params,chan_context
    return chan_context(get_runtime_params())

@router.get('/chan-analysis')
def chan_analysis(symbol:str,tf:str='5m',level:int=1):
    from alpha_v7_feed import anchored_frame as frame
    from alpha_v7_analysis import analyze
    try:
        if level not in range(4):raise ValueError('级别必须为0至3')
        symbol=symbol_ok(symbol);cs=symbol.replace('-USDT-SWAP','/USDT:USDT')
        f=frame(exchange(),cs,tf,count=1500)
        return {'success':True,**analyze(f,{**chart_chan_context(),'chan_level':level}),'history_bars':len(f['close'])}
    except (ValueError,RuntimeError,KeyError) as exc:raise HTTPException(400,str(exc))
    except Exception as exc:raise HTTPException(502,'公开历史数据读取失败：'+str(exc))

@router.get('/orderflow')
def orderflow_snapshot(symbol:str):
    from alpha_v7_orderflow import streaming_snapshot
    try:
        symbol=symbol_ok(symbol)
        return {'success':True,**streaming_snapshot(exchange(),symbol.replace('-USDT-SWAP','/USDT:USDT'))}
    except Exception as exc:raise HTTPException(502,str(exc))
