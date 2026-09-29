"""Actual public OKX microstructure. Never infer aggressor side from candle color.
Contract sizes are converted to base units, then quote notional (linear USDT).
REST snapshots are explicitly sampled; WS-observed CVD has a session boundary.
"""
from __future__ import annotations
from collections import deque
import threading,time,math,json
_LOCK=threading.RLock();_STATES={};_WS=None

def number(x):
 try:
  x=float(x);return x if math.isfinite(x) else None
 except (ValueError,TypeError):return None

class FlowState:
 def __init__(self):
  self.trades=deque();self.seen={};self.cvd=0.;self.started=None;self.book=None;self.book_at=0.;self.walls={};self.slow={};self.slow_at=0.;self.rest_at=0.;self.source='REST抽样';self.missing=[];self.ws_at=0
  self.ws_channels={};self.last_trade_at=0.
 def reset_observation(self,source):
  self.trades.clear();self.seen.clear();self.cvd=0.;self.started=None
  self.book=None;self.book_at=0.;self.walls={};self.ws_channels={};self.ws_at=0.;self.last_trade_at=0.;self.source=source
 def trade(self,row,contract_size,now):
  info=row.get('info') or {};tid=str(row.get('id') or info.get('tradeId') or '')
  ts=number(row.get('timestamp') or info.get('ts'));price=number(row.get('price') or info.get('px'));amount=number(info.get('sz') if info.get('sz') is not None else row.get('amount'));side=row.get('side') or info.get('side')
  contract_size=number(contract_size)
  if not tid or ts is None or price is None or amount is None or contract_size is None or side not in ('buy','sell'):return False
  ts/=1000
  if not 0<=now-ts<=120 or min(price,amount,contract_size)<=0 or tid in self.seen:return False
  quote=amount*contract_size*price
  if not math.isfinite(quote):return False
  self.seen[tid]=ts;self.cvd+=(1 if side=='buy' else -1)*quote
  self.started=ts if self.started is None else min(self.started,ts);self.last_trade_at=max(self.last_trade_at,ts)
  self.trades.append(dict(id=tid,ts=ts,price=price,quote=quote,side=side))
  self.trades=deque(t for t in self.trades if now-t['ts']<=120)
  self.seen={k:v for k,v in self.seen.items() if now-v<=180}
  return True
 def depth(self,book,contract_size,now):
  try:return self._depth(book,contract_size,now)
  except (ValueError,TypeError,IndexError):
   self.book=None;self.book_at=0.;self.walls={}
   raise
 def _depth(self,book,contract_size,now):
  ts=number(book.get('timestamp'))
  contract_size=number(contract_size)
  if contract_size is None or contract_size<=0:raise ValueError('合约面值无效')
  if ts is None or ts/1000<self.book_at or not 0<=now-ts/1000<=10:raise ValueError('盘口过期或缺少交易所时间戳')
  if ts/1000==self.book_at:return False
  bids=book.get('bids') or [];asks=book.get('asks') or []
  if not bids or not asks or float(bids[0][0])>=float(asks[0][0]):raise ValueError('盘口交叉或为空')
  sides={};walls={}
  for side,levels in (('bid',bids),('ask',asks)):
   arr=[];previous=None
   for r in levels[:20]:
    px,sz=number(r[0]),number(r[1])
    if px is None or sz is None or px<=0 or sz<0:raise ValueError('盘口价量无效')
    if previous is not None and (px>=previous if side=='bid' else px<=previous):raise ValueError('盘口价位乱序或重复')
    quote=sz*contract_size*px
    if not math.isfinite(quote):raise ValueError('盘口名义金额无效')
    arr.append((px,quote));previous=px
   total=sum(v for _,v in arr);sides[side]=total;mean=total/max(len(arr),1)
   for px,v in arr:
    key=(side,px)
    if v>=3*mean and v>0:
     old=self.walls.get(key) or {};count=int(old.get('count',0))+1 if now-float(old.get('at',0))<=15 else 1
     walls[key]=dict(count=count,at=now,quote=v,first=old.get('first',now) if count>1 else now)
  self.walls=walls
  if sides['bid']+sides['ask']<=0:raise ValueError('空深度')
  bid,ask=float(bids[0][0]),float(asks[0][0]);self.book=dict(imbalance=(sides['bid']-sides['ask'])/(sides['bid']+sides['ask']),bid_quote=sides['bid'],ask_quote=sides['ask'],bid=bid,ask=ask,mid=(bid+ask)/2,spread_bps=(ask-bid)/((ask+bid)/2)*10000,levels=min(len(bids),len(asks)))
  self.book_at=ts/1000
  return True
 def snapshot(self,now):
  recent=sorted([t for t in self.trades if 0<=now-t['ts']<=30],key=lambda x:x['ts']);buy=sum(t['quote'] for t in recent if t['side']=='buy');sell=sum(t['quote'] for t in recent if t['side']=='sell');total=buy+sell
  fresh_trades=len(recent)>=5 and now-recent[-1]['ts']<=10 if recent else False
  book=self.book if 0<=now-self.book_at<=10 else None
  imb=(buy-sell)/total if fresh_trades and total else None
  move=(recent[-1]['price']/recent[0]['price']-1) if len(recent)>=2 else None
  bw=max([w['quote'] for (side,px),w in self.walls.items() if side=='bid' and w['count']>=3 and now-w.get('first',now)>=3 and now-w['at']<=10],default=0)
  aw=max([w['quote'] for (side,px),w in self.walls.items() if side=='ask' and w['count']>=3 and now-w.get('first',now)>=3 and now-w['at']<=10],default=0)
  slow=dict(self.slow) if 0<=now-self.slow_at<=90 else {'open_interest':None,'oi_change_pct':None,'funding_rate':None}
  return dict(book_imbalance=book['imbalance'] if book else None,book=book,trade_imbalance=imb,delta_usdt=buy-sell if fresh_trades else None,
   buy_usdt=buy if fresh_trades else None,sell_usdt=sell if fresh_trades else None,trade_count=len(recent),observed_cvd_usdt=self.cvd if self.started is not None else None,
   cvd_from=self.started,coverage=self.source+'；CVD仅为当前观察段，断流会重置',book_confirmed=bool(book and (bw or aw)),bid_wall_usdt=bw if book else 0,ask_wall_usdt=aw if book else 0,
   suspected_buy_absorption=bool(book and fresh_trades and imb<-.5 and move is not None and move>-.0005 and bw),
   suspected_sell_absorption=bool(book and fresh_trades and imb>.5 and move is not None and move<.0005 and aw),
   fresh=bool(book and fresh_trades),received_at=now,book_at=self.book_at,last_trade_at=self.last_trade_at,
   channel_status={c:bool(0<=now-self.ws_channels.get(c,-math.inf)<=15) for c in ('trades','books5')},missing=list(self.missing),**slow)

def snapshot(exchange,symbol,now=None):
 now=time.time() if now is None else float(now);key=(getattr(exchange,'id','okx'),symbol)
 market=exchange.market(symbol);size=number(market.get('contractSize'))
 if not market.get('linear') or market.get('quote')!='USDT' or not size or size<=0:raise ValueError('订单流仅支持明确合约面值的USDT线性合约')
 with _LOCK:
  state=_STATES.setdefault(key,FlowState())
  if now-state.rest_at<3:return state.snapshot(now)
  state.rest_at=now;state.missing=[]
  ws_active=state.source.startswith('WebSocket') and all(0<=now-state.ws_channels.get(c,-math.inf)<=15 for c in ('trades','books5'))
  if not ws_active and state.source.startswith('WebSocket'):state.reset_observation('REST抽样（WS通道过期）')
 # Reuse user's connected public client; no extra private credentials.
 try:
  if not ws_active:
   book=exchange.fetch_order_book(symbol,limit=20)
   with _LOCK:state.depth(book,size,now)
 except Exception as exc:
  with _LOCK:state.missing.append('盘口：'+str(exc))
 try:
  rows=[] if ws_active else exchange.fetch_trades(symbol,limit=100)
  with _LOCK:
   # If polling/connection was interrupted, reset the observation segment explicitly.
   if state.trades and now-max(t['ts'] for t in state.trades)>120:state.cvd=0;state.started=None;state.seen={};state.trades.clear();state.last_trade_at=0
   for row in rows:state.trade(row,size,now)
 except Exception as exc:
  with _LOCK:state.missing.append('逐笔：'+str(exc))
 if now-state.slow_at>=60:
  slow={}
  for name,method,field in [('funding_rate','fetch_funding_rate','fundingRate'),('open_interest','fetch_open_interest','openInterestAmount')]:
   try:
    r=getattr(exchange,method)(symbol);v=number(r.get(field))
    if v is None:raise ValueError('字段缺失')
    slow[name]=v
   except Exception as exc:slow[name]=None;state.missing.append(name+'：'+str(exc))
  with _LOCK:
   previous=state.slow.get('open_interest');current=slow.get('open_interest');slow['oi_change_pct']=current/previous-1 if previous and current is not None else None
   state.slow=slow;state.slow_at=now
 with _LOCK:return state.snapshot(now)

class PublicStream:
 """One public WS per client. Snapshot books5 avoids unsafe incremental-book merges."""
 def __init__(self):
  self.targets={};self.socket=None;self.thread=None;self.last_error='';self.connected=False
 def subscribe(self,inst,key,size):
  with _LOCK:
   new=inst not in self.targets
   if new and len(self.targets)>=40:raise ValueError('实时订单流最多40个合约')
   self.targets[inst]=(key,size,time.time())
   if self.thread is None or not self.thread.is_alive():
    self.thread=threading.Thread(target=self.loop,daemon=True,name='v73-public-flow');self.thread.start()
   elif new and self.socket and self.connected:self.socket.send(json.dumps({'op':'subscribe','args':[{'channel':c,'instId':inst} for c in ('trades','books5')]}))
 def reset(self):
  with _LOCK:
   self.connected=False
   for key,_,_ in self.targets.values():
    s=_STATES.get(key)
    if s and s.source.startswith('WebSocket'):
     s.reset_observation('REST抽样（WS断流后重新计数）')
 def message(self,raw,now=None):
  if raw=='pong':return
  now=time.time() if now is None else now;msg=json.loads(raw)
  if msg.get('event')=='error':raise ValueError('WS订阅失败：'+str(msg.get('msg')))
  if msg.get('event'):return
  arg=msg.get('arg') or {};inst=arg.get('instId')
  channel=arg.get('channel');rows=msg.get('data')
  if channel not in ('trades','books5') or not isinstance(rows,list) or not rows:return
  with _LOCK:
   if inst not in self.targets:return
   key,size,_=self.targets[inst];s=_STATES.setdefault(key,FlowState())
   if not s.source.startswith('WebSocket'):
    s.reset_observation('WebSocket逐笔+5档盘口')
   for row in rows:
    if channel=='trades':accepted=s.trade({'info':row},size,now);stamp=s.last_trade_at
    else:accepted=s.depth({'timestamp':row.get('ts'),'bids':row.get('bids'),'asks':row.get('asks')},size,now);stamp=s.book_at
    if accepted:s.ws_channels[channel]=stamp;s.ws_at=now
 def loop(self):
  import websocket
  while True:
   try:
    self.socket=websocket.create_connection('wss://ws.okx.com:8443/ws/v5/public',timeout=10)
    with _LOCK:
     # Stop unused subscriptions from accumulating as the scanner rotates markets.
     self.targets={k:v for k,v in self.targets.items() if time.time()-v[2]<180}
     args=[{'channel':c,'instId':inst} for inst in self.targets for c in ('trades','books5')]
     if not args:return
     self.connected=True
    self.socket.send(json.dumps({'op':'subscribe','args':args}));ping_at=0
    while True:
     try:
      raw=self.socket.recv()
      if not raw:raise ConnectionError('WS关闭')
      self.message(raw);ping_at=0
     except websocket.WebSocketTimeoutException:
      if ping_at and time.time()-ping_at>=10:raise ConnectionError('WS心跳超时')
      self.socket.send('ping');ping_at=time.time()
   except Exception as exc:self.last_error=str(exc)[:180]
   finally:
    self.reset()
    if self.socket:
     try:self.socket.close()
     except Exception:pass
   threading.Event().wait(5)

_STREAMS={}
def streaming_snapshot(exchange,symbol,now=None):
 market=exchange.market(symbol);size=number(market.get('contractSize'))
 if not market.get('linear') or market.get('quote')!='USDT' or not size or size<=0:raise ValueError('仅支持USDT线性合约')
 key=(getattr(exchange,'id','okx'),symbol)
 with _LOCK:stream=_STREAMS.setdefault(id(exchange),PublicStream())
 stream.subscribe(market['id'],key,size)
 result=snapshot(exchange,symbol,now);result['websocket_error']=stream.last_error;return result
