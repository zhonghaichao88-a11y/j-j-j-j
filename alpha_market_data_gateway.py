"""Institutional 12.0 market-data gateway primitives.
Network-free core: normalize tick/L2 events, sequence/checksum-style integrity,
watermarking, gap detection, and deterministic replay envelopes.
"""
from __future__ import annotations
import hashlib, json, math, time
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional

VERSION = "ALPHA-X-INSTITUTIONAL-12.0"

def f(x, d=0.0):
    try:
        v=float(x); return v if math.isfinite(v) else d
    except Exception: return d

def event_id(*parts: Any) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:32]

@dataclass(frozen=True)
class MarketEvent:
    venue: str
    channel: str
    instrument: str
    ts_exchange: int
    ts_local: int
    seq: int
    kind: str
    payload_hash: str
    event_key: str

def make_event(venue: str, channel: str, instrument: str, ts_exchange: int,
               seq: int, kind: str, payload: Dict[str, Any], ts_local: Optional[int] = None) -> MarketEvent:
    raw=json.dumps(payload, sort_keys=True, separators=(",",":"), ensure_ascii=False)
    ph=hashlib.sha256(raw.encode()).hexdigest()
    local=int(time.time()*1000) if ts_local is None else int(ts_local)
    return MarketEvent(venue,channel,instrument,int(ts_exchange),local,int(seq),kind,ph,
                       event_id(venue,channel,instrument,ts_exchange,seq,ph))

class SequenceGuard:
    def __init__(self): self.last: Dict[str,int]={}
    def accept(self, stream: str, seq: int) -> Dict[str,Any]:
        seq=int(seq); prev=self.last.get(stream)
        if prev is not None and seq <= prev: return {"ok":False,"reason":"duplicate_or_out_of_order","prev":prev,"seq":seq}
        gap = None if prev is None else seq-prev-1
        self.last[stream]=seq
        return {"ok":gap in (None,0),"gap":gap,"prev":prev,"seq":seq,"reason":"gap" if gap else "ok"}

class Watermark:
    def __init__(self, max_lag_ms: int=2000): self.max_lag_ms=int(max_lag_ms); self.last_exchange_ts=None
    def update(self, ts_exchange: int, now_ms: Optional[int]=None) -> Dict[str,Any]:
        now=int(time.time()*1000) if now_ms is None else int(now_ms)
        self.last_exchange_ts=int(ts_exchange); lag=now-self.last_exchange_ts
        return {"ok":lag <= self.max_lag_ms,"lag_ms":lag,"max_lag_ms":self.max_lag_ms}
