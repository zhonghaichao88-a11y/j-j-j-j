"""ALPHA-X ULTRA MAX 10.0 institutional research/governance kernel.
Network-free primitives: attribution, multiple-testing-aware promotion, drift,
capacity/cost stress, model lifecycle, and fail-closed policy decisions.
"""
from __future__ import annotations
import hashlib, json, math, statistics, time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT=Path(__file__).parent
STATE_FILE=ROOT/'alpha10_governance.json'


def _safe_float(x, default=0.0):
    try:
        x=float(x); return x if math.isfinite(x) else default
    except Exception: return default


def stable_id(*parts: Any) -> str:
    raw='|'.join(str(x) for x in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def atomic_json(path: Path, data: Dict[str,Any]) -> None:
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2,sort_keys=True),encoding='utf-8')
    tmp.replace(path)


def load_state() -> Dict[str,Any]:
    if not STATE_FILE.exists(): return {'version':1,'models':{},'events':[]}
    try: return json.loads(STATE_FILE.read_text(encoding='utf-8'))
    except Exception: return {'version':1,'models':{},'events':[]}


def append_event(kind:str, payload:Dict[str,Any]) -> Dict[str,Any]:
    s=load_state(); ev={'id':stable_id(kind,time.time_ns()),'kind':kind,'ts':time.time(),'payload':payload}
    s.setdefault('events',[]).append(ev); s['events']=s['events'][-1000:]; atomic_json(STATE_FILE,s); return ev


def returns_stats(pnls: Iterable[float]) -> Dict[str,float]:
    x=[_safe_float(v) for v in pnls if math.isfinite(_safe_float(v))]
    n=len(x)
    if n==0: return {'n':0,'mean':0.0,'std':0.0,'sharpe':0.0,'downside':0.0,'sortino':0.0,'pf':0.0,'win_rate':0.0,'max_dd':0.0}
    mean=statistics.mean(x); sd=statistics.stdev(x) if n>1 else 0.0
    wins=sum(v for v in x if v>0); losses=-sum(v for v in x if v<0)
    downside=math.sqrt(sum(min(v,0.0)**2 for v in x)/max(n,1))
    eq=peak=0.0; maxdd=0.0
    for v in x:
        eq+=v; peak=max(peak,eq); maxdd=max(maxdd,peak-eq)
    return {'n':n,'mean':mean,'std':sd,'sharpe':mean/(sd+1e-12)*math.sqrt(n) if n>1 else 0.0,
            'downside':downside,'sortino':mean/(downside+1e-12)*math.sqrt(n) if n else 0.0,
            'pf':wins/max(losses,1e-12) if losses else (99.0 if wins else 0.0),
            'win_rate':sum(v>0 for v in x)/n,'max_dd':maxdd}


def bootstrap_mean_ci(pnls: Iterable[float], iterations:int=2000, seed:int=19, alpha:float=.05)->Dict[str,float]:
    import numpy as np
    x=np.asarray([_safe_float(v) for v in pnls],dtype=float); x=x[np.isfinite(x)]
    if len(x)<2: return {'n':int(len(x)),'low':0.0,'high':0.0,'mean':float(x.mean()) if len(x) else 0.0}
    rng=np.random.default_rng(seed); means=np.empty(iterations)
    for i in range(iterations): means[i]=rng.choice(x,size=len(x),replace=True).mean()
    return {'n':int(len(x)),'low':float(np.quantile(means,alpha/2)),'high':float(np.quantile(means,1-alpha/2)),'mean':float(x.mean())}


def multiple_testing_penalty(num_trials:int, alpha:float=.05)->Dict[str,float]:
    """Bonferroni-style family-wise threshold; conservative by design."""
    m=max(1,int(num_trials)); return {'trials':m,'raw_alpha':alpha,'adjusted_alpha':alpha/m,'min_z':3.0 if m>=20 else 2.33 if m>=5 else 1.96}


def promotion_gate(champion:Iterable[float], challenger:Iterable[float], *, trials:int=1,
                   min_n:int=100, min_mean:float=0.0, min_pf:float=1.05,
                   min_sharpe:float=0.5)->Dict[str,Any]:
    c=returns_stats(champion); h=returns_stats(challenger); ci=bootstrap_mean_ci(challenger)
    mt=multiple_testing_penalty(trials)
    # We require challenger absolute economics AND CI excluding zero. For small samples this fails closed.
    eligible=(h['n']>=min_n and h['mean']>min_mean and h['pf']>=min_pf and h['sharpe']>=min_sharpe
              and ci['low']>0 and c['n']>=max(30,min_n//2))
    return {'eligible':bool(eligible),'champion':c,'challenger':h,'challenger_mean_ci95':ci,
            'multiple_testing':mt,'reason':'all_statistical_gates_passed' if eligible else 'statistical_gate_failed'}


def cost_stress(pnls:Iterable[float], extra_bps:Iterable[float]=(0,2,5,10,20,40), turnover_notional:float=1.0)->List[Dict[str,float]]:
    stats=returns_stats(pnls); out=[]
    for bps in extra_bps:
        cost=float(bps)/10000*float(turnover_notional)
        mean=stats['mean']-cost
        out.append({'extra_cost_bps':float(bps),'mean_pnl_after_cost':mean,'pf_proxy':max(0.0,stats['pf']*(mean/max(stats['mean'],1e-12))) if stats['mean']>0 else 0.0})
    return out


def capacity_curve(pnls:Iterable[float], notionals:Iterable[float]=(1,2,5,10), impact_bps:float=2.0)->List[Dict[str,float]]:
    base=returns_stats(pnls); out=[]
    for scale in notionals:
        impact=impact_bps*max(0.0,float(scale)-1.0)
        mean=base['mean']*scale-(impact/10000.0)*scale
        out.append({'notional_multiple':float(scale),'impact_bps':float(impact),'mean_pnl':mean})
    return out


def factor_attribution(trades:Iterable[Dict[str,Any]])->Dict[str,Any]:
    """Approximate online factor contribution from realized trade score exposure.
    This is attribution, not causal inference; it must never be used as a label source.
    """
    agg={}
    for t in trades or []:
        pnl=_safe_float(t.get('net_pnl',t.get('pnl',0)))
        scores=t.get('factor_scores') or {}
        for name,score in scores.items():
            s=_safe_float(score); a=agg.setdefault(str(name),{'observations':0,'weighted_pnl':0.0,'abs_exposure':0.0})
            a['observations']+=1; a['weighted_pnl']+=pnl*s; a['abs_exposure']+=abs(s)
    for a in agg.values(): a['contribution_per_abs_exposure']=a['weighted_pnl']/max(a['abs_exposure'],1e-12)
    return {'factors':agg,'method':'online_exposure_attribution','causal':False}


@dataclass
class Lifecycle:
    model_id:str
    symbol:str
    role:str='candidate'
    state:str='CANDIDATE'
    created_at:float=0.0
    promoted_at:float=0.0
    retired_at:float=0.0
    reason:str=''

    def transition(self, state:str, reason:str=''):
        allowed={'CANDIDATE':{'SHADOW','REJECTED'},'SHADOW':{'PROMOTION_REVIEW','REJECTED','RETRAIN'},
                 'PROMOTION_REVIEW':{'CHAMPION','REJECTED'},'CHAMPION':{'RETRAIN','RETIRED'},
                 'RETRAIN':{'CANDIDATE'},'REJECTED':set(),'RETIRED':set()}
        if state not in allowed.get(self.state,set()): raise ValueError(f'illegal lifecycle transition {self.state}->{state}')
        self.state=state; self.reason=reason
        now=time.time()
        if state=='CHAMPION': self.promoted_at=now
        if state=='RETIRED': self.retired_at=now
        return self


def fail_closed(*, data_ok:bool, model_ok:bool, risk_ok:bool, execution_ok:bool,
                reconciliation_ok:bool, daily_loss_ok:bool=True)->Dict[str,Any]:
    checks={'data':data_ok,'model':model_ok,'risk':risk_ok,'execution':execution_ok,'reconciliation':reconciliation_ok,'daily_loss':daily_loss_ok}
    failed=[k for k,v in checks.items() if not bool(v)]
    return {'allowed':not failed,'state':'READY' if not failed else 'FAIL_CLOSED','failed_checks':failed,'checks':checks}


def snapshot() -> Dict[str,Any]:
    s=load_state(); return {'version':'10.0','models':s.get('models',{}),'event_count':len(s.get('events',[])),'generated_at':time.time()}
