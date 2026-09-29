"""ALPHA-X 10.0 operational watchdog and degraded-mode policy."""
from __future__ import annotations
import time
from typing import Any,Dict

def evaluate(status:Dict[str,Any], now:float|None=None)->Dict[str,Any]:
    now=time.time() if now is None else now; reasons=[]
    if status.get('mode')=='live' and not status.get('running'): reasons.append('engine_not_running')
    ws=status.get('ws') or {}; running=bool(ws.get('running')); transport_age=float(ws.get('transport_age', ws.get('age_sec', 0)) or 0)
    if status.get('mode')=='live' and (not running or transport_age>45): reasons.append('private_ws_transport_stale')
    if int(status.get('error_count') or 0)>=5: reasons.append('repeated_errors')
    return {'healthy':not reasons,'degraded':bool(reasons),'reasons':reasons,'ts':now}
