"""ALPHA-X ULTRA MAX 5.0 portfolio risk engine.
No leverage target is treated as a risk budget. Risk is budgeted at portfolio level.
"""
from __future__ import annotations
import math
from typing import Dict, Any
import numpy as np


def covariance_weights(returns: Dict[str, Any], scores: Dict[str,float], max_weight=.45, gross=1.0)->Dict[str,float]:
    syms=[s for s in scores if s in returns]
    if not syms: return {}
    mat=[]; valid=[]
    for s in syms:
        a=np.asarray(returns[s],dtype=float); a=a[np.isfinite(a)]
        if len(a)>=40: valid.append(s); mat.append(a[-300:])
    if not valid: return _score_weights(scores,max_weight,gross)
    m=min(len(x) for x in mat); X=np.vstack([x[-m:] for x in mat]);
    cov=np.cov(X); cov=np.atleast_2d(cov)+np.eye(len(valid))*1e-8
    try:
        inv=np.linalg.pinv(cov); raw=np.maximum(inv@np.asarray([max(scores[s],1e-6) for s in valid]),0)
    except Exception: return _score_weights(scores,max_weight,gross)
    if not np.any(raw>0): return {}
    raw=raw/raw.sum()*gross
    raw=np.minimum(raw,max_weight)
    if raw.sum()>gross: raw=raw/raw.sum()*gross
    return {s:float(w) for s,w in zip(valid,raw)}


def _score_weights(scores,max_weight,gross):
    total=sum(max(float(v),0) for v in scores.values())
    if total<=0:return {}
    w={s:min(max_weight,max(float(v),0)/total*gross) for s,v in scores.items() if v>0}
    if sum(w.values())>gross:
        z=sum(w.values()); w={s:v/z*gross for s,v in w.items()}
    return w


def risk_budget(equity:float, risk_pct:float, stop_pct:float, confidence:float, stress:float,
                portfolio_weight:float, max_notional_pct:float, leverage:float, free:float)->Dict[str,float]:
    equity=max(float(equity),0); free=max(float(free),0); stop=max(float(stop_pct),0.002)
    conf=max(0,min(1,float(confidence))); stress=max(0,min(1,float(stress)))
    # Stress reduces risk continuously; confidence only scales within the already bounded budget.
    risk_dollars=equity*max(float(risk_pct),0)*conf*(1-0.55*stress)*max(0,min(1.20,float(portfolio_weight)))
    notional=risk_dollars/stop
    cap=min(equity*max_notional_pct,free*max(float(leverage),1)*0.80)
    return {"risk_usdt":float(risk_dollars),"notional_usdt":float(max(0,min(notional,cap))),"portfolio_weight":float(portfolio_weight)}


def daily_loss_block(start_equity:float,equity:float,max_loss_pct:float)->bool:
    s=float(start_equity); e=float(equity)
    return s>0 and (s-e)/s>=float(max_loss_pct)
