"""ALPHA-X 10.0 autonomous research/operations control plane.
Policy-only: it can recommend/queue research work, but never silently promotes or
changes live risk limits. This keeps the production execution plane fail-closed.
"""
from __future__ import annotations
import math, time
from typing import Any, Dict, Iterable


def regime_bucket(ctx: Dict[str, Any] | None) -> str:
    c=ctx or {}
    regime=str(c.get("regime") or c.get("market_regime") or "UNKNOWN").upper()
    stress=float(c.get("stress") or 0.0)
    if stress >= 0.75: return "STRESS"
    if regime in {"TREND","TRENDING","BULL","BEAR"}: return "TREND"
    if regime in {"RANGE","MEAN_REVERSION","MEAN-REVERSION"}: return "RANGE"
    if regime in {"BREAKOUT","EXPANSION"}: return "BREAKOUT"
    return "UNKNOWN"


def regime_attribution(trades: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    buckets: Dict[str, Dict[str, Any]] = {}
    for t in trades or []:
        b=regime_bucket(t.get("market_context") or t.get("context"))
        pnl=float(t.get("net_pnl", t.get("pnl", 0)) or 0)
        x=buckets.setdefault(b,{"trades":0,"wins":0,"net_pnl":0.0,"gross_pnl":0.0})
        x["trades"]+=1; x["wins"]+=int(pnl>0); x["net_pnl"]+=pnl
        x["gross_pnl"]+=float(t.get("gross_pnl",pnl) or 0)
    for x in buckets.values():
        x["win_rate"]=x["wins"]/max(x["trades"],1)
    return {"buckets":buckets,"total_trades":sum(x["trades"] for x in buckets.values()),
            "generated_at":time.time()}


def execution_summary(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    vals=[]; lat=[]; fees=0.0
    for r in rows or []:
        try:
            v=float(r.get("implementation_shortfall_bps",0));
            if math.isfinite(v): vals.append(v)
        except Exception: pass
        try:
            v=float(r.get("fill_latency_ms_mean",0));
            if math.isfinite(v): lat.append(v)
        except Exception: pass
        fees += float(r.get("fees",0) or 0)
    vals.sort()
    return {"samples":len(vals),"mean_shortfall_bps":sum(vals)/len(vals) if vals else 0.0,
            "p50_shortfall_bps":vals[len(vals)//2] if vals else 0.0,
            "mean_fill_latency_ms":sum(lat)/len(lat) if lat else 0.0,"fees":fees}


def autonomous_decision(*, health: Dict[str,Any], governance: Dict[str,Any] | None=None,
                        retrain_required: Dict[str,Any] | None=None,
                        execution: Dict[str,Any] | None=None) -> Dict[str,Any]:
    """Return a conservative next action; never executes it."""
    reasons=[]; action="CONTINUE"
    if not health.get("healthy",True):
        action="DEGRADED"; reasons.extend(health.get("reasons") or [])
    elif retrain_required and retrain_required.get("required"):
        action="QUEUE_RETRAIN"; reasons.extend(retrain_required.get("reasons") or [])
    elif governance and governance.get("eligible"):
        action="PROMOTION_REVIEW"; reasons.append("statistical_governance_ready")
    if execution and abs(float(execution.get("mean_shortfall_bps",0)))>10:
        action="EXECUTION_REVIEW"; reasons.append("execution_cost_high")
    return {"action":action,"reasons":reasons,"fail_closed":action in {"DEGRADED","EXECUTION_REVIEW"},"ts":time.time()}
