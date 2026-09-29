"""ALPHA-X 10.0 execution-quality analytics. Network-free and safe to run in paper/live."""
from __future__ import annotations
import math, statistics, time
from typing import Any, Dict, Iterable

def _finite(v, d=0.0):
    try:
        x=float(v); return x if math.isfinite(x) else d
    except Exception: return d

def execution_quality(expected_px: float, fills: Iterable[Dict[str,Any]], side: str, qty: float, arrival_px: float|None=None) -> Dict[str,Any]:
    rows=list(fills or []); q=max(_finite(qty),0.0); exp=_finite(expected_px); arr=_finite(arrival_px,exp)
    fq=sum(max(_finite(r.get('size') or r.get('fillSz')),0) for r in rows)
    if fq<=0: fq=q
    avg=sum(_finite(r.get('px') or r.get('fillPx'))*max(_finite(r.get('size') or r.get('fillSz')),0) for r in rows)/fq if rows and fq else exp
    signed=(avg-exp)/max(exp,1e-12)*(1 if side=='long' else -1)
    arrival=(avg-arr)/max(arr,1e-12)*(1 if side=='long' else -1)
    fees=sum(_finite(r.get('fee')) for r in rows)
    latency_ms=[]
    for r in rows:
        if r.get('inTime') and r.get('fillTime'):
            latency_ms.append(max(0,(_finite(r['fillTime'])-_finite(r['inTime']))/1000))
    return {'expected_px':exp,'arrival_px':arr,'avg_fill_px':avg,'filled_qty':fq,'implementation_shortfall_bps':signed*10000,'arrival_slippage_bps':arrival*10000,'fees':fees,'fills':len(rows),'fill_latency_ms_mean':statistics.mean(latency_ms) if latency_ms else 0.0,'ts':time.time()}

def quality_grade(q:Dict[str,Any])->str:
    b=abs(_finite(q.get('implementation_shortfall_bps')))
    if b<=2:return 'A'
    if b<=5:return 'B'
    if b<=10:return 'C'
    return 'D'
