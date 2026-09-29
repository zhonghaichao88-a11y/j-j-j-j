"""ALPHA-X institutional research layer.
Deterministic strategy committee + regime gate + portfolio allocator.
External feeds are optional and fail closed; no external feed can create a trade by itself.
"""
from __future__ import annotations
from typing import Dict, Any, Optional
import numpy as np

STRATEGIES = ("TREND", "MOMENTUM", "MEAN_REVERSION", "BREAKOUT", "ORDER_FLOW", "CARRY")

def _clip(x, lo=-1.0, hi=1.0):
    try: return float(np.clip(float(x), lo, hi))
    except Exception: return 0.0

def strategy_committee(row: Dict[str, Any], ctx: Dict[str, Any], probs: np.ndarray,
                       validation_sharpe: float = 0.0) -> Dict[str, Any]:
    trend=_clip(float(row.get("trend_score",0) or 0)/0.03)
    mom=_clip((float(row.get("ret10",0) or 0)+float(row.get("ret20",0) or 0))/0.04)
    rsi=float(row.get("rsi14",.5) or .5); mr=_clip((.5-rsi)/.25)
    breakout=_clip(float(row.get("dist_ema20",0) or 0)/0.03)
    imbalance=_clip(float(ctx.get("orderbook_imbalance") or 0)*4)
    funding=ctx.get("funding_rate")
    carry=0.0 if funding is None else _clip(-float(funding)/0.0015)
    vals={"TREND":trend,"MOMENTUM":mom,"MEAN_REVERSION":mr,"BREAKOUT":breakout,"ORDER_FLOW":imbalance,"CARRY":carry}
    weights={"TREND":1.0,"MOMENTUM":.9,"MEAN_REVERSION":.65,"BREAKOUT":.8,"ORDER_FLOW":.9,"CARRY":.45}
    regime=ctx.get("regime","UNKNOWN")
    if regime=="RANGE": weights.update(TREND=.45,MOMENTUM=.55,MEAN_REVERSION=1.0,BREAKOUT=.45)
    elif regime in ("STRESS","EXTREME"): weights.update(TREND=.75,MOMENTUM=.65,MEAN_REVERSION=.25,BREAKOUT=.55,CARRY=.2)
    if validation_sharpe>0.8: weights["TREND"]*=1.15
    score=sum(vals[k]*weights[k] for k in STRATEGIES)/sum(weights.values())
    model_dir=float(probs[2]-probs[0])
    agreement=float(np.mean([1 if (v*model_dir)>=0 else 0 for v in vals.values()])) if model_dir else 0.0
    composite=_clip(.55*_clip(model_dir*2)+.45*score)
    signal="LONG" if composite>=0.18 and probs[2]>probs[0] else ("SHORT" if composite<=-0.18 and probs[0]>probs[2] else "FLAT")
    # Agreement is a sizing/reliability input; only strong opposite evidence should veto.
    if agreement < 0.25 and abs(composite) < 0.30: signal="FLAT"
    return {"signal":signal,"committee_score":composite,"agreement":agreement,"alphas":vals,"weights":weights}

def portfolio_allocator(scores: Dict[str,float], vols: Dict[str,float], correlations: Optional[np.ndarray]=None,
                        max_weight: float=.25, max_gross: float=.75) -> Dict[str,float]:
    keys=list(scores)
    if not keys: return {}
    s=np.maximum(np.asarray([float(scores[k]) for k in keys]),0)
    v=np.maximum(np.asarray([float(vols.get(k,1.0)) for k in keys]),1e-6)
    raw=s/v
    if correlations is not None and correlations.shape==(len(keys),len(keys)):
        c=np.clip(np.nan_to_num(correlations,nan=0),-1,1); np.fill_diagonal(c,1)
        penalty=1+np.maximum(c.sum(axis=1)-1,0)
        raw/=penalty
    if raw.sum()<=0: return {k:0.0 for k in keys}
    w=raw/raw.sum()*min(1.0,max_gross)
    w=np.minimum(w,max_weight)
    if w.sum()>max_gross: w=w/w.sum()*max_gross
    return {k:float(w[i]) for i,k in enumerate(keys)}

def risk_adjusted_confidence(model_conf: float, committee_score: float, agreement: float, stress: float) -> float:
    # Confidence is the model signal strength only. Committee/stress are handled
    # independently downstream as direction confirmation and position sizing.
    return float(np.clip(float(model_conf),0,1))
