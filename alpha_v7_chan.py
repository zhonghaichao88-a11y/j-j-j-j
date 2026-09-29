"""Versioned, causal Chan structure engine (CX-75).

Rules: direction-aware K inclusion; old (strict 5-merged-bar) or new pens; standard
characteristic sequences, both gap cases and first-pen destruction;
recursive segment levels; fixed-core centers and expansion events.
All confirmed records have independent price time and knowledge time.
Divergence is an explicit MACD-area implementation, not a theorem of profits.
Buy/sell points: 1 (trend divergence), 盘背1 (range divergence), 2, 类2, 3.
"""
from __future__ import annotations
from copy import deepcopy
import numpy as np

RULESET='CX-76-feature-first-element'

def included(a,b):
 return (a['high']>=b['high'] and a['low']<=b['low']) or (b['high']>=a['high'] and b['low']<=a['low'])

def merge_bar(a,b,direction):
 fn=max if direction>0 else min
 hi=fn(a['high'],b['high']);lo=fn(a['low'],b['low'])
 return dict(high=hi,low=lo,hi_t=a['hi_t'] if hi==a['high'] else b['hi_t'],lo_t=a['lo_t'] if lo==a['low'] else b['lo_t'],
             first=a['first'],last=b['last'],ts=b['ts'],i=a['i'])

def pens(f,mode=0):
 """Seal a pen only when the next opposite endpoint is valid. Last pen is provisional.
 mode=0 老笔：顶底分型中间K（含）之间至少5根合并K；
 mode=1 新笔：合并K跨度>=3 且两分型之间（不含）原始K线>=3根（顶底分型不共用K线）。
 """
 bars=[];fractals=[];ends=[];sealed=[];direction=1
 ts=np.asarray(f['ts']);h=np.asarray(f['high']);l=np.asarray(f['low'])
 def accept(x,known):
  if not ends:ends.append(x);return
  previous=ends[-1];d=1 if x['kind']=='H' else -1
  if x['kind']==previous['kind']:
   if d*(x['price']-previous['price'])>0:ends[-1]=x
   return
  span=x['i']-previous['i']
  if mode==1:
   if span<3 or sum(b['last']-b['first']+1 for b in bars[previous['i']+1:x['i']])<3:return
  elif span<4:return
  # No overlapping fractal centers; both high and low must be ordered.
  if d*(x['high']-previous['high'])<=0 or d*(x['low']-previous['low'])<=0:return
  between=bars[previous['i']:x['i']+1]
  if d==1 and (x['price']<max(b['high'] for b in between) or previous['price']>min(b['low'] for b in between)):return
  if d==-1 and (x['price']>min(b['low'] for b in between) or previous['price']<max(b['high'] for b in between)):return
  if len(ends)>=2:sealed.append(unit(ends[-2],previous,known,len(sealed),'pen'))
  ends.append(x)
 for k,t in enumerate(ts):
  b=dict(high=float(h[k]),low=float(l[k]),hi_t=int(t),lo_t=int(t),first=k,last=k,ts=int(t),i=len(bars))
  if bars and included(bars[-1],b):bars[-1]=merge_bar(bars[-1],b,direction);continue
  if bars:direction=1 if b['high']>bars[-1]['high'] else -1
  bars.append(b)
  # The previous right neighbor is now finalized; no inclusion can alter it.
  if len(bars)>=4:
   left,mid,right=bars[-4:-1]
   top=mid['high']>max(left['high'],right['high']) and mid['low']>max(left['low'],right['low'])
   bottom=mid['low']<min(left['low'],right['low']) and mid['high']<min(left['high'],right['high'])
   if top or bottom:
    x=dict(i=mid['i'],raw_i=mid['last'],t=mid['hi_t'] if top else mid['lo_t'],price=mid['high'] if top else mid['low'],
           kind='H' if top else 'L',high=mid['high'],low=mid['low'],known_at=int(t))
    fractals.append(x);accept(x,int(t))
 provisional=unit(ends[-2],ends[-1],int(ts[-1]),len(sealed),'pen') if len(ends)>=2 else None
 if provisional:provisional['confirmed']=False;provisional['state']='形成中'
 return dict(merged=bars,fractals=fractals,units=sealed,provisional=provisional)

def unit(a,b,known,index,kind,children=None,case=''):
 d=1 if b['price']>a['price'] else -1
 return dict(a=dict(a),b=dict(b),side=d,low=min(a['price'],b['price']),high=max(a['price'],b['price']),
             ts=a['t'],to=b['t'],known_at=int(known),index=index,kind=kind,confirmed=True,state='已确认',children=children or [],case=case)

def feature_events(units,start,direction,limit=None):
 """Online inclusion processing. Record fractals when recognized, not from final redrawn sequence.
 limit：只处理到第 limit 笔（在线处理，前面的事件与不限制时完全相同）。"""
 seq=[];events=[];movement=direction
 for j in range(start,len(units) if limit is None else min(len(units),limit+1)):
  u=units[j]
  if u['side']!=-direction:continue
  e=dict(high=u['high'],low=u['low'],start=j,end=j,peak=j,known_at=u['known_at'])
  # 分型的第一、第二元素之间不做包含处理：新元素创出线段方向的新极值时单独成为一个元素，
  # 否则真正的顶/底元素会把它左边的元素吞掉，分型永远凑不齐，线段被拖得过长。
  new_extreme=seq and (e['high']>seq[-1]['high'] if direction>0 else e['low']<seq[-1]['low'])
  if seq and included(seq[-1],e) and not new_extreme:
   old=seq[-1];fn=max if movement>0 else min
   high=fn(old['high'],e['high']);low=fn(old['low'],e['low'])
   peak=old['peak'] if (old['high']>=e['high'] if direction>0 else old['low']<=e['low']) else j
   seq[-1]=dict(high=high,low=low,start=old['start'],end=j,peak=peak,known_at=u['known_at'])
  else:
   if seq:movement=1 if e['high']>seq[-1]['high'] else -1
   seq.append(e)
  if len(seq)<3:continue
  a,b,c=seq[-3:]
  top=b['high']>max(a['high'],c['high']) and b['low']>max(a['low'],c['low'])
  bottom=b['low']<min(a['low'],c['low']) and b['high']<min(a['high'],c['high'])
  if (direction>0 and top) or (direction<0 and bottom):
   gap=a['high']<b['low'] if direction>0 else a['low']>b['high']
   ev=dict(left=dict(a),middle=dict(b),right=dict(c),gap=gap,end=b['peak'],confirm_index=j,known_at=u['known_at'])
   if not events or (events[-1]['end'],events[-1]['confirm_index'])!=(ev['end'],j):events.append(ev)
 return events

def segments(units,level=1,strict=True):
 """Characteristic-sequence segmentation, recursively reusable on confirmed lower units.
 strict=True：线段端点必须是段内极值（段内任何一笔都不能越过端点）。
 strict=False：只看特征序列分型，不要求端点是段内极值（与 chan.py 的做法一致，线段更短）。
 """
 out=[];audit=[];start=0;inverse_cache={}
 ends_px=np.array([u['b']['price'] for u in units],float)
 def beyond(lo,hi,d,pivot):
  # units[lo:hi] 里是否有笔的终点越过 pivot（d 方向）
  return hi>lo and max(d*units[k]['b']['price'] for k in range(lo,hi))>d*pivot
 while start+3<=len(units):
  d=units[start]['side'];candidates=[]
  run=np.maximum.accumulate(d*ends_px[start:])   # 从 start 起的累计极值
  def beyond_from_start(hi,pivot):return hi>start and run[hi-1-start]>d*pivot
  l71=[]
  # Lesson 71: first reversing pen destroys previous feature; later same-direction
  # reversal pen must break its endpoint before the original extreme is renewed.
  for end in range(start+3,len(units)-2,2):
   if l71 and end>min(l71)[0]:break          # 后面的确认只会更晚，不可能更早
   if units[end]['side']!=-d:continue
   left=units[end-2];first=units[end];pivot=first['a']['price']
   destroyed=first['low']<left['low'] if d>0 else first['high']>left['high']
   if not destroyed:continue
   if strict and beyond_from_start(end,pivot):continue
   m=d*units[end]['b']['price']
   for j in range(end+2,len(units),2):
    if l71 and j>min(l71)[0]:break
    m=max(m,d*units[j-1]['b']['price'],d*units[j]['b']['price'])
    if strict and m>d*pivot:break
    if -d*(units[j]['b']['price']-first['b']['price'])>0:
     if max(u['low'] for u in units[end:end+3])<=min(u['high'] for u in units[end:end+3]):l71.append((j,end,'首笔破坏后反向三笔确认'))
     break
  # Case 67: standardized opposite-direction feature sequence.
  for ev in feature_events(units,start,d,limit=min(l71)[0] if l71 else None):
   end=ev['end'];confirm=ev['confirm_index']
   if end-start<3 or (end-start)%2!=1:continue
   if not (max(u['low'] for u in units[start:start+3])<=min(u['high'] for u in units[start:start+3])):continue
   pivot=units[end]['a']['price']
   if strict and beyond_from_start(confirm+1,pivot):continue
   case='特征序列无缺口'
   if ev['gap']:
    if (end,-d) not in inverse_cache:inverse_cache[(end,-d)]=feature_events(units,end,-d)
    inverse=next((x for x in inverse_cache[(end,-d)] if x['confirm_index']>=confirm),None)
    if inverse is None:audit.append(dict(state='等待缺口第二序列确认',start=start,end=end,known_at=ev['known_at']));continue
    confirm=inverse['confirm_index'];case='缺口+反向特征分型'
    if strict and beyond(end,confirm+1,d,pivot):continue
   candidates.append((confirm,end,case))
  candidates+=l71   # 原顺序：特征序列候选在前，同一时刻同一端点时沿用特征序列的标签
  if not candidates:break
  confirm,end,case=min(candidates,key=lambda x:(x[0],x[1]))
  known=max(units[confirm]['known_at'],out[-1]['known_at'] if out else 0)
  out.append(unit(units[start]['a'],units[end-1]['b'],known,len(out),'segment',list(range(start,end)),case))
  out[-1]['level']=level;start=end
 provisional=None
 if start<len(units):
  d=units[start]['side'];ends=[j for j in range(start,len(units)) if units[j]['side']==d]
  end=max(ends,key=lambda j:d*units[j]['b']['price'])
  provisional=unit(units[start]['a'],units[end]['b'],units[-1]['known_at'],len(out),'segment',list(range(start,end+1)))
  provisional.update(level=level,confirmed=False,state='形成中')
 return out,provisional,audit

def segment_direction(units,parent,i,known):
 """Direction of the segment containing unit i, using only segments confirmed by `known`.
 Units after the last confirmed segment belong to the forming segment, whose start is already fixed."""
 start=0
 for seg in parent:
  if seg['known_at']>known:break
  if i<=seg['children'][-1]:start=seg['children'][0];break
  start=seg['children'][-1]+1
 return units[min(start,len(units)-1)]['side']

def centers(units,level,parent=None):
 """A center's initial core is immutable. Extension/expansion are timestamped events.
 parent: confirmed segments built from these units. When given, a center must start with a unit
 opposite to its segment (下跌段里的中枢是“上下上”), so the entering move is never counted inside it.
 """
 zones=[];events=[];i=0;current=None;outside=[]
 while i<len(units):
  u=units[i]
  if current is None:
   if i+2>=len(units):break
   trip=units[i:i+3];low=max(x['low'] for x in trip);high=min(x['high'] for x in trip)
   if parent is not None and trip[0]['side']==segment_direction(units,parent,i,max(x['known_at'] for x in trip)):i+=1;continue
   if low<high:
    current=dict(id=f'L{level}:{trip[0]["ts"]}:{trip[2]["to"]}',level=level,low=low,high=high,
     gg=max(x['high'] for x in trip),dd=min(x['low'] for x in trip),start=i,end=i+2,ts=trip[0]['ts'],to=trip[2]['to'],
     known_at=max(x['known_at'] for x in trip),extended=False,closed_at=None,state='已确认',snapshots=[])
    current['snapshots'].append(dict(known_at=current['known_at'],end=i+2,to=current['to'],gg=current['gg'],dd=current['dd']))
    zones.append(current);events.append(dict(type='中枢形成',zone_id=current['id'],known_at=current['known_at'],level=level));i+=3;outside=[]
   else:i+=1
   continue
  touch=u['high']>=current['low'] and u['low']<=current['high']
  if touch:
   current.update(end=i,to=u['to'],gg=max(current['gg'],u['high']),dd=min(current['dd'],u['low']),extended=True)
   current['snapshots'].append(dict(known_at=u['known_at'],end=i,to=current['to'],gg=current['gg'],dd=current['dd']))
   events.append(dict(type='中枢延伸',zone_id=current['id'],known_at=u['known_at'],level=level));outside=[];i+=1
  else:
   outside.append(i)
   # A departure and first opposite pullback staying outside seals the center.
   if len(outside)>=2:
    # The connecting departure is not part of the center's completed oscillation
    # envelope. Seal that boundary only when the first outside return is known.
    depart=outside[0]-1
    if depart>current['start']+2:
     core_units=units[current['start']:depart]
     current.update(end=depart-1,to=core_units[-1]['to'],gg=max(x['high'] for x in core_units),dd=min(x['low'] for x in core_units))
     current['snapshots'].append(dict(known_at=u['known_at'],end=current['end'],to=current['to'],gg=current['gg'],dd=current['dd']))
    current['closed_at']=u['known_at'];current['state']='已结束';current=None;i=outside[0];outside=[]
   else:i+=1
 expansions=[]
 for a,b in zip(zones,zones[1:]):
  # Use ONLY snapshots known when the new center formed, not its eventual range.
  sa=next((s for s in reversed(a['snapshots']) if s['known_at']<=b['known_at']),None)
  sb=b['snapshots'][0]
  if sa and max(sa['dd'],sb['dd'])<=min(sa['gg'],sb['gg']):
   expansions.append(dict(type='中枢扩展升级',level=level+1,children=[a['id'],b['id']],low=max(sa['dd'],sb['dd']),high=min(sa['gg'],sb['gg']),ts=a['ts'],to=sb['to'],known_at=b['known_at']))
 return zones,events,expansions

def zone_snapshot(z,known):
 snap=next((x for x in reversed(z['snapshots']) if x['known_at']<=known),None)
 if not snap:return None
 result={**z,**snap};result['snapshots']=[x for x in z['snapshots'] if x['known_at']<=known]
 if z.get('closed_at') and z['closed_at']>known:result.update(closed_at=None,state='已确认')
 result['extended']=len(result['snapshots'])>1
 return result

class LazySnapshots:
 """只在真正用到时才计算中枢在某时刻的快照（结果与逐个计算完全相同）。"""
 def __init__(self,raw,known):self.raw=raw;self.known=known;self.cache={}
 def __len__(self):return len(self.raw)
 def __bool__(self):return bool(self.raw)
 def __getitem__(self,i):
  i=i if i>=0 else len(self.raw)+i
  if i not in self.cache:self.cache[i]=zone_snapshot(self.raw[i],self.known)
  return self.cache[i]
 def __reversed__(self):
  for i in range(len(self.raw)-1,-1,-1):yield self[i]

def trade_signals(units,zones,hist,raw_ts,level=0,divergence_ratio=.9,macd_mode='same',class2_follow=2):
 """Six buy/sell point classes on confirmed units only.
 1买/1卖  (T1)  趋势背驰：两个 GG/DD 不重叠的同向中枢（第20课），离开段创新低且力度弱于进入段。
 盘背1买 (T1P) 盘整背驰：只有一个中枢或中枢重叠，离开段创新低且力度弱于进入段；单独开关，默认不下单。
 2买/2卖  (T2)  1买（含盘背1买）后第一次回抽不破 1买。
 类2买    (T2S) 2买之后 class2_follow 次以内的回抽，仍不破 1买，且仍在 2买后反弹段范围内震荡。
 3买/3卖  (T3)  离开中枢后第一次回抽不回到中枢核心。
 Range divergence that fails the entering-move test stays an observation, never a buy/sell point.
 """
 signals=[];observations=[];first={};second={};used_third=set();hist=np.asarray(hist,float)
 def power(u):
  mask=(raw_ts>=u['ts'])&(raw_ts<=u['to']);h=hist[mask]
  if macd_mode=='abs':return float(np.sum(np.abs(h)))
  # 同向面积：上涨段只计红柱，下跌段只计绿柱（第24/25课的“对应段落 MACD 面积”）。
  return float(np.sum(np.maximum(h,0))) if u['side']>0 else float(np.sum(np.maximum(-h,0)))
 def add(label,klass,kind,u,j,zone=None,extra=None):
  side=1 if label.endswith('买') else -1
  item=dict(id=f'{RULESET}|L{level}|{label}|{u["to"]}|{u["known_at"]}',label=label,bsp_class=klass,kind=kind,side=side,price=u['b']['price'],ts=u['to'],known_at=u['known_at'],state='已确认',level=level,unit_index=j,
            invalidation=u['b']['price'],zone_id=zone['id'] if zone else None,evidence=extra or {},rule=RULESET)
  signals.append(item);return item
 for j,u in enumerate(units):
  known=u['known_at'];side=-u['side']
  available=LazySnapshots([z for z in zones if z['known_at']<=known and z['start']<j],known)
  buy=side==1
  # A new extreme beyond the class-1 point ends its 2/类2 chain.
  a=first.get(side)
  if a and j>a['unit_index'] and side*(u['b']['price']-a['price'])<0:first.pop(side,None);second.pop(side,None)
  if j>=2:
   prior=units[j-2];weaker=power(u)<power(prior)*divergence_ratio;new_extreme=side*(u['b']['price']-prior['b']['price'])<0
   if weaker and new_extreme and available:
    z=available[-1];trend=False
    if len(available)>=2:
     prev=available[-2]
     trend=(z['gg']<prev['dd']) if buy else (z['dd']>prev['gg'])
    outside=u['b']['price']<z['low'] if buy else u['b']['price']>z['high']
    entry_index=z['start']-1
    entering=units[entry_index] if entry_index>=0 else None
    trend_div=bool(entering and entering['side']==u['side'] and power(u)<power(entering)*divergence_ratio)
    evidence=dict(current_power=power(u),previous_power=power(entering) if entering else None,ratio=divergence_ratio,entering_unit=entry_index,macd_mode=macd_mode)
    if trend and outside and trend_div:
     first[side]=add('1买' if buy else '1卖',1,'T1',u,j,z,{**evidence,'trend_centers':2});second.pop(side,None)
    elif outside and trend_div:
     first[side]=add('盘背1买' if buy else '盘背1卖',1,'T1P',u,j,z,{**evidence,'trend_centers':1});second.pop(side,None)
    else:observations.append(dict(label='盘整背驰观察',side=side,ts=u['to'],price=u['b']['price'],known_at=known,level=level))
  a=first.get(side)
  if a and j==a['unit_index']+2 and side*(u['b']['price']-a['price'])>0:
   second[side]=add('2买' if buy else '2卖',2,'T2',u,j,extra=dict(first_signal=a['id'],first_price=a['price'],first_kind=a['kind']))
  b=second.get(side)
  if a and b and j>b['unit_index'] and (j-b['unit_index'])%2==0 and (j-b['unit_index'])//2<=class2_follow and side*(u['b']['price']-a['price'])>0:
   rebound=units[b['unit_index']+1]
   # 仍在 2买后的反弹段范围内：回抽低点没有跑出反弹段的高点（否则已是离开，交给3买判断）。
   inside=rebound['low']<=u['b']['price']<=rebound['high']
   if inside:add('类2买' if buy else '类2卖',2,'T2S',u,j,extra=dict(first_signal=a['id'],second_signal=b['id'],first_price=a['price'],second_price=b['price']))
  # Do not use the final enlarged center envelope. Core was fixed at creation.
  if j>=1:
   departure=units[j-1]
   for z in reversed(available):
    d=departure['side'];key=(z['id'],d)
    if key in used_third or u['side']!=-d or j-1<z['start']+2:continue
    left=(departure['b']['price']>z['high'] if d==1 else departure['b']['price']<z['low'])
    originated=(departure['low']<=z['high'] and departure['high']>=z['low'])
    if not (left and originated):continue
    held=u['low']>z['high'] if d==1 else u['high']<z['low']
    if held:
     used_third.add(key)
     add('3买' if d==1 else '3卖',3,'T3',u,j,z,dict(departure=j-1,first_return=True,core=[z['low'],z['high']]))
    break
 return signals,observations

def signal_status(signals,raw_ts,close):
 """A pivot may fail while its confirming structure is still forming."""
 status={};raw_ts=np.asarray(raw_ts);close=np.asarray(close)
 for event in signals:
  crossed=np.flatnonzero((raw_ts>event['ts']) & (event['side']*(close-event['invalidation'])<0))
  invalidated=int(raw_ts[crossed[0]]) if len(crossed) else None
  status[event['id']]=dict(state='已失效' if invalidated is not None else '已确认',
   invalidated_at=invalidated,invalid_at_confirmation=invalidated is not None and invalidated<=event['known_at'])
 return status

def signal_class(event):
 """1/2/3 类；旧记录没有 bsp_class 时按标签首字推断。"""
 if event.get('bsp_class'):return int(event['bsp_class'])
 label=event['label']
 return int(next(ch for ch in label if ch in '123'))

SWITCH_SUFFIX={'T1':'1','T1P':'1p','T2':'2','T2S':'2s','T3':'3'}

def signal_switch(event):
 """页面开关名：chan_buy1/1p/2/2s/3 与 chan_sell 同理；旧记录没有 kind 时按类别推断。"""
 suffix=SWITCH_SUFFIX.get(event.get('kind')) or str(signal_class(event))
 return 'chan_'+('buy' if event['side']==1 else 'sell')+suffix

def select_signal(result,known,config,bar_ms=None):
 """Filter enabled, still-valid events before prioritizing simultaneous signals.
 chan_grace_bars>0 时，最近 N 根内确认、至今未失效的信号仍可被选中，避免漏扫一根就永久丢失。
 """
 eligible=[];grace=int(config.get('chan_grace_bars',0) or 0)
 earliest=known-grace*int(bar_ms or 0) if grace and bar_ms else known
 for event in result.get('signals',[]):
  key=signal_switch(event)
  status=result.get('signal_status',{}).get(event['id'],{})
  invalidated=status.get('invalidated_at')
  if earliest<=event['known_at']<=known and config.get(key) and (invalidated is None or invalidated>known):eligible.append(event)
 if len({e['side'] for e in eligible})>1:return None
 return max(eligible,key=lambda e:(e['known_at'],signal_class(e),e['ts'])) if eligible else None

def analyze(f,hist=None,max_level=3,signal_level=0,divergence_ratio=.9,pen_mode=0,macd_mode='same',seg_mode=0):
 n=len(f.get('close',[]))
 if n<5:return dict(fractals=[],strokes=[],segments=[],zones=[],signals=[],levels=[],observations=[],rule=RULESET)
 raw_ts=np.asarray(f['ts']);hist=np.asarray(hist if hist is not None else np.zeros(n),float)
 base=pens(f,pen_mode);units=base['units'];levels=[];all_signals=[];observations=[];all_zones=[];by_level={}
 for level in range(max_level+1):
  next_units,forming,audit=segments(units,level+1,strict=seg_mode==0) if len(units)>=3 else ([],None,[])
  zs,events,expanded=centers(units,level,next_units)
  sig,obs=trade_signals(units,zs,hist,raw_ts,level,divergence_ratio,macd_mode)
  trend='结构不足' if not zs else '盘整'
  if len(zs)>=2:
   previous,current=zs[-2:];trend='上涨' if current['dd']>previous['gg'] else '下跌' if current['gg']<previous['dd'] else '盘整/中枢扩展'
  levels.append(dict(level=level,trend=trend,units=units,centers=zs,events=events,expansions=expanded,forming_next=forming,audit=audit))
  by_level[level]=sig
  if level==signal_level:all_signals=sig;observations=obs
  all_zones.extend(zs);units=next_units
  if not units:break
 strokes=[dict(a=x['a'],b=x['b'],confirmed=True,known_at=x['known_at'],state=x['state']) for x in base['units']]
 if base['provisional']:
  x=base['provisional'];strokes.append(dict(a=x['a'],b=x['b'],confirmed=False,known_at=x['known_at'],state='形成中'))
 # 非交易级别的买卖点只用于画图（笔级1/2买等在线段级几乎不会出现），不参与 select_signal。
 display_signals=[x for lv,sig in by_level.items() if lv!=signal_level for x in sig]
 status=signal_status(all_signals+display_signals,raw_ts,f['close'])
 segment_list=levels[1]['units'] if len(levels)>1 else []
 return dict(fractals=base['fractals'],strokes=strokes,segments=segment_list,zones=all_zones,signals=all_signals,levels=levels,
             observations=observations,display_signals=display_signals,signal_status=status,rule=RULESET,signal_level=signal_level,pen_mode=pen_mode,macd_mode=macd_mode,seg_mode=seg_mode,
             states={'确认':'事件确认后冻结','形成中':'可以延伸，不能下单','失效':'由实际价格穿越信号失效位决定，不擦除历史确认事件'})
