"""Causal V7 research engine. Original implementations; no Pine interpreter.
All events carry known_at: historical pivot time is never its trade time.
Chan structures use the versioned CX-73 rules and separate price time from confirmation time.
"""
from __future__ import annotations
import numpy as np

STRATEGIES = {
 'sweep_reversal':'流动性清扫收回', 'structure_retest':'结构突破回踩',
 'fvg_continuation':'缺口顺势延续', 'donchian_retest':'唐奇安突破回踩',
 'squeeze_breakout':'压缩释放突破', 'ema_pullback':'均线趋势回踩',
 'vwap_reclaim':'VWAP 收复', 'vwap_revert':'VWAP 均值回归',
 'supertrend_structure':'超级趋势与结构', 'ssl_channel':'SSL 通道交叉',
 'chan_quant':'缠论严格规则买卖点', 'liquidity_fvg_ob':'清扫缺口订单块共振',
}

def ema(x,n):
 x=np.asarray(x,float);y=np.empty(len(x));y[0]=x[0]
 for i in range(1,len(x)):y[i]=y[i-1]+2/(n+1)*(x[i]-y[i-1])
 return y

def roll(x,n,op=np.mean):
 return np.array([op(x[max(0,i-n+1):i+1]) for i in range(len(x))])

def rma(x,n):
 x=np.asarray(x,float);y=np.empty(len(x));y[0]=x[0]
 for i in range(1,len(x)):y[i]=(y[i-1]*min(i,n-1)+x[i])/min(i+1,n)
 return y

def wma(x,n):
 return np.array([np.average(x[max(0,i-n+1):i+1],weights=np.arange(1,min(i+1,n)+1)) for i in range(len(x))])

def indicators(f):
 c,o,h,l,v=[np.asarray(f[k],float) for k in ('close','open','high','low','volume')]
 prev=np.r_[c[0],c[:-1]];tr=np.maximum(h-l,np.maximum(abs(h-prev),abs(l-prev)));atr=rma(tr,14)
 delta=c-prev;up=rma(np.maximum(delta,0),14);dn=rma(np.maximum(-delta,0),14)
 rsi=np.where(up+dn>0,100*up/np.maximum(up+dn,1e-12),50)
 ef,es,et=ema(c,9),ema(c,21),ema(c,50);mid=roll(c,20);sd=roll(c,20,np.std)
 dif=ema(c,12)-ema(c,26);dea=ema(dif,9);hist=dif-dea
 dh=h-np.r_[h[0],h[:-1]];dl=np.r_[l[0],l[:-1]]-l
 plus=100*rma(np.where((dh>dl)&(dh>0),dh,0),14)/np.maximum(atr,1e-12)
 minus=100*rma(np.where((dl>dh)&(dl>0),dl,0),14)/np.maximum(atr,1e-12)
 adx=rma(100*abs(plus-minus)/np.maximum(plus+minus,1e-12),14)
 typical=(h+l+c)/3;vw=np.cumsum(typical*v)/np.maximum(np.cumsum(v),1e-12)
 flow=typical*v;sg=np.sign(typical-np.r_[typical[0],typical[:-1]])
 pos=roll(np.where(sg>0,flow,0),14,np.sum);neg=roll(np.where(sg<0,flow,0),14,np.sum)
 mfi=np.where(pos+neg>0,100*pos/np.maximum(pos+neg,1e-12),50)
 clv=(2*c-h-l)/np.maximum(h-l,1e-12);cmf=roll(clv*v,20,np.sum)/np.maximum(roll(v,20,np.sum),1e-12)
 ap=ema(typical,10);dev=ema(abs(typical-ap),10);wt=ema((typical-ap)/np.maximum(.015*dev,1e-12),21)
 stoch=100*(rsi-roll(rsi,14,np.min))/np.maximum(roll(rsi,14,np.max)-roll(rsi,14,np.min),1e-12)
 # Supertrend with recursive final bands.
 upper=(h+l)/2+3*atr;lower=(h+l)/2-3*atr;trend=np.ones(len(c));st=np.zeros(len(c));st[0]=lower[0]
 for i in range(1,len(c)):
  upper[i]=upper[i] if upper[i]<upper[i-1] or c[i-1]>upper[i-1] else upper[i-1]
  lower[i]=lower[i] if lower[i]>lower[i-1] or c[i-1]<lower[i-1] else lower[i-1]
  trend[i]=1 if c[i]>upper[i-1] else -1 if c[i]<lower[i-1] else trend[i-1]
  st[i]=lower[i] if trend[i]>0 else upper[i]
 # VIDYA uses a causal Chande momentum coefficient.
 gains=roll(np.maximum(delta,0),9,np.sum);loss=roll(np.maximum(-delta,0),9,np.sum)
 coeff=2/15*abs(gains-loss)/np.maximum(gains+loss,1e-12);vid=c.copy()
 for i in range(1,len(c)):vid[i]=vid[i-1]+coeff[i]*(c[i]-vid[i-1])
 sslh=roll(h,10);ssll=roll(l,10);ssl=np.ones(len(c))
 for i in range(1,len(c)):ssl[i]=1 if c[i]>sslh[i] else -1 if c[i]<ssll[i] else ssl[i-1]
 return dict(ema9=ef,ema21=es,ema50=et,sma20=mid,boll_up=mid+2*sd,boll_low=mid-2*sd,
  atr=atr,rsi=rsi,macd=dif,macd_signal=dea,macd_hist=hist,adx=adx,dmi_plus=plus,dmi_minus=minus,
  supertrend=st,supertrend_side=trend,hma=wma(2*wma(c,10)-wma(c,20),4),vidya=vid,
  don_high=roll(h,20,np.max),don_low=roll(l,20,np.min),keltner_up=es+1.5*atr,keltner_low=es-1.5*atr,
  chandelier_long=roll(h,22,np.max)-3*atr,chandelier_short=roll(l,22,np.min)+3*atr,
  rsi_smooth=ema(rsi,5),wavetrend=wt,squeeze=(mid+2*sd<es+1.5*atr)&(mid-2*sd>es-1.5*atr),
  stoch_rsi=stoch,vwap=vw,mfi=mfi,cmf=cmf,obv=np.cumsum(np.sign(delta)*v),volume_ratio=v/np.maximum(roll(v,20),1e-12),
  ssl_side=ssl,ssl_high=sslh,ssl_low=ssll)

def pivots(f,span=2):
 h,l,ts=[np.asarray(f[k]) for k in ('high','low','ts')];out=[]
 for i in range(span,len(h)-span):
  high=h[i]>max(h[i-span:i]) and h[i]>=max(h[i+1:i+span+1])
  low=l[i]<min(l[i-span:i]) and l[i]<=min(l[i+1:i+span+1])
  if high==low:continue
  out.append(dict(i=i,t=int(ts[i]),price=float(h[i] if high else l[i]),kind='H' if high else 'L',known_at=int(ts[i+span])))
 return out

from collections import OrderedDict
import hashlib,threading
_CHAN_CACHE=OrderedDict();_CHAN_LOCK=threading.RLock()
def chan(f,ind=None,signal_level=1,pen_mode=0,macd_mode='same'):
 from alpha_v7_chan import analyze as strict_chan
 digest=hashlib.sha256()
 for key in ('ts','high','low','close'):digest.update(np.asarray(f[key],dtype=np.float64).tobytes())
 cache_key=(digest.digest(),signal_level,pen_mode,macd_mode)
 with _CHAN_LOCK:
  if cache_key in _CHAN_CACHE:
   _CHAN_CACHE.move_to_end(cache_key);return _CHAN_CACHE[cache_key]
 if ind is None:
  line=ema(f['close'],12)-ema(f['close'],26);hist=line-ema(line,9)
 else:hist=ind['macd_hist']
 result=strict_chan(f,hist,signal_level=signal_level,pen_mode=pen_mode,macd_mode=macd_mode)
 with _CHAN_LOCK:
  _CHAN_CACHE[cache_key]=result
  while len(_CHAN_CACHE)>24:_CHAN_CACHE.popitem(last=False)
 return result


def chan_options(context):
 context=context or {}
 return dict(signal_level=int(context.get('chan_level',1)),pen_mode=int(context.get('chan_pen',0)),macd_mode='abs' if int(context.get('chan_macd',1))==0 else 'same')

def analyze(f,context=None):
 full_chan=None
 if len(f['close'])>=1500:
  full_chan=chan(f,**chan_options(context))
  f={k:np.asarray(v)[-288:] for k,v in f.items()}
 c,o,h,l,v,ts=[np.asarray(f[k],float) for k in ('close','open','high','low','volume','ts')]
 if len(c)<60:raise ValueError('至少需要60根已收盘K线')
 if not all(np.isfinite(a).all() for a in (c,o,h,l,v,ts)):raise ValueError('行情含非有限值')
 ind=indicators(f);pv=pivots(f);a=float(ind['atr'][-1]);price=float(c[-1]);events=[];zones=[];overlays=[]
 def event(label,i,p,side,group='结构',known=None):
  item=dict(label=label,ts=int(ts[i]),price=float(p),side=side,known_at=int(ts[i] if known is None else known),group=group);events.append(item);return item
 last_h=last_l=None;bias=0;broken=set()
 for i in range(len(c)):
  for p in [p for p in pv if p['known_at']==ts[i]]:
   old=last_h if p['kind']=='H' else last_l
   label=p['kind'] if old is None else ('HH' if p['price']>old['price'] else 'LH') if p['kind']=='H' else ('HL' if p['price']>old['price'] else 'LL')
   event(label,p['i'],p['price'],1 if p['kind']=='L' else -1,known=p['known_at'])
   if old and abs(p['price']-old['price'])<=float(ind['atr'][i])*.15:event('等高流动性' if p['kind']=='H' else '等低流动性',p['i'],p['price'], -1 if p['kind']=='H' else 1,'流动性',p['known_at'])
   if p['kind']=='H':last_h=p
   else:last_l=p
  for p,d in ((last_h,1),(last_l,-1)):
   if not p or (p['t'],d) in broken:continue
   if d*(c[i]-p['price'])>0:
    event('CHOCH' if bias and bias!=d else 'BOS',i,p['price'],d);broken.add((p['t'],d));bias=d
    # Last opposing candle preceding displacement defines the quantitative OB.
    opposite=[j for j in range(max(0,i-10),i) if d*(c[j]-o[j])<0]
    if opposite:
     j=opposite[-1];zones.append(dict(label='订单块',low=float(l[j]),high=float(h[j]),ts=int(ts[j]),known_at=int(ts[i]),side=d,group='供需'))
   elif (d==1 and h[i]>p['price'] and c[i]<p['price']) or (d==-1 and l[i]<p['price'] and c[i]>p['price']):event('清扫收回',i,p['price'],-d,'流动性')
  if i>=2:
   if l[i]>h[i-2]:zones.append(dict(label='FVG',low=float(h[i-2]),high=float(l[i]),ts=int(ts[i-2]),known_at=int(ts[i]),side=1,group='供需'))
   elif h[i]<l[i-2]:zones.append(dict(label='FVG',low=float(h[i]),high=float(l[i-2]),ts=int(ts[i-2]),known_at=int(ts[i]),side=-1,group='供需'))
 for z in zones:
  later=np.where(ts>z['known_at'])[0];invalid=next((i for i in later if (c[i]<z['low'] if z['side']==1 else c[i]>z['high'])),None)
  z['state']='已失效' if invalid is not None else '有效';z['to']=int(ts[invalid] if invalid is not None else ts[-1])
  if invalid is not None:event('IFVG' if z['label']=='FVG' else 'Breaker',invalid,(z['low']+z['high'])/2,-z['side'],'供需')
  touch=next((i for i in later if l[i]<=z['high'] and h[i]>=z['low']),None)
  if touch is not None:event('回补' if z['label']=='FVG' else '订单块回踩',touch,(z['low']+z['high'])/2,z['side'],'供需')
 recent=[e for e in events if e['known_at']==int(ts[-1])];values={k:float(x[-1]) for k,x in ind.items()};ch=full_chan or chan(f,ind,**chan_options(context))
 regime='趋势' if values['adx']>=25 and abs(values['ema9']-values['ema21'])>.3*a else '震荡'
 if values['squeeze']:regime='压缩'
 if any(e['label']=='BOS' for e in recent):regime='突破'
 if any(e['label']=='清扫收回' for e in recent):regime='流动性清扫'
 candidates={};up=values['ema9']>values['ema21']>values['ema50'];dn=values['ema9']<values['ema21']<values['ema50']
 def put(key,d,reason):
  if d:candidates[key]=dict(side=int(d),reason=reason,known_at=int(ts[-1]))
 sweep=next((e for e in reversed(recent) if e['label']=='清扫收回'),None)
 put('sweep_reversal',sweep['side'] if sweep else 0,'已收盘清扫前高/前低后收回')
 breaks=[e for e in events if e['label'] in ('BOS','CHOCH') and ts[max(0,len(ts)-10)]<=e['known_at']<ts[-1]]
 b=breaks[-1] if breaks else None
 ret=bool(b and l[-1]<=b['price']+.15*a and h[-1]>=b['price']-.15*a and b['side']*(c[-1]-b['price'])>0)
 put('structure_retest',b['side'] if ret else 0,'已确认结构突破后回踩站稳')
 active=[z for z in zones if z['state']=='有效' and z['known_at']<ts[-1] and l[-1]<=z['high'] and h[-1]>=z['low']]
 fg=next((z for z in reversed(active) if z['label']=='FVG' and ((z['side']==1 and up and c[-1]>z['high']) or (z['side']==-1 and dn and c[-1]<z['low']))),None)
 put('fvg_continuation',fg['side'] if fg else 0,'趋势一致，回踩已知FVG后收回')
 dh=float(np.max(h[-22:-2]));dl=float(np.min(l[-22:-2]))
 put('donchian_retest',1 if c[-2]>dh and l[-1]<=dh+.15*a and c[-1]>dh else -1 if c[-2]<dl and h[-1]>=dl-.15*a and c[-1]<dl else 0,'前根突破20根通道，本根回踩确认')
 released=any(ind['squeeze'][-6:-1]) and not ind['squeeze'][-1]
 put('squeeze_breakout',1 if released and price>values['boll_up'] else -1 if released and price<values['boll_low'] else 0,'布林进入肯特纳后释放并突破')
 put('ema_pullback',1 if up and l[-1]<=values['ema21'] and price>values['ema9'] else -1 if dn and h[-1]>=values['ema21'] and price<values['ema9'] else 0,'9/21/50均线同向并回踩收复')
 vw=ind['vwap'];put('vwap_reclaim',1 if c[-2]<=vw[-2] and price>vw[-1] else -1 if c[-2]>=vw[-2] and price<vw[-1] else 0,'收盘穿过样本锚定VWAP')
 put('vwap_revert',1 if regime=='震荡' and l[-1]<vw[-1]-2*a and price>vw[-1]-2*a else -1 if regime=='震荡' and h[-1]>vw[-1]+2*a and price<vw[-1]+2*a else 0,'震荡行情VWAP两倍ATR极值收回')
 put('supertrend_structure',1 if ind['supertrend_side'][-2]<0 and values['supertrend_side']>0 and values['adx']>=20 and last_l is not None and price>last_l['price'] else -1 if ind['supertrend_side'][-2]>0 and values['supertrend_side']<0 and values['adx']>=20 and last_h is not None and price<last_h['price'] else 0,'超级趋势翻转、ADX达到20且站在确认结构保护位有利侧')
 put('ssl_channel',1 if ind['ssl_side'][-2]<0 and values['ssl_side']>0 else -1 if ind['ssl_side'][-2]>0 and values['ssl_side']<0 else 0,'收盘穿越SSL高低均线通道')
 signals=[x for x in ch['signals'] if x['known_at']==int(ts[-1])]
 put('chan_quant',signals[-1]['side'] if signals else 0,signals[-1]['label']+'严格规则确认' if signals else '')
 if signals:candidates['chan_quant']['chan_signal']=signals[-1]
 sweeps=[e for e in events if e['label']=='清扫收回' and e['known_at']>=ts[-6]]
 ob=next((z for z in active if z['label']=='订单块' and fg and z['side']==fg['side']),None)
 put('liquidity_fvg_ob',fg['side'] if fg and ob and any(e['side']==fg['side'] for e in sweeps) else 0,'近期清扫、FVG与订单块回踩同向')
 for e in events[-100:]:overlays.append(dict(type='point',**e))
 for z in zones[-35:]:overlays.append(dict(type='zone',**z))
 for x in ch['fractals'][-40:]:overlays.append(dict(type='point',label='顶分型' if x['kind']=='H' else '底分型',group='缠论',ts=x['t'],price=x['price'],known_at=x['known_at'],state='已确认'))
 for s in ch['strokes'][-40:]:overlays.append(dict(type='line',label='笔' if s['confirmed'] else '形成中笔',group='缠论',ts=s['a']['t'],price=s['a']['price'],to=s['b']['t'],end_price=s['b']['price'],known_at=s['known_at'],state='已确认' if s['confirmed'] else '形成中'))
 for s in ch['segments'][-15:]:overlays.append(dict(type='line',label='特征序列线段',group='缠论',ts=s['a']['t'],price=s['a']['price'],to=s['b']['t'],end_price=s['b']['price'],known_at=s['known_at']))
 if len(ch['levels'])>0 and ch['levels'][0].get('forming_next'):
  s=ch['levels'][0]['forming_next'];overlays.append(dict(type='line',label='形成中线段',group='缠论',ts=s['a']['t'],price=s['a']['price'],to=s['b']['t'],end_price=s['b']['price'],known_at=s['known_at'],state='形成中'))
 for z in ch['zones'][-15:]:overlays.append(dict(type='zone',label=f"L{z.get('level',0)}中枢"+('延伸' if z['extended'] else ''),group='缠论',**z))
 for s in ch['signals'][-20:]:overlays.append(dict(type='point',group='缠论',**{**s,**ch.get('signal_status',{}).get(s['id'],{})}))
 names={0:'笔级',1:'线段级',2:'L2级',3:'L3级'}
 for s in sorted(ch.get('display_signals',[]),key=lambda x:x['ts'])[-30:]:
  overlays.append(dict(type='point',group='缠论',**{**s,**ch.get('signal_status',{}).get(s['id'],{}),'label':names.get(s['level'],'L%d级'%s['level'])+s['label'],'trade':False}))
 for s in ch['observations'][-20:]:overlays.append(dict(type='point',**s,group='缠论',state='观察（不下单）'))
 for lev in ch.get('levels',[]):
  for z in lev['expansions'][-3:]:overlays.append(dict(type='zone',group='缠论',label='中枢扩展→L'+str(z['level']),**{k:v for k,v in z.items() if k!='type'}))
 for kind in ('H','L'):
  pts=[p for p in pv if p['kind']==kind]
  if pts:
   p=pts[-1];overlays.append(dict(type='level',label='阻力' if kind=='H' else '支撑',group='结构',ts=p['t'],price=p['price']))
  if len(pts)>=2:
   x,y=pts[-2:];overlays.append(dict(type='line',label='趋势线',group='结构',ts=x['t'],price=x['price'],to=y['t'],end_price=y['price']))
 span=max(h[-50:])-min(l[-50:]);equilibrium=float((max(h[-50:])+min(l[-50:]))/2)
 values['premium_discount']=(price-min(l[-50:]))/max(span,1e-12);values['equilibrium']=equilibrium
 for label,key in (('VWAP','vwap'),('超级趋势','supertrend')):
  # piecewise curve, downsample only rendering; signal calculation uses every bar
  for i in range(max(1,len(c)-90),len(c),3):overlays.append(dict(type='line',label=label,group='趋势',ts=int(ts[i-1]),price=float(ind[key][i-1]),to=int(ts[i]),end_price=float(ind[key][i])))
 # External structure is a separately confirmed, wider five-bar pivot scale.
 external=pivots(f,5)
 if external:
  for x in external[-12:]:overlays.append(dict(type='point',label='外部高点' if x['kind']=='H' else '外部低点',group='结构',ts=x['t'],price=x['price'],side=-1 if x['kind']=='H' else 1,known_at=x['known_at']))
  last_high=next((x for x in reversed(external) if x['kind']=='H'),None)
  last_low=next((x for x in reversed(external) if x['kind']=='L'),None)
  for x,d in ((last_high,1),(last_low,-1)):
   if x and d*(price-x['price'])>0 and d*(c[-2]-x['price'])<=0:overlays.append(dict(type='point',label='外部结构突破',group='结构',ts=int(ts[-1]),price=x['price'],side=d,known_at=int(ts[-1])))
 # Rolling regression channel is a present-time estimate, never backdated as a signal.
 x=np.arange(50);slope,intercept=np.polyfit(x,c[-50:],1);fit=intercept+slope*x;width=2*float(np.std(c[-50:]-fit))
 for offset in (-width,width):overlays.append(dict(type='line',label='回归通道',group='趋势',ts=int(ts[-50]),price=float(fit[0]+offset),to=int(ts[-1]),end_price=float(fit[-1]+offset),known_at=int(ts[-1])))
 from datetime import datetime
 from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
 try: session_timezone=ZoneInfo('America/New_York')
 except ZoneInfoNotFoundError: session_timezone=None
 sessions=[];active_session=None
 for i,t in enumerate(ts if session_timezone else []):
  local=datetime.fromtimestamp(t/1000,session_timezone);label='伦敦观察窗' if 2<=local.hour<5 else '纽约观察窗' if 7<=local.hour<10 else None
  key=(local.date().isoformat(),label)
  if label and active_session and active_session['key']==key:
   active_session['to']=int(t);active_session['low']=min(active_session['low'],float(l[i]));active_session['high']=max(active_session['high'],float(h[i]))
  elif label:
   active_session=dict(key=key,type='zone',label=label,group='时段',ts=int(t),to=int(t),low=float(l[i]),high=float(h[i]));sessions.append(active_session)
  else:active_session=None
 for session in sessions[-4:]:overlays.append({k:v for k,v in session.items() if k!='key'})
 # Confirmed swing divergences and anchored VWAP start only at known pivot.
 for kind in ('H','L'):
  pp=[x for x in pv if x['kind']==kind]
  if len(pp)>=2:
   x,y=pp[-2:];d=1 if kind=='L' else -1
   for key,label in (('rsi','RSI背离'),('macd_hist','MACD背离')):
    if d*(y['price']-x['price'])<0 and d*(ind[key][y['i']]-ind[key][x['i']])>0:
     overlays.append(dict(type='point',label=label,group='动量',ts=y['t'],price=y['price'],side=d,known_at=y['known_at']))
 if pv:
  anchor=pv[-1]['i'];amount=float(np.sum(v[anchor:]));avwap=float(np.sum((h[anchor:]+l[anchor:]+c[anchor:])/3*v[anchor:])/amount) if amount>0 else price
  values['anchored_vwap']=avwap;overlays.append(dict(type='level',label='转折锚定VWAP',group='趋势',ts=pv[-1]['t'],price=avwap))
 values['atr_percent']=a/price
 values['roc_10']=(price/c[-11]-1)*100
 values['cci']=(price-float(np.mean(c[-20:])))/max(.015*float(np.mean(abs(c[-20:]-np.mean(c[-20:])))),1e-12)
 values['williams_r']=-100*(max(h[-14:])-price)/max(max(h[-14:])-min(l[-14:]),1e-12)
 values['efficiency_ratio']=abs(price-c[-21])/max(float(np.sum(abs(np.diff(c[-21:])))),1e-12)
 # Day/week levels use UTC and exclusively completed prior periods.
 for period,label in ((86400000,'前日'),(604800000,'前周')):
  offset=345600000 if label=='前周' else 0;bucket=np.floor((ts-offset)/period);prior=bucket[-1]-1;idx=np.where(bucket==prior)[0]
  bar_ms=int(ts[-1]-ts[-2]);boundary=int(prior*period+offset)
  if len(idx) and ts[idx[0]]==boundary and ts[idx[-1]]+bar_ms==boundary+period and len(idx)*bar_ms==period:
   for suffix,x in (('高点',max(h[idx])),('低点',min(l[idx]))):overlays.append(dict(type='level',label=label+suffix,group='流动性',ts=int(ts[idx[0]]),price=float(x)))
 return dict(values=values,events=events,zones=zones,chan=ch,regime=regime,candidates=candidates,overlays=overlays,
  data_status={'订单流':'本模块未接入逐笔/盘口历史，未伪造Delta、CVD或吸收信号'},as_of=int(ts[-1]))
