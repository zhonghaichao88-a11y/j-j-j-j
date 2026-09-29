"""Deterministic event-sourced replay for recovery and audit testing."""
from __future__ import annotations
import hashlib,json

def canonical(e):
    return json.dumps(e,sort_keys=True,separators=(',',':'),ensure_ascii=False)

def replay(events):
    state={'orders':{},'positions':{},'last_seq':0}
    for e in sorted(events or [],key=lambda x:int(x.get('seq',0))):
        seq=int(e.get('seq',0))
        if seq<=state['last_seq']: continue
        typ=str(e.get('type','')).upper(); key=str(e.get('key',''))
        if typ in ('ORDER_SUBMITTED','ORDER_FILLED','ORDER_CLOSED'):
            state['orders'][key]={**state['orders'].get(key,{}),**e}
        elif typ=='POSITION_UPSERT':
            state['positions'][key]={**state['positions'].get(key,{}),**e}
        elif typ=='POSITION_FLAT':
            state['positions'].pop(key,None)
        state['last_seq']=seq
    return state

def chain(events):
    prev='0'*64; out=[]
    for e in sorted(events or [],key=lambda x:int(x.get('seq',0))):
        body=canonical(e)
        h=hashlib.sha256((prev+body).encode()).hexdigest(); out.append({'seq':int(e.get('seq',0)),'hash':h}); prev=h
    return out
