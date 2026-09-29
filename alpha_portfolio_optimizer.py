"""Small, deterministic constrained portfolio optimizer. No external solver required."""
from __future__ import annotations
import math

def optimize(expected:dict, cov:dict, max_weight=.25, risk_aversion=1.0, gross_limit=1.0, step=.01):
    names=list(expected); w={n:0.0 for n in names}
    # Greedy marginal utility allocation; deterministic and fail-closed.
    candidates=[]
    for n in names:
        var=max(float(cov.get(n,{}).get(n,1.0)),1e-12)
        score=float(expected[n])/(math.sqrt(var)*max(risk_aversion,1e-9))
        if math.isfinite(score) and score>0:candidates.append((score,n))
    candidates.sort(reverse=True)
    remaining=max(float(gross_limit),0)
    for _,n in candidates:
        add=min(max(float(max_weight),0),remaining)
        w[n]=add; remaining-=add
        if remaining<=1e-12:break
    return w

def portfolio_stats(weights,cov):
    names=list(weights); v=0.0
    for i in names:
        for j in names:
            cij=float(cov.get(i,{}).get(j,cov.get(j,{}).get(i,0.0)))
            v+=weights[i]*weights[j]*cij
    vol=math.sqrt(max(v,0))
    gross=sum(abs(x) for x in weights.values())
    return {'variance':v,'volatility':vol,'gross':gross,'hhi':sum(x*x for x in weights.values())}
