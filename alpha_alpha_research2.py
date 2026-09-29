"""ALPHA-X Alpha Research 2.0: anti-overfit + multi-source + cost-aware gates.

The module is deliberately conservative: unavailable data sources are never fabricated.
Historical multi-source columns are optional and must be supplied by the data adapter.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, Iterable, List, Tuple
import math
import numpy as np
from sklearn.linear_model import LogisticRegression, RidgeClassifier
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.metrics import log_loss

SOURCE_COLUMNS = {
    "funding": ("funding_rate", "funding_rate_realized", "funding_rate_event"),
    "open_interest": ("open_interest", "oi_change_pct", "oi"),
    "liquidation": ("liquidation_buy_usd", "liquidation_sell_usd", "liquidation_net_usd"),
    "order_flow": ("ofi", "aggressive_buy_ratio", "trade_imbalance", "trade_count"),
    "l2": ("bid_depth_usd", "ask_depth_usd", "spread_bps", "depth_imbalance"),
    "cross_market": ("btc_ret_1", "eth_ret_1", "btc_eth_spread", "venue_basis_bps"),
}

@dataclass(frozen=True)
class CostModel:
    fee_bps: float = 5.0
    slippage_bps: float = 5.0
    funding_bps_per_8h: float = 0.0
    impact_bps: float = 0.0
    stress_multiplier: float = 1.0
    def total_bps(self, holding_hours: float = 0.25) -> float:
        funding=max(0.0,float(self.funding_bps_per_8h))*max(0.0,float(holding_hours))/8.0
        return (max(0.0,float(self.fee_bps))+max(0.0,float(self.slippage_bps))+max(0.0,float(self.impact_bps))+funding)*max(1.0,float(self.stress_multiplier))

def source_audit(rows: Iterable[Dict[str,Any]]) -> Dict[str,Any]:
    rows=list(rows or [])
    available={}
    for src, cols in SOURCE_COLUMNS.items():
        present=[c for c in cols if any(c in r and r.get(c) not in (None, "") for r in rows)]
        available[src]=present
    return {"rows":len(rows),"available":available,"independent_sources":sum(bool(v) for v in available.values()),
            "missing_sources":[k for k,v in available.items() if not v]}

def parameter_stability(results: Iterable[Dict[str,Any]], score_key: str="net_expectancy_bps") -> Dict[str,Any]:
    rows=list(results or [])
    if not rows: return {"stable":False,"reason":"no_results"}
    scores=np.asarray([float(r.get(score_key,0.0)) for r in rows],dtype=float)
    finite=scores[np.isfinite(scores)]
    if len(finite)<5: return {"stable":False,"reason":"too_few_parameter_points","n":int(len(finite))}
    best=float(np.max(finite)); median=float(np.median(finite)); spread=float(np.std(finite))
    near=float(np.mean(finite >= best*0.75)) if best>0 else float(np.mean(finite>=0))
    # A strategy that only works at one sharp point is suspicious.
    stable=bool(best>0 and near>=0.20 and median>0 and spread < max(abs(best)*2.5,1e-9))
    return {"stable":stable,"n":int(len(finite)),"best":best,"median":median,"std":spread,"near_best_fraction":near}

def stressed_edge(expected_gross_bps: float, holding_hours: float, costs: CostModel, extra_stress: float=1.0) -> Dict[str,Any]:
    c=CostModel(costs.fee_bps,costs.slippage_bps,costs.funding_bps_per_8h,costs.impact_bps,costs.stress_multiplier*max(1.0,float(extra_stress)))
    total=c.total_bps(holding_hours)
    return {"gross_bps":float(expected_gross_bps),"cost_bps":float(total),"net_bps":float(expected_gross_bps-total),"viable":bool(expected_gross_bps>total)}

def meta_candidates(seed: int=2026):
    return {
        "logistic": LogisticRegression(max_iter=2000,class_weight="balanced",C=.35,random_state=seed),
        "histgb": HistGradientBoostingClassifier(max_iter=180,max_leaf_nodes=9,l2_regularization=2.5,learning_rate=.05,random_state=seed),
        "rf": RandomForestClassifier(n_estimators=180,max_depth=7,min_samples_leaf=12,class_weight="balanced_subsample",random_state=seed,n_jobs=-1),
    }

def select_meta_oof(X: np.ndarray, y: np.ndarray, folds: List[Tuple[np.ndarray,np.ndarray]], min_train: int=300) -> Tuple[Any,Dict[str,Any]]:
    X=np.asarray(X,float); y=np.asarray(y,int)
    candidates=meta_candidates(); scores={k:[] for k in candidates}; used=0
    for tr,va in folds:
        if len(tr)<min_train or len(va)<50 or len(np.unique(y[tr]))<3: continue
        for name,m in candidates.items():
            try:
                m.fit(X[tr],y[tr]); p=m.predict_proba(X[va]); scores[name].append(float(log_loss(y[va],p,labels=[0,1,2])))
            except Exception: pass
        used+=1
    means={k:float(np.mean(v)) for k,v in scores.items() if v}
    if not means: return None,{"enabled":False,"reason":"meta_no_valid_folds"}
    best_name=min(means,key=means.get); best=candidates[best_name]; best.fit(X,y)
    return best,{"enabled":True,"model":best_name,"folds":used,"logloss":means,"selection_uses_test":False}

def rolling_feature_drift(reference: np.ndarray, current: np.ndarray) -> float:
    a=np.asarray(reference,float); b=np.asarray(current,float)
    if a.size==0 or b.size==0: return 1.0
    am=np.nanmean(a,axis=0); bm=np.nanmean(b,axis=0); asd=np.nanstd(a,axis=0)+1e-9
    z=np.nanmean(np.abs((bm-am)/asd))
    return float(np.clip(z/3.0,0,1))
