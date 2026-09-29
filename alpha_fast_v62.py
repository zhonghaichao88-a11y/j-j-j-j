"""V6.2 可复现的价格行为候选。纯函数；无账户和网络访问。
规则为课程主题的机械化实现，不代表已学完整课程或盈利保证。
"""
from copy import deepcopy
import numpy as np
import alpha_fast_v6 as base
VERSION='6.3-ADAPTIVE-RESEARCH'
PARAMS=dict(fee_side=.0006,slip_side=.0003,min_net_rr=1.15,min_target_cost=2.,
 max_chase_r=.25,min_stop=.002,max_stop=.06,entry_set='adaptive',target_scale=1.,
 # The quality filters are deliberately parameterised so research can compare
 # fixed candidates without editing production logic between runs.
 macro_filter=False,short_macro_filter=True,long_only=False,
 long_size_multiplier=1.,short_size_multiplier=.35,
 min_hour_er=0.,min_hour_gap_atr=0.,
 breakout_body_atr=.08,breakout_volume_ratio=0.,breakout_close_location=.55,
 retest_body_atr=.05,retest_volume_ratio=0.,
 range_er_max=.20,range_min_width_atr=3.,range_max_width_atr=8.,
 range_sweep_atr=.10,range_reclaim_atr=.12,range_close_location=.64,range_volume_ratio=.8,
 range_target_fraction=.5,benchmark_trend_return=.05)
_EMPTY=base.decide('',{'frames':{}})

def closed_frames(frames,now_ms):
 out={}
 for tf,minutes in (('5m',5),('15m',15),('1h',60)):
  f=frames.get(tf) or {};ts=np.asarray(f.get('ts',[]),float);mask=ts+minutes*60000<=now_ms
  out[tf]={k:np.asarray(v)[mask] for k,v in f.items() if len(v)==len(ts)}
 return out

def pivots(f,kind):
 a=np.asarray(f[kind],float);ts=np.asarray(f['ts'],float)
 if len(a)<5:return []
 windows=np.lib.stride_tricks.sliding_window_view(a,5)
 ext=windows.min(axis=1) if kind=='low' else windows.max(axis=1)
 indices=np.flatnonzero(a[2:-2]==ext)+2;indices=indices[indices>=max(2,len(a)-55)]
 out=[]
 for j in indices:
  if not out or out[-1][1]!=a[j]:out.append((float(ts[j]),float(a[j]),int(j)))
 return out

def context(frames):
 h=frames['1h'];m=frames['15m'];highs=pivots(h,'high');lows=pivots(h,'low')
 d=0
 if len(highs)>=2 and len(lows)>=2:
  if highs[-1][1]>highs[-2][1] and lows[-1][1]>lows[-2][1]:d=1
  elif highs[-1][1]<highs[-2][1] and lows[-1][1]<lows[-2][1]:d=-1
 er=base.efficiency(np.asarray(m['close'],float),20)
 regime='多头趋势' if d==1 else ('空头趋势' if d==-1 else ('区间震荡' if er<.25 else '方向过渡'))
 return d,regime,er

def decide(symbol,data,params=None):
 p={**PARAMS,**(params or {})};out=deepcopy(_EMPTY)
 out.update(strategy_label='V6.3 全市场状态自适应裸K（研究）',version=VERSION,reason='',timeframe='全市场72h状态/1h背景/15m结构/5m确认')
 out['market_context'].update(no_trade=False,reasons=[])
 out['fast_strategy'].update(version=VERSION,engine_version='v6',v62=True,profitability='未通过独立合约盈利验证')
 out['fast_data']=dict(data.get('summary') or {})
 def wait(why):
  out['reason']='V6.3：'+why;out['entry_price_confirmation']['reason']=out['reason'];return out
 frames=data.get('frames') or {};now=data.get('as_of_ms');px=base.num(data.get('ticker_last'))
 err=base.validate_frames(frames,now)
 if err or px<=0 or data.get('missing'):return wait(err or '核心数据缺失或价格无效')
 f,m,h=(frames[k] for k in ('5m','15m','1h'));c=np.asarray(f['close'],float);hi=np.asarray(f['high'],float);lo=np.asarray(f['low'],float);op=np.asarray(f['open'],float);vol=np.asarray(f['volume'],float)
 a5,a15,a1=(base.atr(x) for x in (f,m,h))
 entry_set=p['entry_set']
 benchmark=dict(data.get('benchmark') or {})
 if entry_set=='adaptive':
  if not benchmark or int(benchmark.get('symbols') or 0)<4:return wait('全市场状态数据不足，等待至少4个币种完成72小时状态计算')
  entry_set='trend' if abs(base.num(benchmark.get('return_72h')))>=p['benchmark_trend_return'] else 'range_only'
 out['market_context']['adaptive_entry_set']=entry_set
 out['market_context']['benchmark_return_72h']=base.num(benchmark.get('return_72h'))
 trend,regime,er=context(frames);out['market_context']['regime']='V6.2/'+regime
 d=0;engine='';policy='trend';level=stop=target=0.;evidence=[]
 hc=np.asarray(h['close'],float);hh=np.asarray(h['high'],float);hl=np.asarray(h['low'],float);ho=np.asarray(h['open'],float);hv=np.asarray(h['volume'],float)
 # A simple, symmetric one-hour momentum filter. It is not a prediction score:
 # it only prevents taking a fresh break against the current hourly slope.
 fast=base.ema(hc,12);slow=base.ema(hc,26);prior=base.ema(hc[:-3],12)
 hour_er=base.efficiency(hc,20);hour_gap=abs(fast-slow)/max(a1,1e-12)
 macro=1 if fast>slow and fast>prior else (-1 if fast<slow and fast<prior else 0)
 def side_allowed(direction):
  if p['long_only'] and direction<0:return False
  if hour_er<p['min_hour_er'] or hour_gap<p['min_hour_gap_atr']:return False
  if p['macro_filter'] and macro!=direction:return False
  if p['short_macro_filter'] and direction<0 and macro!=-1:return False
  return True
 def close_location(direction,o_,h_,l_,c_):
  span=max(float(h_-l_),1e-12)
  return float((c_-l_)/span if direction==1 else (h_-c_)/span)
 ref=float(c[-1]);t5=float(f['ts'][-1]);h_end=float(h['ts'][-1])+3600000
 # Two hourly closes beyond the SAME prior box, not a changing breakout threshold.
 upper=float(max(hh[-22:-2]));lower=float(min(hl[-22:-2]));width=upper-lower
 if entry_set!='range_only' and now is not None and 0<=float(now)-h_end<300000:
  bd=1 if min(hc[-2:])>upper and all(hc[-2:]>ho[-2:]) else (-1 if max(hc[-2:])<lower and all(hc[-2:]<ho[-2:]) else 0)
  body_ok=bool(bd and min(bd*(hc[-2:]-ho[-2:]))>=p['breakout_body_atr']*a1)
  volume_ok=bool(bd and min(hv[-2:])>=p['breakout_volume_ratio']*max(float(np.median(hv[-22:-2])),1e-12))
  location_ok=bool(bd and min(close_location(bd,ho[j],hh[j],hl[j],hc[j]) for j in (-2,-1))>=p['breakout_close_location'])
  if bd and trend!=-bd and side_allowed(bd) and body_ok and volume_ok and location_ok:
   d=bd;engine='V6_BREAKOUT';level=upper if d==1 else lower
   stop=float(min(hl[-2:])-.2*a1) if d==1 else float(max(hh[-2:])+.2*a1)
   target=level+d*width*p['target_scale'];evidence=['同一小时箱体外连续两根收盘确认','目标按已形成箱体高度测量']
 if not d and entry_set not in ('breakout_only','range_only'):
  # Recent confirmed hourly breakout, followed by a separately closed 5m retest.
  upper=float(max(hh[-21:-1]));lower=float(min(hl[-21:-1]));width=upper-lower
  bd=1 if hc[-1]>upper else (-1 if hc[-1]<lower else 0)
  lv=upper if bd==1 else lower
  touch=(lo[-1]<=lv+.2*a15 and c[-1]>lv and c[-1]>op[-1]) if bd==1 else (hi[-1]>=lv-.2*a15 and c[-1]<lv and c[-1]<op[-1])
  hour_body=bd*(hc[-1]-ho[-1]) if bd else 0.
  hour_volume=float(hv[-1])/max(float(np.median(hv[-21:-1])),1e-12) if bd else 0.
  if (bd and trend!=-bd and side_allowed(bd) and t5>=h_end and abs(ref-lv)<=1.5*a15 and touch
      and hour_body>=p['retest_body_atr']*a1 and hour_volume>=p['retest_volume_ratio']):
   d=bd;engine='V6_TREND';level=lv
   stop=float(min(lo[-4:])-.2*a15) if d==1 else float(max(hi[-4:])+.2*a15)
   stop=min(stop,ref-1.1*a15) if d==1 else max(stop,ref+1.1*a15)
   target=lv+d*width*p['target_scale'];evidence=['已确认小时突破后的五分钟回踩','回踩收盘站回关键位']
 if not d and entry_set in ('context','range_only') and regime=='区间震荡' and er<=p['range_er_max']:
  rh=float(max(m['high'][-21:-1]));rl=float(min(m['low'][-21:-1]))
  bd=1 if lo[-1]<rl and ref>rl and ref>op[-1] else (-1 if hi[-1]>rh and ref<rh and ref<op[-1] else 0)
  width_atr=(rh-rl)/max(a15,1e-12)
  sweep=(rl-lo[-1])/a15 if bd==1 else ((hi[-1]-rh)/a15 if bd==-1 else 0.)
  reclaim=(ref-rl)/a15 if bd==1 else ((rh-ref)/a15 if bd==-1 else 0.)
  loc=close_location(bd,op[-1],hi[-1],lo[-1],ref) if bd else 0.
  volume_ratio=float(vol[-1])/max(float(np.median(vol[-21:-1])),1e-12) if bd else 0.
  if (bd and side_allowed(bd) and p['range_min_width_atr']<=width_atr<=p['range_max_width_atr']
      and sweep>=p['range_sweep_atr'] and reclaim>=p['range_reclaim_atr']
      and loc>=p['range_close_location'] and volume_ratio>=p['range_volume_ratio']):
   d=bd;engine='V6_RANGE';policy='range';level=rl if d==1 else rh
   stop=float(lo[-1]-.2*a15) if d==1 else float(hi[-1]+.2*a15)
   stop=min(stop,ref-1.1*a15) if d==1 else max(stop,ref+1.1*a15)
   full_target=rh-.2*a15 if d==1 else rl+.2*a15
   target=ref+p['range_target_fraction']*(full_target-ref)
   evidence=['区间外扫过后收回','目标置于区间内的可达位置']
 if not d:return wait(regime+'：等待突破延续、回踩确认或区间边界收回')
 risk=d*(ref-stop)
 if risk<=0 or abs(px-ref)>p['max_chase_r']*risk:return wait('价格已偏离结构触发位')
 spread=base.num(data.get('spread_bps'),-1)
 if spread>15:return wait('点差超出成本预算')
 micro=data.get('v6_micro') or {};funding=base.num(micro.get('funding_rate'))
 cost=2*(p['fee_side']+p['slip_side'])+(spread/10000 if spread>=0 else .0004)+max(0.,d*funding)
 sl=d*(px-stop)/px;tp=d*(target-px)/px
 if sl<p['min_stop'] or sl>p['max_stop'] or sl<2*cost:return wait('结构风险距离与成本不匹配')
 if tp<p['min_target_cost']*cost or (tp-cost)/(sl+cost)<p['min_net_rr']:return wait('真实结构目标空间不足；不人为拉远目标')
 mult=.75*(p['long_size_multiplier'] if d==1 else p['short_size_multiplier'])
 if micro.get('fresh') and micro.get('book_confirmed'):
  book=base.num(micro.get('book_imbalance'));flow=base.num(micro.get('trade_imbalance'))
  direction_size=p['long_size_multiplier'] if d==1 else p['short_size_multiplier']
  if d*book>.1 and d*flow>.1:mult=1.*direction_size
  elif d*book<-.2 and d*flow<-.2:mult=.6*direction_size
 side='LONG' if d==1 else 'SHORT';score=.60
 out.update(signal=side,tp=tp,sl=sl,base_tp=tp,base_sl=sl,horizon=16 if policy=='range' else 96,confidence=score,raw_confidence=score,signal_tier='结构候选',fast_entry_size_multiplier=mult)
 fs=out['fast_strategy'];fs.update(v62=True,v62_policy=policy,v62_entry_set=entry_set,v6_engine=engine,v4_engine=engine,path='区间边界收回' if policy=='range' else '趋势延续',tier='结构候选',evidence=evidence,trigger_evidence=evidence,
 tp_price=target,sl_price=stop,reference_price=px,rr=tp/sl,net_rr=(tp-cost)/(sl+cost),estimated_round_cost=cost,min_net_rr=p['min_net_rr'],max_chase_r=p['max_chase_r'],min_target_cost=p['min_target_cost'],entry_size_multiplier=mult,signal_id=f'{symbol}|v62|{int(t5)}|{side}',bar_ts=int(t5),invalidation_level=level,max_seconds=14400 if policy=='range' else 86400,exit_policy='v62_context',protect_at_r=1.5,target_reason='已形成结构的测量目标',score_is_probability=False)
 out['strategy_committee'].update(signal=side,committee_score=score)
 out['entry_price_confirmation'].update(enabled=True,decision='ENTER',score=score,entry_price=px,entry_size_multiplier=mult,entry_quality='结构候选')
 out['dynamic_tp_sl']=dict(tp=tp,sl=sl,reason='V6.2：测量目标与结构失效位置')
 mode_text='趋势模式' if entry_set!='range_only' else '区间模式'
 out['reason']='V6.3 '+mode_text+'；'+regime+'；'+'；'.join(evidence)+f'；扣费目标风险比{fs["net_rr"]:.2f}（不是胜率）'
 out['entry_price_confirmation']['reason']=out['reason'];return out

def position_meta(pred):
 m=base.position_meta(pred);fs=pred.get('fast_strategy') or {}
 if fs.get('v62'):m.update(v62=True,v62_policy=fs.get('v62_policy','trend'))
 return m

partial_target_pct=base.partial_target_pct
partial_floor_pct=base.partial_floor_pct

def exit_plan(position,price,now,frames=None,trailing=False):
 p=position;entry=base.num(p.get('entry'));px=base.num(price);d=1 if p.get('side')=='long' else -1
 if min(entry,px)<=0:return {'close':'','stop':None}
 risk=entry*base.num(p.get('base_sl_pct'));profit=d*(px-entry);cost=entry*base.num(p.get('v6_round_cost'),.0022)
 age=now-base.num(p.get('opened_at'),now)
 if age>=base.num(p.get('v6_max_seconds'),86400):return {'close':'V6.2持仓到期','stop':None}
 fs=closed_frames(frames or {},now*1000);f=fs.get('5m') or {};cl=f.get('close',[]);ts=f.get('ts',[]);level=base.num(p.get('v6_level'))
 if p.get('v6_engine')=='V6_BREAKOUT' and len(cl)>=2 and len(ts)>=2 and ts[-2]>=p.get('opened_at',0)*1000 and level>0:
  if all(d*(x-level)<0 for x in cl[-2:]):return {'close':'V6.2突破连续两根收回箱体','stop':None}
 if not trailing or risk<=0:return {'close':'','stop':None}
 candidates=[]
 if p.get('v62_policy')=='range':
  target=entry*base.num(p.get('base_tp_pct'))
  if profit>=.65*target and profit>=1.5*cost:candidates.append(entry+d*max(cost+.05*risk,.5*profit))
 else:
  # Do not use every 5m wiggle as a major higher-low. Confirm 15m pivot AND later extension.
  m=fs.get('15m') or {}
  if profit>=1.5*risk and len(m.get('close',[]))>=20:
   kind='low' if d==1 else 'high';ps=pivots(m,kind)
   if len(ps)>=2:
    t,v,j=ps[-1];pt,pv,pj=ps[-2]
    extreme=max(m['high'][pj:j+1]) if d==1 else min(m['low'][pj:j+1])
    if t>=p.get('opened_at',0)*1000 and d*(v-pv)>0 and d*(m['close'][-1]-extreme)>0:
     candidates.append(v-d*.2*base.atr(m))
  h=fs.get('1h') or {}
  if profit>=2*risk and len(h.get('close',[]))>=20:candidates.append(px-d*3*base.atr(h))
 if not candidates:return {'close':'','stop':None}
 stop=max(candidates) if d==1 else min(candidates);old=base.num(p.get('sl'))
 if d*(stop-old)<=0 or d*(px-stop)<=0:stop=None
 return {'close':'','stop':stop}
