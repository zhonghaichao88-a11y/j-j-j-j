"""V6 只读回放：合成情景/真实OKX CSV严格分开。不会导入账户或下单模块。
CLI --synthetic 用于功能模拟，不是盈利证明。真实CSV schema见download_v6.py。
所有指标由扣费净收益计算；每个时间段从10000独立开始；无参数搜索。
"""
from __future__ import annotations
import argparse, csv, hashlib, json, math, sys
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
import alpha_fast_v6 as v6

PROFILES={'BTC':('大币',60000,.0012),'ETH':('大币',3000,.0016),'SOL':('大币',150,.0023),'XRP':('大币',.6,.0020),
          'WIF':('小币',1.6,.0040),'SUI':('小币',1.1,.0032),'SEI':('小币',.3,.0042),'PEPE':('小币',.00001,.0048)}


def synthetic(symbol,days=45):
    # 标签只是价格/波动量级；不是对应币真实走势。趋势/震荡/突发跳空均包含。
    group,base,vol=PROFILES[symbol]; seed=13000+list(PROFILES).index(symbol)
    rng=np.random.default_rng(seed); n=days*288; c=np.empty(n); o=np.empty(n); h=np.empty(n); l=np.empty(n); vs=np.empty(n)
    last=base; anchor=base; regime=0
    for i in range(n):
        if i%144==0: regime=int(rng.choice([-1,0,0,1])); anchor=last
        gap=rng.normal(0,vol*2) if rng.random()<.001 else 0
        op=last*math.exp(gap); mean=(regime*vol*.18 if regime else -.06*math.log(last/anchor))
        shock=rng.normal(0,vol); impulse=rng.normal(0,vol*4) if rng.random()<.005 else 0
        cl=op*math.exp(mean+shock+impulse)
        wick=abs(rng.normal(vol*.4,vol*.2)); hi=max(op,cl)*math.exp(wick); lo=min(op,cl)*math.exp(-wick)
        o[i],c[i],h[i],l[i]=op,cl,hi,lo; vs[i]=max(.01,rng.lognormal(6,.35)*(1+abs(shock+impulse)/vol)); last=cl
    ts=1735689600000+np.arange(n,dtype=np.int64)*300000
    return pd.DataFrame(dict(open_ms=ts,open=o,high=h,low=l,close=c,vol=vs)),seed


def validate_csv(df):
    need=['open_ms','open','high','low','close','vol']
    if any(k not in df for k in need): raise ValueError('缺少字段:'+','.join(need))
    df=df[need].copy()
    if not np.isfinite(df.to_numpy(float)).all(): raise ValueError('数据包含空值/非有限值')
    if df.open_ms.duplicated().any() or not df.open_ms.is_monotonic_increasing: raise ValueError('时间戳重复/未排序')
    if (df.open_ms.astype('int64')%300000!=0).any(): raise ValueError('时间戳未对齐5分钟')
    if (df[['open','high','low','close']]<=0).any().any() or (df.vol<0).any(): raise ValueError('价格/成交量无效')
    if (df.high<df[['open','close']].max(axis=1)).any() or (df.low>df[['open','close']].min(axis=1)).any(): raise ValueError('高低价不合法')
    return df


class FrameSource:
    def __init__(self,df):
        self.raw={}; self.ends={}
        for name,mins in [('5m',5),('15m',15),('1h',60)]:
            if mins==5: d=df.rename(columns={'open_ms':'ts','vol':'volume'})
            else:
                x=df.copy(); x.index=pd.to_datetime(x.open_ms,unit='ms',utc=True)
                g=x.resample(f'{mins}min',label='left',closed='left')
                d=g.agg({'open':'first','high':'max','low':'min','close':'last','vol':'sum','open_ms':'count'})
                # 不完整高周期柱不参与决策，不能把缺口拼成完整小时。
                d=d[d.open_ms==mins//5].drop(columns='open_ms').rename(columns={'vol':'volume'})
                d['ts']=d.index.astype('int64')//10**6
            self.raw[name]={k:d[k].to_numpy(float) for k in ['ts','open','high','low','close','volume']}
            self.ends[name]=self.raw[name]['ts']+mins*60000
    def at(self,close_ms):
        frames={}
        for name in self.raw:
            n=int(np.searchsorted(self.ends[name],close_ms,side='right'))
            frames[name]={k:v[max(0,n-100):n] for k,v in self.raw[name].items()}
        return frames


def resolve_bar(p,op,hi,lo,close):
    """固定保护优先；跳空止损用更差开盘价；同柱双触发先止损。"""
    d=1 if p['side']=='long' else -1
    if (d==1 and lo<=p['sl']) or (d==-1 and hi>=p['sl']):
        return (min(op,p['sl']) if d==1 else max(op,p['sl'])),'结构止损'
    if (d==1 and hi>=p['tp']) or (d==-1 and lo<=p['tp']): return p['tp'],'目标止盈'
    return None,''


def metrics(trades,marks):
    nets=np.array([t['net_pnl'] for t in trades],float)
    gains=float(nets[nets>0].sum()); losses=float(-nets[nets<0].sum())
    a=np.array([10000.0]+marks,float); peaks=np.maximum.accumulate(a)
    return dict(trades=len(nets),win_pct=float((nets>0).mean()*100) if len(nets) else 0.0,
                payoff_ratio=float(nets[nets>0].mean()/-nets[nets<0].mean()) if gains>0 and losses>0 else None,
                profit_factor=gains/losses if losses>0 else None,
                net_pnl=float(nets.sum()),return_pct=(float(a[-1])/10000-1)*100,
                max_drawdown_pct=float(np.max((peaks-a)/peaks)*100),
                costs=float(sum(t['fees']+t['slippage']+t['funding_cost'] for t in trades)),
                mean_net_R=float(np.mean([t['net_R'] for t in trades])) if trades else 0.0)


def replay(symbol,df,start,end,slip=.0003,cost_mult=1.0,funding=None,trailing=False,partial=False,params=None,benchmark=None):
    df=validate_csv(df); src=FrameSource(df); arr={k:df[k].to_numpy(float) for k in df}
    fee=.0006*cost_mult; slip*=cost_mult; equity=10000.; position=None; pending=None; last_exit=-999; marks=[]; trades=[]; decisions=0
    missing_bars=int((np.diff(arr['open_ms'])!=300000).sum())
    if missing_bars: raise ValueError('回放区间存在缺口，拒绝跨缺口假设成交；请补齐数据')
    ft=[] if funding is None else list(funding[['ts','rate']].itertuples(index=False,name=None))
    def close_slice(p,price,fraction,why,ts):
        nonlocal equity
        qty=min(p['remaining'],fraction*p['quantity']); d=1 if p['side']=='long' else -1
        gross=d*(price-p['entry'])*qty
        fees=(p['entry']+price)*qty*fee
        slips=(p['entry']+price)*qty*slip
        net=gross-fees-slips
        equity+=net; p['realized']+=net; p['fees']+=fees; p['slippage']+=slips
        p['remaining']-=qty; p['last_exit']=price; p['last_reason']=why
        if p['remaining']<=p['quantity']*1e-8:
            trades.append(dict(symbol=symbol,engine=p['v6_engine'],side=p['side'],entry_ms=int(p['opened_at']*1000),
                exit_ms=int(ts),entry=p['entry'],exit=price,net_pnl=p['realized'],fees=p['fees'],slippage=p['slippage'],
                funding_cost=p['funding_cost'],net_R=p['realized']/max(p['initial_risk'],1e-9),reason=why))
            return True
        return False
    for i in range(max(1,start),min(end,len(df))):
        ts=arr['open_ms'][i]; op,hi,lo,cl=(arr[k][i] for k in ('open','high','low','close'))
        if pending and not position:
            pred=pending; pending=None; fs=pred['fast_strategy']; d=1 if pred['signal']=='LONG' else -1
            ref=fs['reference_price']; risk=d*(ref-fs['sl_price'])
            # 与实盘守卫一致：下一根开盘跳离触发位超过0.25R不追。
            sl=d*(op-fs['sl_price'])/op; tp=d*(fs['tp_price']-op)/op; cost=fs['estimated_round_cost']
            if risk>0 and abs(op-ref)<=fs.get('max_chase_r',.25)*risk and sl>0 and tp>=fs.get('min_target_cost',2.5)*cost and (tp-cost)/(sl+cost)>=fs.get('min_net_rr',1.1):
                budget=equity*.0025*pred['fast_entry_size_multiplier']
                notional=min(budget/sl,equity*.4); quantity=notional/op
                position=dict(**v6.position_meta(pred),side='long' if d==1 else 'short',entry=op,
                    sl=fs['sl_price'],tp=fs['tp_price'],base_sl_pct=sl,base_tp_pct=tp,opened_at=ts/1000,
                    quantity=quantity,remaining=quantity,initial_risk=quantity*op*sl,realized=0.,fees=0.,slippage=0.,funding_cost=0.,steps=[],pending_close='')
        if position:
            p=position; d=1 if p['side']=='long' else -1
            # 资金费按实际事件计入，正负均可；数据缺失由报告明确声明，不称其保守。
            for fts,rate in ft:
                if ts<=fts<ts+300000 and fts>p['opened_at']*1000:
                    fc=d*p['remaining']*op*float(rate); equity-=fc; p['realized']-=fc; p['funding_cost']+=fc
            if p.get('pending_close'):
                close_slice(p,op,1,p['pending_close'],ts); position=None; last_exit=i
            else:
                exit_px,reason=resolve_bar(p,op,hi,lo,cl)
                if exit_px is not None:
                    close_slice(p,exit_px,1,reason,ts+300000); position=None; last_exit=i
                else:
                    if partial:
                        for name,prog in [('TP1',.5),('TP2',.8)]:
                            if name in p['steps']: continue
                            pct=v6.partial_target_pct(p,prog) if hasattr(v6,'partial_target_pct') else max(.002,min(p['base_tp_pct']*prog,.25))
                            # 在收盘观察到目标才减仓，新增SL从下一柱生效；不猜柱内先后。
                            if pct<p['base_tp_pct'] and d*(cl/p['entry']-1)>=pct:
                                close_slice(p,cl,.3,'分批'+name,ts+300000); p['steps'].append(name)
                                floor_pct=v6.partial_floor_pct(p,name) if hasattr(v6,'partial_floor_pct') else (.0005 if name=='TP1' else max(.002,min(p['base_tp_pct']*.5,.25)))
                                floor=p['entry']*(1+d*floor_pct)
                                p['sl']=max(p['sl'],floor) if d==1 else min(p['sl'],floor)
                            break
                    plan=v6.exit_plan(p,cl,(ts+300000)/1000,src.at(ts+300000),trailing=trailing)
                    if plan['stop'] is not None: p['sl']=max(p['sl'],plan['stop']) if d==1 else min(p['sl'],plan['stop'])
                    if plan['close']: p['pending_close']=plan['close']
        mark=equity
        if position:
            p=position; d=1 if p['side']=='long' else -1
            mark+=d*(cl-p['entry'])*p['remaining']-(p['entry']+cl)*p['remaining']*(fee+slip)
        marks.append(mark)
        if not position and i-last_exit>=3 and i<end-1:
            data=dict(frames=src.at(ts+300000),ticker_last=cl,as_of_ms=ts+300000,spread_bps=-1,missing=[],summary={},
                      benchmark=(benchmark or {}).get(int(ts+300000),{}))
            pred=v6.decide(symbol,data,params={**(params or {}),'fee_side':fee,'slip_side':slip})
            decisions+=1
            if pred['signal'] in ('LONG','SHORT'): pending=pred
    if position:
        close_slice(position,arr['close'][end-1],1,'样本段结束强平',arr['open_ms'][end-1]+300000); marks[-1]=equity
    result=metrics(trades,marks); result.update(decisions=decisions,raw_rows=end-start,
        funding_included=funding is not None,execution='市价假设，下一根开盘；无L2，非挂单成交验证',
        trailing=trailing,partial=partial)
    return result,trades


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--synthetic',action='store_true'); ap.add_argument('--data',type=Path)
    ap.add_argument('--days',type=int,default=45); ap.add_argument('--out',type=Path,default=ROOT/'backtest'/'v6_results')
    ap.add_argument('--symbols'); ap.add_argument('--stress',type=float,default=1)
    ap.add_argument('--trailing',action='store_true'); ap.add_argument('--partial',action='store_true')
    args=ap.parse_args()
    if args.synthetic==bool(args.data): ap.error('必须且只能指定 --synthetic 或 --data 路径')
    args.out.mkdir(parents=True,exist_ok=True); rows=[]; all_trades=[]; manifest=[]; failed=[]
    symbols=args.symbols.split(',') if args.symbols else (list(PROFILES) if args.synthetic else [p.stem.removesuffix('_5m') for p in sorted(args.data.glob('*_5m.csv'))])
    if not symbols: ap.error('未找到 *_5m.csv 数据文件')
    for sym in symbols:
        try:
            if args.synthetic and sym not in PROFILES: raise ValueError('合成模拟不支持该币种')
            if args.synthetic: df,seed=synthetic(sym,args.days); digest=hashlib.sha256(df.to_csv(index=False).encode()).hexdigest(); funding=None
            else:
                path=args.data/f'{sym}_5m.csv'; df=pd.read_csv(path); seed=None; digest=hashlib.sha256(path.read_bytes()).hexdigest()
                fp=args.data/f'{sym}_funding.csv'; funding=pd.read_csv(fp) if fp.exists() else None
            df=validate_csv(df)
            # 禁止把短数据片段输出成完整回测。
            if len(df)<288*10: raise ValueError('不足10天数据，无法完成预热和分段测试')
            split=int(len(df)*.65); group='大币' if sym in ('BTC','ETH','SOL','XRP') else '其他币'; slip=.0003 if group=='大币' else .001
            manifest.append(dict(symbol=sym,rows=len(df),sha256=digest,seed=seed,first_ms=int(df.open_ms.iloc[0]),last_ms=int(df.open_ms.iloc[-1])))
            for name,a,b in [('前段',720,split),('后段',split,len(df))]:
                met,ts=replay(sym,df,a,b,slip=slip,cost_mult=args.stress,funding=funding,trailing=args.trailing,partial=args.partial)
                rows.append(dict(symbol=sym,group=group,period=name,**met))
                all_trades.extend(dict(period=name,**t) for t in ts)
                print(sym,name,'笔数',met['trades'],'净收益%',round(met['return_pct'],2),'PF',met['profit_factor'],flush=True)
        except Exception as exc: failed.append(dict(symbol=sym,error=str(exc))); print(sym,'失败',str(exc),flush=True)
    payload=dict(data_kind='合成行情功能模拟，不能证明真实盈利' if args.synthetic else '用户提供/下载的历史K线回放',
        strategy_version=v6.VERSION,params=v6.PARAMS,stress=args.stress,trailing=args.trailing,partial=args.partial,
        funding_note='无资金费文件时未计资金费；不是保守估计；合成场景不代表真实市场',
        grouping_note='各币独立10000起始资金；不是共享资金组合回测；不含实盘旧风控与选币模块',
        manifest=manifest,results=rows,failed=failed)
    (args.out/'summary.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    pd.DataFrame(rows).to_csv(args.out/'metrics.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(all_trades).to_csv(args.out/'trades.csv',index=False,encoding='utf-8-sig')
    if failed: raise SystemExit(2)

if __name__=='__main__': main()
