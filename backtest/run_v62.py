"""Shared production strategy replay; reused history, not unseen holdout."""
from pathlib import Path
import sys,json,hashlib,argparse,concurrent.futures
import pandas as pd
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import v6_replay as r
import alpha_fast_v6 as v61
import alpha_fast_v62 as v62
VARIANTS={'V61':(False,{}),'V62趋势':(True,{'entry_set':'trend'}),'V62背景':(True,{'entry_set':'context'}),'V62突破':(True,{'entry_set':'breakout_only'})}
def work(job):
 file,name,stress=job;new,params=VARIANTS[name];r.v6=v62 if new else v61
 df=pd.read_csv(file).rename(columns={'open_time_ms':'open_ms','volume':'vol'})[['open_ms','open','high','low','close','vol']].iloc[:-1];n=len(df);sym=Path(file).stem.removesuffix('USDT_5m');rows=[];trades=[]
 for phase,a,b in [('前段',720,int(n*.6)),('中段',int(n*.6),int(n*.8)),('后段',int(n*.8),n)]:
  m,t=r.replay(sym,df,a,b,slip=.0003 if sym in ('BTC','ETH','SOL','XRP') else .001,cost_mult=stress,trailing=True,params=params)
  rows.append(dict(variant=name,phase=phase,symbol=sym,**m));trades.extend(dict(variant=name,phase=phase,**x) for x in t)
 return rows,trades

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--data',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);ap.add_argument('--variants',default=','.join(VARIANTS));ap.add_argument('--stress',type=float,default=1);ap.add_argument('--workers',type=int,default=4);a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True)
 files=sorted(a.data.glob('*USDT_5m.csv'));assert len(files)==33
 manifest=dict(variants={v:VARIANTS[v] for v in a.variants.split(',')},stress=a.stress,data={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in files},code={f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in ['alpha_fast_v6.py','alpha_fast_v62.py','backtest/v6_replay.py']},note='全部历史已复用，不称独立未知样本；每币独立账户，无资金费和真实执行验证')
 (a.out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
 rows=[];trades=[]
 with concurrent.futures.ProcessPoolExecutor(max_workers=a.workers) as ex:
  futures=[ex.submit(work,(str(f),v,a.stress)) for v in a.variants.split(',') for f in files]
  for fu in concurrent.futures.as_completed(futures):
   ms,ts=fu.result();rows.extend(ms);trades.extend(ts);print(ms[0]['variant'],ms[0]['symbol'],sum(x['trades'] for x in ms),flush=True)
   (a.out/'progress.json').write_text(json.dumps(rows,ensure_ascii=False))
 out=[]
 for v in a.variants.split(','):
  for phase in ['前段','中段','后段']:
   ts=[x for x in trades if x['variant']==v and x['phase']==phase];ms=[x for x in rows if x['variant']==v and x['phase']==phase];x=np.array([t['net_pnl'] for t in ts]);wins=x[x>0];loss=x[x<0]
   out.append(dict(variant=v,phase=phase,trades=len(x),win_pct=float((x>0).mean()*100) if len(x) else 0,profit_factor=float(wins.sum()/-loss.sum()) if len(loss) else None,payoff_ratio=float(wins.mean()/-loss.mean()) if len(wins) and len(loss) else None,net_pnl=float(x.sum()),average_return_pct=float(x.sum()/3300),worst_symbol_drawdown_pct=max(m['max_drawdown_pct'] for m in ms),profitable_symbols=sum(m['net_pnl']>0 for m in ms)))
 pd.DataFrame(trades).to_csv(a.out/'trades.csv',index=False);pd.DataFrame(rows).to_csv(a.out/'by_symbol.csv',index=False);pd.DataFrame(out).to_csv(a.out/'summary.csv',index=False);(a.out/'summary.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
