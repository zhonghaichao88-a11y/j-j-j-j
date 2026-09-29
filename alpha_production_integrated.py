"""ALPHA-X Institutional 15.0 Integrated Production Kernel.

This module is intentionally an orchestration layer: institutional components must
change the decision, size, execution route, or permission to trade. It never invents
market data and never grants live permission by itself.
"""
from __future__ import annotations
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from alpha_institutional_data import validate_bars, replay_fingerprint
from alpha_market_data_gateway import make_event, SequenceGuard, Watermark
from alpha_market_impact import estimate as impact_estimate
from alpha_capacity import CapacityModel
from alpha_smart_execution import plan as execution_plan
from alpha_venue_router import Quote, route
from alpha_portfolio_optimizer import optimize as portfolio_optimize, portfolio_stats
from alpha_risk_kernel import gate as risk_gate
from alpha_production15 import EpochFence, production_gate15, canary_gate15

VERSION = "ALPHA-X-INSTITUTIONAL-15.0-INTEGRATED-PARAM-CALIBRATED-V5"
ROOT = Path(__file__).parent
FENCE = EpochFence(ROOT / "alpha_execution_fence.json")


def _gate(cfg, name: str) -> bool:
    try:
        return bool((cfg.get("gate_switches") or {}).get(name, True))
    except Exception:
        return True


def _finite(x, default=0.0):
    try:
        v=float(x)
        return v if v == v and abs(v) != float("inf") else default
    except Exception:
        return default


def _normalize_rows(candles: Iterable[Any]) -> list[dict]:
    rows=[]
    for x in list(candles or []):
        if isinstance(x, dict):
            rows.append(x)
        elif isinstance(x, (list, tuple)) and len(x) >= 6:
            rows.append({"ts":x[0],"open":x[1],"high":x[2],"low":x[3],"close":x[4],"volume":x[5]})
    return rows

def market_quality(candles: Iterable[Any], timeframe_minutes: int = 15, now_ms: Optional[int] = None) -> Dict[str, Any]:
    rows=_normalize_rows(candles)
    quality=validate_bars(rows, timeframe_minutes=timeframe_minutes)
    if rows:
        last_ts=int(_finite(rows[-1].get("ts")))
        now=int(time.time()*1000) if now_ms is None else int(now_ms)
        lag=now-last_ts
        quality["fresh"] = lag <= max(2*timeframe_minutes*60_000, 120_000)
        quality["lag_ms"] = lag
        quality["fingerprint"] = replay_fingerprint(rows[-500:])
    else:
        quality.update(fresh=False, lag_ms=None, fingerprint="")
    quality["ok"] = bool(quality.get("ok") and quality.get("fresh"))
    return quality


def execution_quote(symbol: str, ticker: Dict[str, Any], fee_bps: float, impact_bps: float,
                    latency_ms: float = 0.0) -> Dict[str, Any]:
    bid=_finite(ticker.get("bid")); ask=_finite(ticker.get("ask")); last=_finite(ticker.get("last"))
    if bid<=0: bid=last
    if ask<=0: ask=last
    if bid<=0 or ask<=0 or ask<bid:
        return {"ok":False,"reason":"invalid_quote"}
    return route([Quote("OKX",bid,ask,fee_bps=float(fee_bps),latency_ms=float(latency_ms),impact_bps=float(impact_bps))], "buy")


def pretrade_pipeline(*, symbol: str, side: str, candles: Iterable[dict], ticker: Dict[str, Any],
                      equity: float, free: float, requested_notional: float, stop_pct: float,
                      confidence: float, stress: float, cfg: Dict[str, Any], live: bool,
                      health_ok: bool = True, reconciliation_ok: bool = True,
                      model_ok: bool = True) -> Dict[str, Any]:
    """Single pre-trade path. Every returned gate can block or resize the order."""
    rows=_normalize_rows(candles)
    quality=market_quality(rows, timeframe_minutes=15)
    if not _gate(cfg, "market_quality"):
        quality = {**quality, "ok": True, "reason": "DISABLED_BY_UI"}
    returns=[]
    closes=[_finite(r.get("close")) for r in rows]
    for a,b in zip(closes[:-1], closes[1:]):
        if a>0 and b>0: returns.append(b/a-1)
    gross_exposure=0.0
    risk=risk_gate([max(_finite(equity),0)], returns[-500:], gross_exposure,
                   max_gross=float(cfg.get("risk_gross_limit",1.0)),
                   max_dd=float(cfg.get("risk_dd_limit",0.15)),
                   max_cvar=float(cfg.get("risk_cvar_soft_limit", cfg.get("risk_cvar_limit",0.05))))
    # Opportunity-preserving CVaR handling:
    # - CVaR at/below the soft limit: no size penalty.
    # - CVaR above the soft limit but below the hard limit: reduce this order's size
    #   continuously instead of blocking a valid signal.
    # - CVaR at/above the hard limit: retain the hard safety block.
    # The existing gross-exposure and drawdown reasons remain hard blocks.
    cvar=float((risk.get("risk") or {}).get("cvar",0.0) or 0.0)
    cvar_soft=max(float(cfg.get("risk_cvar_soft_limit", cfg.get("risk_cvar_limit",0.05))),0.0)
    cvar_hard=max(float(cfg.get("risk_cvar_hard_limit",0.12)), cvar_soft + 1e-9)
    cvar_multiplier=1.0
    cvar_mode="正常"
    if cvar >= cvar_hard:
        cvar_multiplier=0.0
        cvar_mode="极端风险·禁止开仓"
    elif cvar > cvar_soft:
        progress=(cvar-cvar_soft)/max(cvar_hard-cvar_soft,1e-9)
        cvar_multiplier=max(0.35, 1.0-0.65*progress)
        cvar_mode="风险偏高·降仓"
    risk_reasons=list(risk.get("reasons") or [])
    non_cvar_reasons=[r for r in risk_reasons if str(r) != "cvar"]
    # CVaR soft exceedance is deliberately not a hard gate. Other risk-kernel
    # reasons (gross exposure/drawdown) remain hard safety blocks.
    risk_ok=(not non_cvar_reasons and cvar < cvar_hard) if _gate(cfg, "risk_budget") else True
    risk={**risk, "cvar":cvar, "cvar_soft_limit":cvar_soft, "cvar_hard_limit":cvar_hard,
          "cvar_multiplier":cvar_multiplier, "cvar_mode":cvar_mode,
          "hard_reasons":non_cvar_reasons + (["cvar_hard"] if cvar >= cvar_hard else [])}

    px=_finite(ticker.get("last") or ticker.get("ask") or ticker.get("bid"))
    if px<=0: return {"ready":False,"reason":"invalid_price","quality":quality,"risk":risk}
    # ADV must come from real observed volume. Never manufacture liquidity from the requested order size.
    volume_quote=sum(max(_finite(r.get("volume")),0)*max(_finite(r.get("close")),0) for r in rows[-96:])
    adv=max(volume_quote/4.0, 0.0)
    spread_bps=max(((_finite(ticker.get("ask"))- _finite(ticker.get("bid")))/px*10000),0.0)
    impact=impact_estimate(requested_notional, max(adv,1e-9), spread_bps,
                           max(_finite(cfg.get("volatility_hint",0.01)),0.001))
    cap=CapacityModel(adv_usd=adv, max_participation=float(cfg.get("max_participation",0.08)), impact_bps=max(impact["impact_bps"],0.1))
    cap_row=cap.estimate(requested_notional)

    # Liquidity and capacity are independent gates. Liquidity answers "is there
    # enough real market activity?"; capacity answers "is this order small
    # enough relative to that activity?".
    min_liq_multiple=max(float(cfg.get("min_liquidity_multiple",5.0)),1.0)
    liquidity_ok=(adv >= requested_notional*min_liq_multiple) if requested_notional>0 else adv>0
    if not _gate(cfg, "liquidity"):
        liquidity_ok=True
    capacity_ok_raw=bool(cap_row.get("within_capacity",False))
    capacity_ok=capacity_ok_raw if _gate(cfg, "capacity") else True

    impact_bps=float(impact.get("all_in_cost_bps", impact.get("impact_bps",0.0)) or 0.0)
    max_impact_bps=max(float(cfg.get("max_impact_bps",30.0)),0.0)
    impact_ok=(impact_bps <= max_impact_bps) if _gate(cfg, "impact_cost") else True

    # Route only across venues for which a real quote exists. The current live adapter
    # supplies OKX, so we never fabricate a competing venue.
    rr=route([Quote("OKX",_finite(ticker.get("bid")),_finite(ticker.get("ask")),
                     fee_bps=float(cfg.get("fee_bps",5.0)),impact_bps=impact_bps,
                     latency_ms=float(cfg.get("execution_latency_ms",0.0)))], side)
    route_ok=bool(rr.get("ok")) if _gate(cfg, "execution") else True
    ex=execution_plan(requested_notional, px, adv, max(((_finite(ticker.get("ask"))- _finite(ticker.get("bid")))/px*10000),0),
                       max(_finite(cfg.get("volatility_hint",0.01)),0.001),
                       duration_s=float(cfg.get("execution_duration_s",60)), slices=int(cfg.get("execution_slices",4)),
                       max_participation=float(cfg.get("max_participation",0.08)),
                       urgency=float(cfg.get("execution_urgency",0.85)))
    capacity_limit=float(cap_row.get("capacity_usd",0.0)) if _gate(cfg,"capacity") else max(float(requested_notional),0.0)
    planned=min(float(ex["planned_notional"]), capacity_limit, max(float(free),0)*float(cfg.get("leverage",3))*0.80)
    # Apply CVaR as one explicit risk-sizing action. This replaces the previous
    # "CVaR over 5% => immediate BLOCK" behavior for moderate tail risk.
    pre_cvar_planned=planned
    if _gate(cfg, "risk_budget") and cvar_multiplier < 1.0:
        planned *= cvar_multiplier
    if _gate(cfg, "liquidity") and not liquidity_ok: planned=0.0
    if _gate(cfg, "capacity") and not capacity_ok: planned=0.0
    if _gate(cfg, "impact_cost") and not impact_ok: planned=0.0
    # Opportunity-preserving risk scaling:
    # mild confidence/stress deterioration reduces size instead of hard-blocking;
    # hard floors remain in force to protect live capital.
    tier=str(cfg.get("signal_tier","NORMAL") or "NORMAL").upper()
    conf_floor=float(cfg.get("min_execution_confidence",0.50))
    conf_hard=float(cfg.get("hard_execution_confidence",0.44))
    if tier=="TRIAL":
        # Trial entries are deliberately tiny, but still require a real confidence floor.
        conf_hard=float(cfg.get("trial_hard_confidence",0.26))
        conf_floor=float(cfg.get("trial_execution_confidence",0.33))
    if _gate(cfg, "confidence") and tier not in ("FAST", "一般", "强", "很强"):
        if confidence < conf_hard: planned=0.0
        elif confidence < conf_floor:
            planned *= max(0.10 if tier=="TRIAL" else 0.25, min(1.0, (confidence-conf_hard)/max(conf_floor-conf_hard,1e-9)))
    stress_hard=float(cfg.get("hard_execution_stress",0.985))
    stress_soft=float(cfg.get("max_execution_stress",0.92))
    if _gate(cfg, "stress"):
        if stress >= stress_hard: planned=0.0
        elif stress > stress_soft:
            planned *= max(0.20, 1.0-(stress-stress_soft)/max(stress_hard-stress_soft,1e-9)*0.80)
    spread_pct=max(((_finite(ticker.get("ask"))- _finite(ticker.get("bid")))/px),0.0)
    spread_soft=float(cfg.get("spread_risk_soft",0.0025))
    spread_hard=float(cfg.get("spread_risk_hard",0.0045))
    if _gate(cfg, "spread"):
        if spread_pct >= spread_hard: planned=0.0
        elif spread_pct > spread_soft:
            planned *= max(0.20, 1.0-(spread_pct-spread_soft)/max(spread_hard-spread_soft,1e-9)*0.80)
    # Portfolio optimizer is used as a sizing governor, not as a decorative metric.
    expected={symbol: max(0.0, float(confidence))}
    cov={symbol:{symbol:max(float(stop_pct),0.002)**2}}
    ow=portfolio_optimize(expected,cov,max_weight=float(cfg.get("max_symbol_weight",1.0)),gross_limit=float(cfg.get("portfolio_gross_limit",1.0)))
    weight=float(ow.get(symbol,0.0)) if _gate(cfg, "portfolio_weight") else 1.0
    planned*=max(0.0,min(1.0,weight))
    stats=portfolio_stats(ow,cov)

    gate=production_gate15(
        market_data_ok=quality["ok"] if _gate(cfg,"market_quality") else True,
        clock_ok=bool(cfg.get("clock_ok",True)) if _gate(cfg,"clock") else True,
        risk_ok=risk_ok if _gate(cfg,"risk_budget") else True,
        reconciliation_ok=reconciliation_ok if _gate(cfg,"reconciliation") else True,
        execution_ok=(route_ok and planned>0) if _gate(cfg,"execution") else True,
        ha_ok=bool(cfg.get("ha_ok",True)) if _gate(cfg,"ha_fencing") else True,
        audit_ok=bool(cfg.get("audit_ok",True)) if _gate(cfg,"audit") else True,
        recovery_ok=bool(health_ok) if _gate(cfg,"recovery_health") else True,
        model_ok=model_ok if _gate(cfg,"model_gate") else True,
        kill_switch=False)
    pre_checks={"liquidity":bool(liquidity_ok),"capacity":bool(capacity_ok),"impact_cost":bool(impact_ok),
                "reconciliation":bool(reconciliation_ok),"clock":bool(cfg.get("clock_ok",True)),
                "audit":bool(cfg.get("audit_ok",True)),"recovery_health":bool(health_ok)}
    ready=bool(gate["ready"] and planned>0 and liquidity_ok and capacity_ok and impact_ok)
    disabled=[k for k in (cfg.get("gate_switches") or {}) if not _gate(cfg,k)]
    return {"ready":ready,"version":VERSION,"symbol":symbol,"side":side,
            "requested_notional":float(requested_notional),"planned_notional":float(planned),
            "price":px,"quality":quality,"risk":risk,
            "risk_sizing":{"cvar_mode":cvar_mode,"cvar":cvar,"soft_limit":cvar_soft,
                           "hard_limit":cvar_hard,"multiplier":cvar_multiplier,
                           "before_notional":float(pre_cvar_planned),"after_notional":float(planned)},
            "liquidity":{"ok":bool(liquidity_ok),"adv_usdt":float(adv),"min_multiple":float(min_liq_multiple)},
            "capacity":{**cap_row,"ok":bool(capacity_ok)},
            "impact":{**impact,"impact_ok":bool(impact_ok),"max_impact_bps":float(max_impact_bps),"all_in_cost_bps":float(impact_bps)},
            "route":rr,"execution":ex,"portfolio":{"weights":ow,"stats":stats},
            "pre_checks":pre_checks,"gate":gate,"gate_switches":dict(cfg.get("gate_switches") or {}),"disabled_gates":disabled,"reason":"READY" if ready else "INTEGRATED_GATE_BLOCK"}


def issue_execution_fence(owner: str) -> Dict[str, Any]:
    token=FENCE.issue(owner)
    return {"epoch":token.epoch,"owner":token.owner,"issued_at":token.issued_at,"ttl_sec":token.ttl_sec}


def authorize_execution(token_dict: Dict[str, Any]) -> bool:
    from alpha_production15 import FencingToken
    try: return FENCE.authorize(FencingToken(**token_dict))
    except Exception: return False

def renew_execution_fence(token_dict: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    from alpha_production15 import FencingToken
    try:
        renewed=FENCE.renew(FencingToken(**token_dict))
        if not renewed: return None
        return {"epoch":renewed.epoch,"owner":renewed.owner,"issued_at":renewed.issued_at,"ttl_sec":renewed.ttl_sec}
    except Exception:
        return None


def integrated_canary(**kwargs) -> Dict[str, Any]:
    return canary_gate15(**kwargs)
