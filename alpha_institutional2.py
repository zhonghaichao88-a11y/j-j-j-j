"""Institutional 2.0 control kernel: time, rate, execution, portfolio and fail-closed readiness."""
from __future__ import annotations
import math, time, threading
from dataclasses import dataclass

@dataclass
class ClockGuard:
    max_skew_ms: int = 1500
    last_server_ms: int | None = None
    def update(self, server_ms:int, local_ms:int|None=None):
        local=int(local_ms if local_ms is not None else time.time()*1000)
        self.last_server_ms=int(server_ms)
        return {'skew_ms':self.last_server_ms-local,'ok':abs(self.last_server_ms-local)<=self.max_skew_ms}
    def ok(self, local_ms:int|None=None):
        if self.last_server_ms is None:return False
        local=int(local_ms if local_ms is not None else time.time()*1000)
        return abs(self.last_server_ms-local)<=self.max_skew_ms

class TokenBucket:
    def __init__(self, rate_per_sec:float, burst:int):
        self.rate=max(float(rate_per_sec),0.01); self.capacity=max(int(burst),1); self.tokens=float(self.capacity); self.ts=time.monotonic(); self.lock=threading.Lock()
    def allow(self,cost:float=1.0)->bool:
        with self.lock:
            now=time.monotonic(); self.tokens=min(self.capacity,self.tokens+(now-self.ts)*self.rate); self.ts=now
            if self.tokens>=cost:self.tokens-=cost; return True
            return False

class FailureBreaker:
    def __init__(self, threshold:int=3, cooldown_s:float=30): self.threshold=threshold; self.cooldown=cooldown_s; self.failures=0; self.open_until=0.0
    def failure(self):
        self.failures+=1
        if self.failures>=self.threshold:self.open_until=time.monotonic()+self.cooldown
    def success(self): self.failures=0; self.open_until=0.0
    def is_open(self): return time.monotonic()<self.open_until
    def status(self): return {'open':self.is_open(),'failures':self.failures,'open_until':self.open_until}

def portfolio_stats(weights:dict[str,float], returns:dict[str,list[float]], covariance:dict[tuple[str,str],float]|None=None):
    names=list(weights); w=[float(weights[n]) for n in names]
    var=0.0
    for i,a in enumerate(names):
        for j,b in enumerate(names):
            cov=(covariance or {}).get((a,b), (covariance or {}).get((b,a),0.0))
            if i==j and not cov: 
                xs=returns.get(a,[]); mu=sum(xs)/len(xs) if xs else 0.0; cov=sum((x-mu)**2 for x in xs)/max(len(xs)-1,1) if xs else 0.0
            var += w[i]*w[j]*cov
    vol=math.sqrt(max(var,0))
    return {'portfolio_vol':vol,'variance':var,'gross':sum(abs(x) for x in w),'net':sum(w),'names':names}

def impact_cost_bps(notional:float, adv_notional:float, spread_bps:float, volatility:float, impact_coeff:float=20.0):
    adv=max(float(adv_notional),1e-9); participation=max(float(notional),0)/adv
    impact=max(0.0,float(impact_coeff))*math.sqrt(participation)*max(float(volatility),0)*10000
    return {'participation':participation,'spread_bps':max(float(spread_bps),0),'impact_bps':impact,'all_in_bps':max(float(spread_bps),0)+impact}

def readiness(data_ok:bool, clock_ok:bool, risk_ok:bool, execution_ok:bool, audit_ok:bool, breaker_open:bool=False):
    reasons=[]
    for ok,reason in ((data_ok,'data'),(clock_ok,'clock'),(risk_ok,'risk'),(execution_ok,'execution'),(audit_ok,'audit')):
        if not ok: reasons.append(reason)
    if breaker_open: reasons.append('breaker')
    return {'ready':not reasons,'mode':'TRADE' if not reasons else 'FAIL_CLOSED','reasons':reasons}
