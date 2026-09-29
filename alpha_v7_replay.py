"""Causal bar replay. Conservative SL-first ordering; no invented intrabar path.
主级别 base_tf 全链路：rows 为 bt 主周期已收盘 K 线；高周期帧由 higher_tfs(bt) 从主周期
已收盘 K 线因果聚合（只取完整闭合的更高周期桶，禁未来函数）。"""
import numpy as np
import alpha_fast_v7 as strategy
from alpha_v7_feed import tf_ms, higher_tfs

def aggregate(arrays, target_ms, base_ms):
 # 把 base_ms 主周期已收盘 K 线聚合为 target_ms 高周期桶；仅当桶内根数齐全且首尾对齐才采纳。
 step=int(target_ms);factor=int(target_ms//base_ms);out={k:[] for k in arrays}
 groups={}
 for i,t in enumerate(arrays['ts']):groups.setdefault(int(t)//step*step,[]).append(i)
 for start,indices in groups.items():
  if len(indices)!=factor or arrays['ts'][indices[0]]!=start or arrays['ts'][indices[-1]]+base_ms!=start+step:continue
  out['ts'].append(start);out['open'].append(arrays['open'][indices[0]]);out['close'].append(arrays['close'][indices[-1]])
  out['high'].append(max(arrays['high'][indices]));out['low'].append(min(arrays['low'][indices]));out['volume'].append(sum(arrays['volume'][indices]))
 return {k:np.asarray(v) for k,v in out.items()}

def simulate(rows,params,capital,trailing=False,partial=False):
 p=strategy.validate_params(params);bt=p['base_tf'];base_ms=tf_ms(bt)
 rows=sorted({r['timestamp']:r for r in rows if r.get('confirmed',True)}.values(),key=lambda x:x['timestamp'])
 warmup=1500 if p['strategy']=='chan_quant' else 288
 if len(rows)<warmup+12:raise ValueError(f'本策略回放至少{warmup+12}根K线（{warmup}根预热）')
 if p['orderflow_mode']==2:raise ValueError('该历史只有K线，没有逐笔/盘口档案；不能回测订单流必需模式')
 if any(b['timestamp']-a['timestamp']!=base_ms for a,b in zip(rows,rows[1:])):raise ValueError(f'{bt}K线有缺口')
 arrays={k:np.array([r['timestamp' if k=='ts' else k] for r in rows]) for k in ('ts','open','high','low','close','volume')}
 # 高周期帧：仅缠论多周期需要；按主级别嵌套映射聚合已收盘 K 线。
 if p['strategy']=='chan_quant' and p['chan_mtf']:
  higher={tf:aggregate(arrays,tf_ms(tf),base_ms) for tf in higher_tfs(bt)}
 else:higher={}
 eq=float(capital);peak=eq;dd=0;pos=None;trades=[];curve=[];cost=2*(p['fee_side']+p['slip_side']);closed_trades=[]
 for i in range(warmup,len(rows)):
  r=rows[i];f={k:v[0 if p['strategy']=='chan_quant' else max(0,i-warmup):i] for k,v in arrays.items()};now=r['timestamp']/1000
  frames={bt:f}
  for tf,hf in higher.items():
   step=tf_ms(tf);mask=hf['ts']+step<=r['timestamp'];frames[tf]={k:v[mask][-1500:] for k,v in hf.items()}
  if pos is None:
   pred=strategy.decide('REPLAY',dict(frames=frames,ticker_last=r['open'],spread_bps=0,missing=[],as_of_ms=r['timestamp']),p)
   if pred['signal'] in ('LONG','SHORT'):
    fs=pred['fast_strategy'];d=1 if pred['signal']=='LONG' else -1;entry=float(r['open'])
    pos=dict(entry=entry,side='long' if d==1 else 'short',d=d,sl=fs['sl_price'],tp=fs['tp_price'],original_tp_price=fs['tp_price'],opened_at=now,base_sl_pct=pred['sl'],base_tp_pct=pred['tp'],partial_tp_steps=[],**strategy.position_meta(pred))
    pos.update(notional=min(eq,eq*.02/pred['sl']),remaining=1.,pnl=0.,native_active=False,anchor=entry)
    if trailing and (pos.get('v7_exit_config') or {}).get('runner'):pos['tp']=strategy.runner_target(pos)
  if pos:
   a=pos;d=a['d'];plan=strategy.exit_plan(a,r['open'],now,frames,trailing=False);exit_px=None;reason='';fraction=1
   effective=a['sl']
   if a.get('native_stop'):effective=max(effective,a['native_stop']) if d==1 else min(effective,a['native_stop'])
   if d*(r['open']-effective)<=0:exit_px=r['open'];reason='跳空保护退出'
   elif plan['close']:exit_px=r['open'];reason=plan['close']
   elif (r['low']<=effective if d==1 else r['high']>=effective):exit_px=effective;reason='保护止损（同根先止损）'
   # After protection, staged TP fills at their trigger prices, never at candle best price.
   if exit_px is None and partial:
    for name,progress in [('TP1',.5),('TP2',.8)]:
     pct=strategy.partial_target_pct(a,progress)
     if name in a['partial_tp_steps'] or pct>=a['base_tp_pct']:continue
     target=a['entry']*(1+d*pct)
     if (r['high']>=target if d==1 else r['low']<=target):
      frac=min(.3,a['remaining']);pnl=a['notional']*frac*(d*(target/a['entry']-1)-cost);eq+=pnl;a['pnl']+=pnl;a['remaining']-=frac;a['partial_tp_steps'].append(name)
      trades.append(dict(time=r['timestamp'],side=a['side'],entry=a['entry'],exit=target,pnl=pnl,reason=name,partial=True))
   if exit_px is None and (r['high']>=a['tp'] if d==1 else r['low']<=a['tp']):exit_px=a['tp'];reason='最终止盈'
   if exit_px is None and i==len(rows)-1:exit_px=r['close'];reason='区间结束'
   if exit_px is not None:
    pnl=a['notional']*a['remaining']*(d*(exit_px/a['entry']-1)-cost);eq+=pnl;a['pnl']+=pnl;closed_trades.append(a['pnl'])
    trades.append(dict(time=r['timestamp'],side=a['side'],entry=a['entry'],exit=exit_px,pnl=pnl,reason=reason,partial=False));pos=None
   else:
    # Only amend for the NEXT bar: no use of its high/low in earlier exits.
    nextplan=strategy.exit_plan(a,r['close'],now+300,{bt:{k:v[0 if p['strategy']=='chan_quant' else max(0,i-warmup+1):i+1] for k,v in arrays.items()}},trailing=False)
    if nextplan['stop'] is not None:a['sl']=nextplan['stop']
    if trailing:
     active,callback=strategy.native_trail_config(a)
     if (r['high']>=active if d==1 else r['low']<=active):a['native_active']=True
     if a['native_active']:
      a['anchor']=max(a['anchor'],r['high']) if d==1 else min(a['anchor'],r['low'])
      candidate=a['anchor']*(1-d*callback)
      # If candle already returned past new stop, take a conservative close approximation.
      if d*(r['close']-candidate)<=0:
       pnl=a['notional']*a['remaining']*(d*(r['close']/a['entry']-1)-cost);eq+=pnl;a['pnl']+=pnl;closed_trades.append(a['pnl'])
       trades.append(dict(time=r['timestamp'],side=a['side'],entry=a['entry'],exit=r['close'],pnl=pnl,reason='追踪触发同根回撤（收盘近似）',partial=False));pos=None
      else:a['native_stop']=candidate
  mark=eq if pos is None else eq+pos['notional']*pos['remaining']*(pos['d']*(r['close']/pos['entry']-1)-cost)
  peak=max(peak,mark);dd=max(dd,(peak-mark)/peak);curve.append(dict(time=r['timestamp'],equity=mark))
  if eq<=0:break
 wins=[x for x in closed_trades if x>0];losses=[-x for x in closed_trades if x<0]
 return dict(success=True,trades=trades,equity=curve,bars=len(rows),from_ms=rows[0]['timestamp'],to_ms=rows[-1]['timestamp'],summary={'交易数':len(closed_trades),'成交片段':len(trades),'胜率':len(wins)/len(closed_trades) if closed_trades else 0,'净收益':eq-capital,'收益率':eq/capital-1,'最大回撤':dd,'盈利因子':sum(wins)/sum(losses) if losses else None},assumptions=f'{bt}收盘信号，下一根开盘执行；2%风险/1倍名义上限；逐片段双边手续费滑点；同根先止损；分批保护下一根生效；原生追踪同根激活回撤使用收盘近似。缺少逐笔路径、标记价格、资金费、盘口与真实成交延迟，不能视为实盘验证。',warmup_bars=warmup,orderflow_history='无逐笔档案，观察模式不参与历史交易过滤',higher_history=f'主级别{bt}按 higher_tfs 因果聚合已收盘高周期K线；高周期历史长度受回测区间限制',switches={'趋势跟踪':trailing,'分批止盈':partial})
