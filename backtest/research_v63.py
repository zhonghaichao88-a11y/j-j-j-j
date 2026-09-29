"""V6.3 fixed-candidate research on the reused V6.2 dataset.

This is development evidence, not an unseen holdout.  Each candidate is run on
all 33 symbols and all three chronological segments with the production V6.2
decision and exit functions.
"""
from pathlib import Path
import argparse, concurrent.futures, json, sys
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import v6_replay as replay_engine
import alpha_fast_v62 as strategy

BASE_QUALITY=dict(entry_set="adaptive",short_macro_filter=True,short_size_multiplier=.35,
 min_net_rr=1.15,min_target_cost=2.,breakout_body_atr=.08,breakout_close_location=.55,
 retest_body_atr=.05,range_target_fraction=.5,range_max_width_atr=10.)
CANDIDATES={
 "V63固定方案":dict(BASE_QUALITY,benchmark_trend_return=.05,range_target_fraction=.5,
  range_er_max=.20,range_max_width_atr=8.,range_sweep_atr=.10,
  range_reclaim_atr=.12,range_close_location=.64,range_volume_ratio=.8),
}

_BENCH_CACHE={}
def benchmark_lookup(data_dir):
 key=str(data_dir)
 if key not in _BENCH_CACHE:
  frames=[];timestamps=None
  for f in sorted(Path(data_dir).glob("*USDT_5m.csv")):
   d=pd.read_csv(f,usecols=["open_time_ms","close"]).iloc[:-1]
   if timestamps is None:timestamps=d["open_time_ms"].to_numpy()
   frames.append(np.log(d["close"].astype(float).to_numpy()))
  market=np.exp(np.mean(np.vstack(frames),axis=0));lag=72*12
  ret=np.full(len(market),np.nan);ret[lag:]=market[lag:]/market[:-lag]-1
  _BENCH_CACHE[key]={int(ts)+300000:{"return_72h":float(v),"symbols":len(frames)} for ts,v in zip(timestamps,ret) if np.isfinite(v)}
 return _BENCH_CACHE[key]

def work(job):
 file,name,stress=job
 df=pd.read_csv(file).rename(columns={"open_time_ms":"open_ms","volume":"vol"})[["open_ms","open","high","low","close","vol"]].iloc[:-1]
 n=len(df); sym=Path(file).stem.removesuffix("USDT_5m"); rows=[]; trades=[]
 replay_engine.v6=strategy
 for phase,a,b in [("前段",720,int(n*.6)),("中段",int(n*.6),int(n*.8)),("后段",int(n*.8),n)]:
  m,t=replay_engine.replay(sym,df,a,b,slip=.0003 if sym in ("BTC","ETH","SOL","XRP") else .001,
                           cost_mult=stress,trailing=True,params=CANDIDATES[name],benchmark=benchmark_lookup(Path(file).parent))
  rows.append(dict(candidate=name,phase=phase,symbol=sym,**m))
  trades.extend(dict(candidate=name,phase=phase,**x) for x in t)
 return rows,trades

def summarize(rows,trades):
 out=[]
 for name in CANDIDATES:
  for phase in ("前段","中段","后段"):
   ts=[x for x in trades if x["candidate"]==name and x["phase"]==phase]
   ms=[x for x in rows if x["candidate"]==name and x["phase"]==phase]
   x=np.asarray([t["net_pnl"] for t in ts],float); w=x[x>0]; l=x[x<0]
   out.append(dict(candidate=name,phase=phase,trades=len(x),win_pct=float((x>0).mean()*100) if len(x) else 0.,
    profit_factor=float(w.sum()/-l.sum()) if len(l) else None,net_pnl=float(x.sum()),
    worst_symbol_drawdown_pct=max((m["max_drawdown_pct"] for m in ms),default=0.),
    profitable_symbols=sum(m["net_pnl"]>0 for m in ms)))
 return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--data",type=Path,required=True);ap.add_argument("--out",type=Path,required=True)
 ap.add_argument("--stress",type=float,default=1.);ap.add_argument("--workers",type=int,default=7);args=ap.parse_args()
 args.out.mkdir(parents=True,exist_ok=True);files=sorted(args.data.glob("*USDT_5m.csv"));assert len(files)==33
 rows=[];trades=[]
 with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
  fs=[pool.submit(work,(str(f),name,args.stress)) for name in CANDIDATES for f in files]
  for i,f in enumerate(concurrent.futures.as_completed(fs),1):
   r,t=f.result();rows.extend(r);trades.extend(t)
   if i%33==0:print(f"完成 {i}/{len(fs)} 个币种候选",flush=True)
 out=summarize(rows,trades)
 pd.DataFrame(rows).to_csv(args.out/"by_symbol.csv",index=False)
 pd.DataFrame(trades).to_csv(args.out/"trades.csv",index=False)
 pd.DataFrame(out).to_csv(args.out/"summary.csv",index=False)
 (args.out/"summary.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
 print(pd.DataFrame(out).pivot(index="candidate",columns="phase",values="profit_factor").to_string())

if __name__=="__main__":main()
