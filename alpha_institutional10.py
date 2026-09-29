"""ALPHA-X Institutional 10.0: production-grade, fail-closed orchestration primitives.
Network-free reference implementation. No automatic live promotion is performed here.
"""
from __future__ import annotations
import hashlib, json, math, os, time, uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

VERSION='ALPHA-X-INSTITUTIONAL-10.0'
ROOT=Path(__file__).parent


def stable_id(*parts: Any)->str:
    return hashlib.sha256('|'.join(map(str,parts)).encode()).hexdigest()[:24]


def finite(x, default=0.0):
    try:
        x=float(x); return x if math.isfinite(x) else default
    except Exception: return default


def percentile(xs, q):
    a=sorted(finite(x) for x in xs); n=len(a)
    if not n:return 0.0
    p=max(0,min(1,q))*(n-1); lo=int(p); hi=min(n-1,lo+1); return a[lo]+(a[hi]-a[lo])*(p-lo)


def execution_quality(fills, arrival_price, side):
    vals=[]; latency=[]
    sign=1 if str(side).lower() in ('buy','long') else -1
    for f in fills or []:
        px=finite(f.get('fill_px',f.get('avgPx',f.get('price',0))))
        qty=abs(finite(f.get('qty',f.get('fillSz',f.get('size',0)))))
        if px<=0 or qty<=0: continue
        vals.append((px,qty))
        if f.get('latency_ms') is not None: latency.append(finite(f['latency_ms']))
    qty=sum(q for _,q in vals)
    avg=sum(p*q for p,q in vals)/qty if qty else finite(arrival_price)
    is_bps=sign*(avg-finite(arrival_price))/max(finite(arrival_price),1e-12)*10000
    return {'fills':len(vals),'qty':qty,'avg_fill_px':avg,'arrival_px':finite(arrival_price),
            'implementation_shortfall_bps':is_bps,'latency_p50_ms':percentile(latency,.5),
            'latency_p90_ms':percentile(latency,.9),'slippage_bps':abs(is_bps)}


def pnl_attribution(entry_px, exit_px, qty, side, fee=0.0, funding=0.0, arrival_px=None):
    s=1 if str(side).lower() in ('buy','long') else -1
    gross=(finite(exit_px)-finite(entry_px))*finite(qty)*s
    entry_cost=abs(finite(entry_px)*finite(qty)); slip=0.0
    if arrival_px is not None: slip=abs(finite(entry_px)-finite(arrival_px))*finite(qty)
    net=gross-finite(fee)-finite(funding)-slip
    return {'gross_pnl':gross,'fee':finite(fee),'funding':finite(funding),'entry_slippage_cost':slip,'net_pnl':net}


def risk_budget(weights, covariance, gross_limit=1.0, max_weight=.25, max_vol=1.0):
    names=list(weights or {}); w={n:max(-max_weight,min(max_weight,finite(weights[n]))) for n in names}
    gross=sum(abs(v) for v in w.values())
    if gross>gross_limit and gross>0:
        k=gross_limit/gross; w={n:v*k for n,v in w.items()}
    var=0.0
    for i in names:
        for j in names:
            var+=w[i]*w[j]*finite((covariance.get(i,{}) or {}).get(j,0.0))
    vol=math.sqrt(max(var,0.0))
    if vol>max_vol and vol>0:
        k=max_vol/vol; w={n:v*k for n,v in w.items()}; vol=max_vol
    return {'weights':w,'gross':sum(abs(v) for v in w.values()),'volatility':vol,'within_limits':sum(abs(v) for v in w.values())<=gross_limit+1e-9 and vol<=max_vol+1e-9}


def readiness(checks:Dict[str,bool], live=False):
    failed=[k for k,v in checks.items() if not bool(v)]
    allowed=not failed
    # Live requires explicit opt-in and never defaults to permissive.
    if live and os.getenv('ALPHA_LIVE_ALLOWED','0')!='1':
        failed.append('live_not_explicitly_allowed'); allowed=False
    return {'version':VERSION,'allowed':allowed,'state':'READY' if allowed else 'FAIL_CLOSED','failed_checks':failed,'checks':checks,'live':live}

@dataclass
class Intent:
    symbol:str; side:str; qty:float; strategy_id:str; signal_id:str; created_at:float
    client_id:str=''
    status:str='NEW'
    def __post_init__(self):
        if not self.client_id:self.client_id='AX10-'+stable_id(self.symbol,self.side,self.qty,self.strategy_id,self.signal_id)

class IntentLedger:
    def __init__(self,path:Path): self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
    def put(self,intent:Intent):
        rows=self.read();
        if any(x.get('client_id')==intent.client_id for x in rows): return next(x for x in rows if x['client_id']==intent.client_id)
        d=asdict(intent); d['hash']=hashlib.sha256(json.dumps(d,sort_keys=True).encode()).hexdigest();
        with self.path.open('a',encoding='utf-8') as f:f.write(json.dumps(d,separators=(',',':'))+'\n')
        return d
    def read(self):
        if not self.path.exists():return []
        return [json.loads(x) for x in self.path.read_text(encoding='utf-8').splitlines() if x.strip()]
    def verify(self):
        rows=self.read(); ids=set();
        for r in rows:
            if r.get('client_id') in ids:return {'ok':False,'error':'duplicate_client_id'}
            ids.add(r.get('client_id')); h=r.get('hash'); c=dict(r); c.pop('hash',None)
            if hashlib.sha256(json.dumps(c,sort_keys=True).encode()).hexdigest()!=h:return {'ok':False,'error':'tamper'}
        return {'ok':True,'intents':len(rows)}


def model_promotion_gate(champion, challenger, min_n=100, min_mean=0.0, max_dd_ratio=2.0):
    c=[finite(x) for x in champion]; h=[finite(x) for x in challenger]
    cm=sum(c)/len(c) if c else 0; hm=sum(h)/len(h) if h else 0
    def dd(x):
        eq=peak=dd=0
        for v in x: eq+=v; peak=max(peak,eq); dd=max(dd,peak-eq)
        return dd
    cdd=dd(c); hdd=dd(h)
    return {'eligible':bool(len(h)>=min_n and hm>min_mean and hdd<=max(cdd*max_dd_ratio,1e-9)),
            'champion_n':len(c),'challenger_n':len(h),'champion_mean':cm,'challenger_mean':hm,
            'champion_max_dd':cdd,'challenger_max_dd':hdd,'reason':'passed' if len(h)>=min_n and hm>min_mean and hdd<=max(cdd*max_dd_ratio,1e-9) else 'failed'}


def autonomous_policy(*, health_ok, drift_ok, risk_ok, execution_ok, reconciliation_ok, challenger_ok=False, retrain_needed=False):
    if not all([health_ok,risk_ok,reconciliation_ok]): return 'HALT_FAIL_CLOSED'
    if not execution_ok: return 'DEGRADED_NO_NEW_ORDERS'
    if not drift_ok or retrain_needed: return 'QUEUE_RESEARCH'
    if challenger_ok: return 'PROMOTION_REVIEW'
    return 'CONTINUE'
