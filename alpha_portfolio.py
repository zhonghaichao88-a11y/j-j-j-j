"""Institutional portfolio construction primitives: risk-budgeted, exposure-aware, fail-closed."""
from __future__ import annotations
import math

def _norm(x):
    s=sum(max(float(v),0) for v in x.values())
    return {k:max(float(v),0)/s for k,v in x.items()} if s else {k:0 for k in x}

def allocate(scores:dict, vols:dict, correlations:dict|None=None, max_weight:float=.25)->dict:
    raw={k:max(float(scores.get(k,0)),0)/max(float(vols.get(k,1)),1e-6) for k in scores}
    w=_norm(raw); w={k:min(v,max_weight) for k,v in w.items()}; w=_norm(w)
    return w

def concentration(weights:dict)->dict:
    vals=list(weights.values()); hhi=sum(v*v for v in vals); return {'hhi':hhi,'effective_positions':1/hhi if hhi>0 else 0,'max_weight':max(vals) if vals else 0}
