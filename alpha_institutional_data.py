"""ALPHA-X Institutional 1.0 data-quality and replay primitives.
Network-free: validates market bars before research/execution can consume them.
"""
from __future__ import annotations
import math, statistics, time
from typing import Any, Iterable

def _f(x, d=0.0):
    try:
        y=float(x); return y if math.isfinite(y) else d
    except Exception:return d

def validate_bars(rows: Iterable[dict[str,Any]], timeframe_minutes:int=15) -> dict[str,Any]:
    r=list(rows or []); issues=[]; gaps=[]; duplicates=0; prev=None
    for i,x in enumerate(r):
        ts=int(_f(x.get('ts'))); o,h,l,c,v=map(lambda k:_f(x.get(k)),('open','high','low','close','volume'))
        if prev is not None:
            dt=ts-prev
            if dt<=0: duplicates+=1; issues.append('non_monotonic_timestamp')
            elif dt>timeframe_minutes*60*1000*2: gaps.append({'from':prev,'to':ts,'bars_missing':max(0,round(dt/(timeframe_minutes*60*1000))-1)})
        if min(o,h,l,c)<0 or h<max(o,c) or l>min(o,c) or v<0: issues.append('invalid_ohlcv')
        prev=ts
    completeness=1.0 if not gaps else max(0.0,1-len(gaps)/max(len(r),1))
    return {'ok':not issues and duplicates==0,'rows':len(r),'duplicates':duplicates,'gaps':gaps[:50],
            'completeness':completeness,'issues':sorted(set(issues)),'checked_at':time.time()}

def stale(now_ms:int,last_ts:int,max_age_ms:int)->bool:return now_ms-last_ts>max_age_ms

def replay_fingerprint(rows: Iterable[dict[str,Any]])->str:
    import hashlib, json
    payload=[]
    for x in rows:
        payload.append([int(_f(x.get('ts'))),_f(x.get('open')),_f(x.get('high')),_f(x.get('low')),_f(x.get('close')),_f(x.get('volume'))])
    return hashlib.sha256(json.dumps(payload,separators=(',',':')).encode()).hexdigest()[:24]
