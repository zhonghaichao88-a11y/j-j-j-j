"""Deterministic L2 order-book and tick replay primitives. Network-free."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Any
import math, hashlib, json

@dataclass
class Book:
    bids: dict[float,float]
    asks: dict[float,float]
    ts: int = 0
    def snapshot(self):
        bids=sorted(((p,s) for p,s in self.bids.items() if s>0), reverse=True)
        asks=sorted(((p,s) for p,s in self.asks.items() if s>0))
        return {'ts':self.ts,'bids':bids,'asks':asks}
    def top(self):
        b=max(self.bids) if self.bids else None; a=min(self.asks) if self.asks else None
        if b is None or a is None:return {'bid':b,'ask':a,'mid':None,'spread_bps':None}
        mid=(b+a)/2
        return {'bid':b,'ask':a,'mid':mid,'spread_bps':(a-b)/mid*10000 if mid else None}
    def imbalance(self, depth=5):
        b=sum(s for p,s in sorted(self.bids.items(),reverse=True)[:depth]); a=sum(s for p,s in sorted(self.asks.items())[:depth]); d=b+a
        return (b-a)/d if d else 0.0
    def apply(self, event:dict[str,Any]):
        self.ts=int(event.get('ts',self.ts)); side=str(event.get('side','')).lower(); p=float(event['price']); sz=float(event['size'])
        book=self.bids if side=='bid' else self.asks
        if sz<=0: book.pop(p,None)
        else: book[p]=sz

class L2Replay:
    def __init__(self, events:Iterable[dict[str,Any]]):
        self.events=sorted(list(events or []),key=lambda x:int(x.get('ts',0)))
    def run(self):
        book=Book({},{}); out=[]
        for e in self.events:
            book.apply(e); out.append({'ts':book.ts,'top':book.top(),'imbalance':book.imbalance(),'snapshot':book.snapshot()})
        return out
    def fingerprint(self):
        payload=json.dumps(self.events,sort_keys=True,separators=(',',':')).encode(); return hashlib.sha256(payload).hexdigest()[:32]

def validate_l2(events):
    issues=[]; prev=-1
    for e in events or []:
        ts=int(e.get('ts',0)); p=float(e.get('price',0)); s=float(e.get('size',-1)); side=str(e.get('side','')).lower()
        if ts<prev: issues.append('non_monotonic_timestamp')
        if p<=0 or not math.isfinite(p): issues.append('invalid_price')
        if s<0 or not math.isfinite(s): issues.append('invalid_size')
        if side not in ('bid','ask'): issues.append('invalid_side')
        prev=ts
    return {'ok':not issues,'issues':sorted(set(issues)),'events':len(list(events or []))}
