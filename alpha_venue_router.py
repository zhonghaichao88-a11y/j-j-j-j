"""Venue-neutral smart-order-routing decision primitives.
Does not submit orders. It scores venue quotes after explicit fees/slippage/latency/risk costs.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Any

@dataclass(frozen=True)
class Quote:
    venue: str
    bid: float
    ask: float
    fee_bps: float=0.0
    latency_ms: float=0.0
    impact_bps: float=0.0
    counterparty_bps: float=0.0

def route(quotes: List[Quote], side: str, risk_penalty_bps: float=0.0) -> Dict[str,Any]:
    if not quotes: return {"ok":False,"reason":"no_quotes"}
    side=side.lower()
    rows=[]
    for q in quotes:
        mid=(q.bid+q.ask)/2 if q.bid>0 and q.ask>0 else 0
        px=q.ask if side=="buy" else q.bid
        if px<=0: continue
        # Lower effective cost is better for both sides after costs.
        signed = px * (1 + (q.fee_bps+q.impact_bps+q.counterparty_bps+risk_penalty_bps)/10000) if side=="buy" else px * (1 - (q.fee_bps+q.impact_bps+q.counterparty_bps+risk_penalty_bps)/10000)
        rows.append((signed,q,mid))
    if not rows: return {"ok":False,"reason":"invalid_quotes"}
    rows.sort(key=lambda x:x[0], reverse=(side=="sell"))
    score,q,mid=rows[0]
    return {"ok":True,"venue":q.venue,"side":side,"effective_px":score,"mid":mid,"latency_ms":q.latency_ms,
            "ranked":[{"venue":x[1].venue,"effective_px":x[0]} for x in rows]}
