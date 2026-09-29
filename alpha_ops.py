"""ALPHA-X ULTRA MAX 5.5 execution state machine and circuit breakers.
Designed to make every live order auditable, idempotent, and recoverable.
"""
from __future__ import annotations
import hashlib, json, os, threading, time
from pathlib import Path
from typing import Any, Dict, Optional

ROOT=Path(__file__).parent
OPS_FILE=ROOT/"alpha_ops_state.json"
LOCK=threading.RLock()
STATES=("NEW","SUBMITTED","FILLED","PROTECTED","CLOSING","CLOSED","ERROR","RECONCILED")


def _default_state():
    return {"version":1,"orders":{},"breaker":{"open":False,"reason":"","at":None}}

def _load():
    """读取持久化执行状态；任何合法但非对象的 JSON 都安全降级，绝不让 NoneType.get 贯穿启动链路。"""
    if not OPS_FILE.exists():
        return _default_state()
    try:
        data=json.loads(OPS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _default_state()
        orders=data.get("orders")
        breaker=data.get("breaker")
        if not isinstance(orders, dict):
            data["orders"]={}
        if not isinstance(breaker, dict):
            data["breaker"]={"open":False,"reason":"","at":None}
        else:
            breaker.setdefault("open",False); breaker.setdefault("reason",""); breaker.setdefault("at",None)
        data.setdefault("version",1)
        return data
    except Exception:
        return _default_state()

def _save(x):
    tmp=OPS_FILE.with_suffix('.tmp'); tmp.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(OPS_FILE)

def client_id(symbol:str, side:str, bucket:Optional[int]=None, intent_key:Optional[str]=None)->str:
    """Deterministic lifecycle ID. Prefer a persisted intent_key over process/time entropy."""
    if intent_key:
        raw=f"ALPHAX|{symbol}|{side}|{intent_key}".encode()
    else:
        b=int(time.time()//60 if bucket is None else bucket)
        raw=f"ALPHAX|{symbol}|{side}|{b}".encode()
    return "AX"+hashlib.sha256(raw).hexdigest()[:28]

def upsert_intent(intent_id:str, payload:Dict[str,Any]):
    """Persist an order intent before exchange I/O; idempotent across restarts."""
    with LOCK:
        x=_load(); rec=x["orders"].setdefault(intent_id,{"created_at":time.time(),"events":[]})
        rec["intent"]={**rec.get("intent",{}),**payload}; rec["updated_at"]=time.time(); x["orders"][intent_id]=rec; _save(x)
        return rec

def open_orders():
    with LOCK: return _load().get("orders",{})

def record(order_id:str, state:str, payload:Dict[str,Any]|None=None):
    if state not in STATES: raise ValueError(state)
    with LOCK:
        x=_load(); rec=x["orders"].setdefault(order_id,{"created_at":time.time(),"events":[]})
        rec["state"]=state; rec["updated_at"]=time.time(); rec["events"].append({"at":time.time(),"state":state,"payload":payload or {}})
        rec["events"]=rec["events"][-50:]; x["orders"][order_id]=rec
        if len(x["orders"])>1000:
            keys=sorted(x["orders"],key=lambda k:x["orders"][k].get("updated_at",0));
            for k in keys[:-800]: x["orders"].pop(k,None)
        _save(x)
    return rec

def get(order_id:str):
    with LOCK: return _load().get("orders",{}).get(order_id)

def open_breaker(reason:str):
    with LOCK:
        x=_load(); x["breaker"]={"open":True,"reason":str(reason),"at":time.time()}; _save(x)

def close_breaker():
    with LOCK:
        x=_load(); x["breaker"]={"open":False,"reason":"","at":None}; _save(x)

def breaker_status():
    with LOCK: return _load().get("breaker",{"open":False,"reason":"","at":None})
