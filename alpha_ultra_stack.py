"""ALPHA-X ULTRA MAX: advanced market context, portfolio risk, drift and execution planning.
Optional data sources fail closed: missing external data never creates a trade by itself.
"""
from __future__ import annotations
import math, time
from dataclasses import dataclass
from typing import Dict, Any, Optional, List
import numpy as np

@dataclass
class MarketContext:
    spread_pct: Optional[float] = None
    book_imbalance: Optional[float] = None
    funding_rate: Optional[float] = None
    open_interest: Optional[float] = None
    oi_change_pct: Optional[float] = None
    regime: str = "UNKNOWN"
    stress: float = 0.0
    no_trade: bool = False
    reasons: List[str] = None
    def as_dict(self):
        return {"spread_pct":self.spread_pct,"orderbook_imbalance":self.book_imbalance,
                "funding_rate":self.funding_rate,"open_interest":self.open_interest,
                "oi_change_pct":self.oi_change_pct,"regime":self.regime,
                "stress":self.stress,"no_trade":self.no_trade,"reasons":self.reasons or []}

def regime_from_features(trend: float, vol_regime: float, atr: float) -> str:
    if not np.isfinite([trend, vol_regime, atr]).all(): return "UNKNOWN"
    if atr >= 0.05 or vol_regime >= 2.8: return "EXTREME"
    if atr >= 0.025 or vol_regime >= 1.8: return "STRESS"
    if abs(trend) >= 0.02: return "TREND"
    if abs(trend) <= 0.004 and vol_regime <= 1.2: return "RANGE"
    return "NORMAL"

def build_context(symbol: str, feature_row: Dict[str, Any], exchange=None) -> MarketContext:
    ctx=MarketContext(reasons=[])
    ctx.regime=regime_from_features(float(feature_row.get("trend_score",0) or 0),
                                     float(feature_row.get("vol_regime",1) or 1),
                                     float(feature_row.get("atr14",0) or 0))
    if ctx.regime=="EXTREME":
        ctx.stress=1.0; ctx.no_trade=True; ctx.reasons.append("EXTREME_VOLATILITY")
    elif ctx.regime=="STRESS": ctx.stress=.65
    if exchange is not None:
        try:
            cs=symbol
            book=exchange.fetch_order_book(cs, limit=20)
            bids=book.get("bids") or []; asks=book.get("asks") or []
            if bids and asks:
                bid=float(bids[0][0]); ask=float(asks[0][0]); mid=(bid+ask)/2
                ctx.spread_pct=(ask-bid)/max(mid,1e-12)
                bv=sum(float(x[1]) for x in bids[:10]); av=sum(float(x[1]) for x in asks[:10])
                ctx.book_imbalance=(bv-av)/max(bv+av,1e-12)
                if ctx.spread_pct>=.0045:
                    ctx.no_trade=True; ctx.reasons.append("WIDE_SPREAD_HARD")
                elif ctx.spread_pct>.0025:
                    ctx.reasons.append("WIDE_SPREAD_RISK_REDUCED")
        except Exception as exc: ctx.reasons.append("ORDERBOOK_UNAVAILABLE")
        try:
            fr=exchange.fetch_funding_rate(cs)
            if fr: ctx.funding_rate=float(fr.get("fundingRate")) if fr.get("fundingRate") is not None else None
        except Exception: ctx.reasons.append("FUNDING_UNAVAILABLE")
        try:
            oi=exchange.fetch_open_interest(cs)
            if oi:
                val=oi.get("openInterestValue") or oi.get("openInterest")
                ctx.open_interest=float(val) if val is not None else None
        except Exception: ctx.reasons.append("OI_UNAVAILABLE")
    # Extreme positioning filter: funding alone never forces a trade; it can only block obvious crowding.
    if ctx.funding_rate is not None and abs(ctx.funding_rate)>=0.0020 and ctx.regime in ("STRESS","EXTREME"):
        ctx.no_trade=True; ctx.reasons.append("EXTREME_FUNDING")
    return ctx

def portfolio_weights(expected: Dict[str,float], vol: Dict[str,float], corr: Optional[np.ndarray]=None,
                      max_weight=.25, min_weight=0.0) -> Dict[str,float]:
    """Conservative inverse-volatility portfolio allocator with correlation penalty.
    It is deliberately bounded and deterministic; if no covariance matrix is supplied,
    inverse-volatility is used rather than pretending correlations are known.
    """
    keys=list(expected)
    if not keys:return {}
    raw=np.array([max(float(expected[k]),0.0) for k in keys])
    vols=np.array([max(float(vol.get(k,1.0)),1e-6) for k in keys])
    score=raw/vols
    if corr is not None and corr.shape==(len(keys),len(keys)):
        c=np.clip(np.nan_to_num(corr,nan=0.0),-1,1); np.fill_diagonal(c,1)
        score=score/(1+np.maximum(c.sum(axis=1)-1,0))
    if score.sum()<=0: score=np.ones(len(keys))
    w=score/score.sum()
    w=np.minimum(w,max_weight)
    if w.sum()>0:w=w/w.sum()
    return {k:float(max(min_weight,w[i])) for i,k in enumerate(keys)}

def drift_score(reference: np.ndarray, current: np.ndarray) -> float:
    """Simple robust PSI-like drift score based on standardized mean/variance shifts."""
    a=np.asarray(reference,dtype=float); b=np.asarray(current,dtype=float)
    a=a[np.isfinite(a)]; b=b[np.isfinite(b)]
    if len(a)<20 or len(b)<20:return 0.0
    return float(min(10.0, abs(np.mean(b)-np.mean(a))/(np.std(a)+1e-12)+
                    abs(np.log((np.std(b)+1e-9)/(np.std(a)+1e-9)))))

def execution_plan(notional: float, spread_pct: Optional[float], volatility: float,
                   urgency: float=0.5) -> Dict[str,Any]:
    """Choose a safe execution style. Large orders are split; each child remains independently protected by caller."""
    n=float(notional); sp=float(spread_pct or 0); vol=float(volatility or 0); u=min(max(float(urgency),0),1)
    if n<=1000 and sp<=.0015: return {"style":"MARKET","slices":1,"interval_sec":0}
    if sp>.004: return {"style":"DEFER","slices":0,"interval_sec":0,"reason":"spread_too_wide"}
    if n<=5000 and u>.75: return {"style":"AGGRESSIVE_MARKET","slices":2,"interval_sec":2}
    slices=max(2,min(10,int(math.ceil(n/2500))))
    if vol>.03: slices=min(slices,4)
    return {"style":"TWAP_SPLIT","slices":slices,"interval_sec":max(2,int(8*(1-u))),"reason":"liquidity_aware_split"}

def stress_multiplier(regime: str) -> float:
    return {"EXTREME":0.0,"STRESS":0.35,"TREND":1.0,"RANGE":0.65,"NORMAL":1.0}.get(regime,0.5)
