"""Read-only public candle history for V7. No account/private endpoints.
Bootstraps once, updates a bounded ring; uses the same explicit cutoff for all TFs.
"""
from __future__ import annotations
import time,threading
import numpy as np
TF={'5m':('5m',300000),'15m':('15m',900000),'1h':('1H',3600000),'4h':('4H',14400000),'1d':('1Dutc',86400000)}
# 交易主级别仅开放 5m/15m/1h；4h/1d 只作为主级别嵌套出的高周期。
SUPPORTED_BASE_TFS=('5m','15m','1h')
# 主级别 -> 两个更高周期（顺序：就近高周期 -> 更远高周期）。
# 5m→15m+1h（历史现状）；15m→1h+4h；1h→4h+1d。
HIGHER_TFS={'5m':('15m','1h'),'15m':('1h','4h'),'1h':('4h','1d')}
_LOCK=threading.RLock();_CACHE={}

def tf_ms(tf):
    if tf not in TF:raise ValueError('不支持的周期：'+str(tf))
    return TF[tf][1]

def higher_tfs(base_tf):
    if base_tf not in HIGHER_TFS:raise ValueError('主级别无效（仅支持5m/15m/1h）')
    return HIGHER_TFS[base_tf]

def bundle_tfs(base_tf,multi=True):
    if base_tf not in SUPPORTED_BASE_TFS:raise ValueError('主级别仅支持5m/15m/1h')
    return (base_tf,)+HIGHER_TFS[base_tf] if multi else (base_tf,)

def frame(exchange,symbol,tf='5m',count=1500,now_ms=None):
 if tf not in TF:raise ValueError('不支持的K线周期：'+str(tf))
 now_ms=int(now_ms or time.time()*1000);bar,ms=TF[tf];key=(getattr(exchange,'id','okx'),symbol,tf)
 with _LOCK:old=_CACHE.get(key)
 if old and now_ms-old['at']<3000:return {k:v.copy() for k,v in old['frame'].items()}
 market=exchange.market(symbol);inst=market['id'];rows={}
 if old:
  f=old['frame'];rows={int(t):[float(f[k][i]) for k in ('open','high','low','close','volume')] for i,t in enumerate(f['ts'])}
 # Fresh page uses market/candles; historical pagination always moves strictly backward.
 before=None;pages=0
 while pages<max(2,int(np.ceil(count/300))+1):
  args={'instId':inst,'bar':bar,'limit':'300'}
  if before is not None:args['after']=str(before)
  raw=exchange.request('market/history-candles' if before is not None else 'market/candles','public','GET',args)
  if str((raw or {}).get('code'))!='0':raise RuntimeError('公开K线响应失败：'+str((raw or {}).get('msg','')))
  page=raw.get('data') or [];valid=[]
  for r in page:
   if len(r)<9 or str(r[8])!='1':continue
   t=int(r[0]);vals=list(map(float,r[1:6]))
   if t+ms>now_ms or not all(np.isfinite(vals)) or min(vals[:4])<=0 or vals[4]<0:continue
   if vals[1]<max(vals[0],vals[3]) or vals[2]>min(vals[0],vals[3]):continue
   rows[t]=vals;valid.append(t)
  pages+=1
  if not valid:break
  oldest=min(valid)
  latest=sorted(rows)[-count:]
  if len(latest)>=count and all(b-a==ms for a,b in zip(latest,latest[1:])):break
  if before is not None and oldest>=before:break
  before=oldest
 ordered=sorted(rows)[-count:]
 if not ordered:raise ValueError('没有已收盘K线')
 if len(ordered)>1 and any(b-a!=ms for a,b in zip(ordered,ordered[1:])):raise ValueError(tf+'历史有缺口，不能递归缠论')
 if now_ms-(ordered[-1]+ms)>ms+15000:raise ValueError(tf+'行情过期')
 f={'ts':np.asarray(ordered,dtype=np.int64)}
 for i,k in enumerate(('open','high','low','close','volume')):f[k]=np.asarray([rows[t][i] for t in ordered],float)
 with _LOCK:_CACHE[key]=dict(at=now_ms,frame=f)
 return {k:v.copy() for k,v in f.items()}

def bundle(exchange,symbol,multi=True,count=1500,now_ms=None,base_tf='5m'):
 now_ms=int(now_ms or time.time()*1000)
 # Sequential bootstrap respects exchange rate limiting; subsequent scans reuse rings.
 # 主级别 base_tf 决定拉取哪些周期：base（+嵌套高周期）。不同主级别结构不同。
 tfs=bundle_tfs(base_tf,multi)
 return {tf:anchored_frame(exchange,symbol,tf,count,now_ms) for tf in tfs}

# Keep the initial Chan anchor stable across scans/restarts. A moving left boundary
# can silently rebuild old pens and segments even if no future bar is used.
from pathlib import Path
import hashlib,os
HISTORY_ROOT=Path(__file__).with_name('v7_chan_history')
_ANCHORS={}
# V7.6.7 性能修复：锚点只增不减会让 analyze() 单次耗时随天数线性变长（历史越攒越多，
# 早晚拖慢扫描间隔）。改为“平时不裁剪、攒到上限才一次性重扎根”：
#   - 历史长度 <= REBUILD_TRIGGER 时，行为与旧版完全一致（只追加，不裁剪，笔/线段/买卖点零重画）；
#   - 超过 REBUILD_TRIGGER 才裁到 REBUILD_KEEP 根、重新定锚，仅在这一步可能引起一次性的近期结构调整；
#   - 之后又恢复"只加不裁"直到下一次触发，如此循环，单次 analyze() 耗时被限定在一个固定上限内。
# 经回测脚本验证：重扎根之间零重画，重扎根瞬间仅对少量较早信号产生一次性变化。
REBUILD_TRIGGER=3600
REBUILD_KEEP=3000
def anchored_frame(exchange,symbol,tf='5m',count=1500,now_ms=None):
 fresh=frame(exchange,symbol,tf,count,now_ms)
 identity=f'{getattr(exchange,"id","okx")}|{symbol}|{tf}';key=hashlib.sha256(identity.encode()).hexdigest();path=HISTORY_ROOT/(key+'.npz')
 with _LOCK:
  old=_ANCHORS.get(key)
  if old is None and path.exists():
   with np.load(path,allow_pickle=False) as stored:old={k:stored[k] for k in ('ts','open','high','low','close','volume')}
  if old is not None:
   common,oi,fi=np.intersect1d(old['ts'],fresh['ts'],return_indices=True)
   for field in ('open','high','low','close','volume'):
    if len(common) and not np.allclose(old[field][oi],fresh[field][fi],rtol=1e-10,atol=1e-10):raise ValueError('交易所已收盘历史发生修订；缠论暂停，需检查历史锚点，禁止静默重画')
   new=fresh['ts']>old['ts'][-1]
   if not np.any(new):_ANCHORS[key]=old;return {k:v.copy() for k,v in old.items()}
   if fresh['ts'][new][0]-old['ts'][-1]!=TF[tf][1]:raise ValueError('断线超过历史覆盖范围，缠论缺口需补齐，暂停交易')
   combined={k:np.concatenate((old[k],fresh[k][new])) for k in old}
  else:combined=fresh
  if len(combined['ts'])>50000:raise ValueError('缠论固定锚点达到50000根容量；请停止策略并归档历史后重新预热')
  if len(combined['ts'])>REBUILD_TRIGGER:
   # 一次性重扎根：只在攒够 REBUILD_TRIGGER 根时触发，平时不裁剪。
   combined={k:v[-REBUILD_KEEP:] for k,v in combined.items()}
  HISTORY_ROOT.mkdir(exist_ok=True);tmp=path.with_suffix('.tmp')
  with tmp.open('wb') as handle:np.savez_compressed(handle,**combined)
  os.replace(tmp,path);_ANCHORS[key]=combined
  return {k:v.copy() for k,v in combined.items()}
