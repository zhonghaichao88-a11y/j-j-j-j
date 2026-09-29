"""ALPHA-X Multi-Alpha Research Core.

This is the strategy layer, not an execution/risk wrapper.  It creates several
independent views of the same completed-bar information and optionally learns a
second-stage meta model from strictly walk-forward out-of-fold observations.
No test-set observation is used for feature/weight selection.
"""
from __future__ import annotations
from typing import Dict, Any, Iterable, List, Tuple
import numpy as np
from sklearn.linear_model import LogisticRegression

NAMES = (
    "TREND", "MOMENTUM", "MEAN_REVERSION", "BREAKOUT", "VOLATILITY",
    "VOLUME", "MICRO_MOMENTUM", "REGIME_FIT", "MODEL_DIRECTION", "FUNDING", "OI", "LIQUIDATION", "ORDER_FLOW", "L2", "CROSS_MARKET",
)


def _clip(x, lo=-3.0, hi=3.0):
    try:
        x=float(x)
        if not np.isfinite(x): return 0.0
        return float(np.clip(x, lo, hi))
    except Exception:
        return 0.0


def factor_vector(row: Dict[str, Any], probs: Iterable[float] | None = None) -> np.ndarray:
    """Return normalized directional alpha views in [-1, 1]."""
    p=np.asarray(list(probs) if probs is not None else [1/3,1/3,1/3], dtype=float)
    if p.size<3: p=np.pad(p,(0,3-p.size),constant_values=0)
    p=p[:3]
    trend=_clip(float(row.get("trend_score",0) or 0)/0.018)/3
    mom=_clip((float(row.get("ret10",0) or 0)+0.5*float(row.get("ret20",0) or 0))/0.035)/3
    rsi=float(row.get("rsi14",.5) or .5)
    mr=_clip((0.5-rsi)/0.18)/3
    breakout=_clip(float(row.get("dist_ema20",0) or 0)/0.025)/3
    vr=float(row.get("vol_regime",1) or 1)
    vol=-_clip((vr-1.0)/1.2)/3
    vol_ratio=float(row.get("vol_ratio",1) or 1)
    direction=np.sign(float(row.get("body_pct",0) or 0))*min(abs(float(row.get("body_pct",0) or 0))/0.012,1.0)
    volume=float(np.tanh((vol_ratio-1.0))) * direction
    micro=_clip((float(row.get("obv_slope",0) or 0))/0.08)/3
    regime=_clip(float(row.get("trend_strength",0) or 0)/5.0)/3 * np.sign(trend if trend else direction)
    model=float(np.clip(p[2]-p[0],-1,1))
    funding=_clip(float(row.get("funding_rate",0) or 0)/0.0008) / 3
    oi=_clip(float(row.get("oi_change_pct",0) or 0)/0.08) / 3
    liq=_clip(float(row.get("liquidation_net_usd",0) or 0) / max(abs(float(row.get("liquidation_buy_usd",0) or 0))+abs(float(row.get("liquidation_sell_usd",0) or 0)),1.0))
    ofi=_clip(float(row.get("ofi",row.get("trade_imbalance",0)) or 0)) / 3
    depth=_clip(float(row.get("depth_imbalance",0) or 0)) / 3
    cross=_clip((float(row.get("btc_eth_spread",0) or 0)+float(row.get("venue_basis_bps",0) or 0)/10000.0)/0.02) / 3
    return np.array([trend,mom,mr,breakout,vol,volume,micro,regime,model,funding,oi,liq,ofi,depth,cross],dtype=float)


def factor_dict(row, probs=None) -> Dict[str,float]:
    return {k:float(v) for k,v in zip(NAMES,factor_vector(row,probs))}


def meta_input(row, probs) -> np.ndarray:
    """Meta features: base probabilities + factor views + simple uncertainty."""
    p=np.asarray(probs,dtype=float)[:3]
    if p.size<3: p=np.pad(p,(0,3-p.size),constant_values=0)
    fv=factor_vector(row,p)
    entropy=float(-np.sum(np.clip(p,1e-9,1)*np.log(np.clip(p,1e-9,1))))
    margin=float(max(p[0],p[2])-p[1])
    return np.r_[p,fv,entropy,margin]


def fit_meta(X: np.ndarray, y: np.ndarray):
    X=np.asarray(X,float); y=np.asarray(y,int)
    ok=np.isfinite(X).all(axis=1) & np.isfinite(y)
    X,y=X[ok],y[ok]
    if len(X)<300 or len(np.unique(y))<3:
        return None
    m=LogisticRegression(max_iter=1800,class_weight="balanced",C=.45,random_state=2026)
    m.fit(X,y)
    return m


def meta_proba(model, X: np.ndarray) -> np.ndarray:
    if model is None:
        return np.asarray(X)[:,:3]
    p=model.predict_proba(np.asarray(X,float)); out=np.zeros((len(p),3))
    for j,c in enumerate(model.classes_): out[:,int(c)]=p[:,j]
    return out


def blend_proba(base_p: np.ndarray, meta_p: np.ndarray | None, strength: float=.65) -> np.ndarray:
    b=np.asarray(base_p,float)
    if meta_p is None: return b
    m=np.asarray(meta_p,float)
    s=float(np.clip(strength,0,1)); z=(1-s)*b+s*m
    z=np.maximum(z,1e-9); return z/z.sum(axis=1,keepdims=True)


def meta_explanation(row, probs, meta_p=None) -> Dict[str,Any]:
    fv=factor_dict(row,probs)
    base=float(max(probs[0],probs[2]))
    mp=None if meta_p is None else [float(x) for x in meta_p]
    return {"factors":fv,"base_direction":float(probs[2]-probs[0]),
            "base_confidence":base,"meta_probabilities":mp,
            "alpha_core":"MULTI_ALPHA_META"}
