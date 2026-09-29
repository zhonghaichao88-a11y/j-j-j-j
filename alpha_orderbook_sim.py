"""Deterministic L2 execution simulator for research/replay; never routes live orders."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Iterable
import math
from alpha_l2_replay import Book

@dataclass
class Fill:
    ts:int; side:str; qty:float; price:float; fee:float; slippage_bps:float

class OrderBookSimulator:
    def __init__(self, fee_bps=5.0, impact_bps_per_participation=20.0):
        self.fee_bps=max(float(fee_bps),0); self.impact=max(float(impact_bps_per_participation),0)
    def market(self, book:Book, side:str, qty:float, participation:float=0.0)->Fill:
        q=max(float(qty),0); s=str(side).lower(); top=book.top(); mid=top['mid']
        if q<=0 or mid is None: return Fill(book.ts,s,0,0,0,0)
        px=top['ask'] if s=='buy' else top['bid']
        levels=sorted(book.asks.items()) if s=='buy' else sorted(book.bids.items(),reverse=True)
        rem=q; value=0.0
        for p,sz in levels:
            take=min(rem,max(float(sz),0)); value+=take*p; rem-=take
            if rem<=1e-12: break
        filled=q-rem
        if filled<=0:return Fill(book.ts,s,0,0,0,0)
        avg=value/filled
        impact=abs(float(participation))*self.impact
        avg *= 1 + (impact/10000 if s=='buy' else -impact/10000)
        slip=abs(avg-mid)/mid*10000
        fee=avg*filled*self.fee_bps/10000
        return Fill(book.ts,s,filled,avg,fee,slip)

def replay_market(events:Iterable[dict[str,Any]], orders:Iterable[dict[str,Any]], fee_bps=5.0):
    ev=sorted(list(events or []),key=lambda x:int(x.get('ts',0))); od=sorted(list(orders or []),key=lambda x:int(x.get('ts',0)))
    book=Book({},{}); out=[]; j=0; sim=OrderBookSimulator(fee_bps)
    for e in ev:
        book.apply(e)
        while j<len(od) and int(od[j].get('ts',0))<=book.ts:
            o=od[j]; f=sim.market(book,o.get('side','buy'),float(o.get('qty',0)),float(o.get('participation',0))); out.append(f.__dict__); j+=1
    return out
