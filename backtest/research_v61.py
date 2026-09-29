"""Chronological development/validation/untouched test; no parameter search on test."""
import argparse
import concurrent.futures
import importlib.util
import json
import sys
from pathlib import Path
import pandas as pd
import numpy as np
import v6_replay as replay

RESEARCH_BASE=dict(structure_target=True,target_buffer_atr=.20,protect_at_r=.80,
                   entry_policy='legacy',min_risk_cost=0.0,target_r=0.0)

CANDIDATES={
    'original':dict(original=True,trailing=False,params={}),
    'original_trail':dict(original=True,trailing=True,params={}),
    'structure':dict(original=False,trailing=False,params={'structure_target':True}),
    'structure_protect':dict(original=False,trailing=True,params={'structure_target':True,'protect_at_r':.8}),
    'quality':dict(original=False,trailing=True,params={'entry_policy':'quality','min_risk_cost':2.,'protect_at_r':1.}),
    'early':dict(original=False,trailing=True,params={'entry_policy':'early_pullback','min_risk_cost':2.,'protect_at_r':1.}),
    'early_near':dict(original=False,trailing=True,params={'entry_policy':'early_pullback','min_risk_cost':1.,'protect_at_r':.8,'target_r':1.8}),
    'quality_no_trail':dict(original=False,trailing=False,params={'entry_policy':'quality','min_risk_cost':2.,'protect_at_r':1.}),
    'quality_near':dict(original=False,trailing=True,params={'entry_policy':'quality','min_risk_cost':2.,'protect_at_r':1.,'target_r':1.8}),
    'quality_late':dict(original=False,trailing=True,params={'entry_policy':'quality','min_risk_cost':2.,'protect_at_r':1.4}),
}

def pooled(trades, rows):
    x=np.array([t['net_pnl'] for t in trades],float)
    wins=x[x>0]; losses=x[x<0]
    return dict(trades=len(x),win_pct=float((x>0).mean()*100) if len(x) else 0,
        payoff_ratio=float(wins.mean()/-losses.mean()) if len(wins) and len(losses) else None,
        profit_factor=float(wins.sum()/-losses.sum()) if len(losses) else None,
        net_pnl=float(x.sum()),equal_capital_return_pct=float(x.sum()/max(len(rows),1)/100),
        profitable_symbols=sum(r['net_pnl']>0 for r in rows),symbols=len(rows),
        worst_symbol_drawdown_pct=max((r['max_drawdown_pct'] for r in rows),default=0))

def worker(job):
    path,baseline,variant,phase,stress=job
    cfg=CANDIDATES[variant]
    if cfg['original']:
        spec=importlib.util.spec_from_file_location('baseline_v6',baseline)
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);replay.v6=mod
    else:
        import alpha_fast_v6
        replay.v6=alpha_fast_v6
    df=pd.read_csv(path);n=len(df);sym=Path(path).stem.removesuffix('_5m')
    bounds={'development':(720,int(n*.6)), 'validation':(int(n*.6),int(n*.8)), 'test':(int(n*.8),n)}
    a,b=bounds[phase]
    slip=.0003 if sym in ('BTC','ETH','SOL','XRP') else .001
    m,t=replay.replay(sym,df,a,b,slip=slip,cost_mult=stress,trailing=cfg['trailing'],params={**RESEARCH_BASE,**cfg['params']})
    return dict(symbol=sym,variant=variant,phase=phase,stress=stress,
                start_ms=int(df.open_ms.iloc[a]),end_ms=int(df.open_ms.iloc[b-1])+300000,**m),[
                    dict(variant=variant,phase=phase,stress=stress,**x) for x in t]

def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True)
    p.add_argument('--baseline',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--phase',choices=['development','validation','test'],required=True)
    p.add_argument('--variants',default=','.join(CANDIDATES));p.add_argument('--stress',type=float,default=1)
    p.add_argument('--workers',type=int,default=4)
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    jobs=[(str(f),str(a.baseline),v,a.phase,a.stress) for v in a.variants.split(',') for f in sorted(a.data.glob('*_5m.csv'))]
    rows=[];trades=[]
    with concurrent.futures.ProcessPoolExecutor(max_workers=a.workers) as pool:
        futures=[pool.submit(worker,j) for j in jobs]
        for future in concurrent.futures.as_completed(futures):
            m,t=future.result();rows.append(m);trades.extend(t)
            print(m['variant'],m['symbol'],m['phase'],m['trades'],round(m['return_pct'],3),flush=True)
            (a.out/'progress.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    summary={v:pooled([t for t in trades if t['variant']==v],[r for r in rows if r['variant']==v]) for v in a.variants.split(',')}
    payload=dict(phase=a.phase,stress=a.stress,candidates=CANDIDATES,summary=summary,results=rows)
    (a.out/'summary.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False))
    pd.DataFrame(rows).to_csv(a.out/'metrics.csv',index=False)
    pd.DataFrame(trades).to_csv(a.out/'trades.csv',index=False)
    print(json.dumps(summary,indent=2),flush=True)

if __name__=='__main__':main()
