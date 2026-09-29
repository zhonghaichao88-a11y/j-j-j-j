"""Reproducible offline replay. Uses only bundled candles, no account/API connection."""
import csv,json,time,sys,os
from pathlib import Path
from collections import OrderedDict
os.chdir(Path(__file__).resolve().parent)
from alpha_v7_replay import simulate
from alpha_v7_analysis import STRATEGIES,analyze as original
import alpha_fast_v7 as strategy
from alpha_v7_pine import save
cache=OrderedDict()
def cached(f,context=None):
 key=(tuple(f['ts']),tuple(f['close']),int((context or {}).get('chan_level',1)))
 if key not in cache:cache[key]=original(f,context)
 while len(cache)>900:cache.popitem(last=False)
 return cache[key]
strategy.analyze=cached
results=[]
pine=Path('examples/V73_EMA_STRATEGY.pine').read_text(encoding='utf-8');pine_id=save(pine)
for symbol in ('BTCUSDT','ETHUSDT'):
 with open('data_v62/'+symbol+'_5m.csv') as handle:raw=list(csv.DictReader(handle))[-2200:]
 allrows=[dict(timestamp=int(r['open_time_ms']),**{k:float(r[k]) for k in ('open','high','low','close','volume')},confirmed=True) for r in raw]
 cases=[(k,{'strategy':k},480) for k in STRATEGIES if k!='chan_quant']+[( 'auto_regime',{'strategy':'auto_regime'},480)]
 cases += [('chan_L0_single_tf',{'strategy':'chan_quant','chan_level':0,'chan_mtf':0},2200),('chan_L1',{'strategy':'chan_quant','chan_level':1},2200),('pine_import',{'strategy':'pine_import','pine_id':pine_id},480)]
 for key,params,count in cases:
  started=time.time();r=simulate(allrows[-count:],params,10000,True,True)
  record=dict(symbol=symbol,strategy=key,params=params,summary=r['summary'],from_ms=r['from_ms'],to_ms=r['to_ms'],warmup=r['warmup_bars'],seconds=round(time.time()-started,2))
  results.append(record);print(symbol,key,r['summary']['交易数'],round(r['summary']['净收益'],2),record['seconds'],flush=True)
  Path('V73_REPLAY_RESULTS.json').write_text(json.dumps({'source':'原项目data_v62；来源未独立验证，非实时交易所验收','note':'联调回放，未优化参数，不是样本外盈利证明。订单流缺少历史档案，不参与回放。缠论高周期由已完成5m聚合，历史长度受区间限制。','results':results},ensure_ascii=False,indent=2),encoding='utf-8')
 cache.clear()
