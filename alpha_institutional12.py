"""ALPHA-X Institutional 12.0: production market-data, capacity and routing kernel."""
from __future__ import annotations
from typing import Any, Dict, List
from alpha_market_data_gateway import SequenceGuard, Watermark, make_event
from alpha_capacity import CapacityModel, canary_gate
from alpha_venue_router import Quote, route

VERSION="ALPHA-X-INSTITUTIONAL-12.0"

def readiness(*, market_data_ok: bool, clock_ok: bool, risk_ok: bool, reconciliation_ok: bool,
              canary_ok: bool, queue_depth: int, max_queue: int=100) -> Dict[str,Any]:
    checks={"market_data":bool(market_data_ok),"clock":bool(clock_ok),"risk":bool(risk_ok),
            "reconciliation":bool(reconciliation_ok),"canary":bool(canary_ok),"queue":int(queue_depth)<=max_queue}
    return {"version":VERSION,"ready":all(checks.values()),"checks":checks}
