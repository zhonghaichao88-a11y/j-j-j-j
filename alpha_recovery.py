"""ALPHA-X 10.0 recovery/reconciliation policy.
Pure policy functions are intentionally network-free and easy to test.
"""
from __future__ import annotations
from typing import Any, Dict, List

TERMINAL={"filled","canceled","mmp_canceled","rejected"}

def classify_order(row:Dict[str,Any], known_client_ids=None)->str:
    cid=str(row.get("clOrdId") or "")
    if known_client_ids and cid in set(known_client_ids): return "OWNED"
    if cid.startswith("AX") or str(row.get("tag") or "")=="ALPHAX": return "ALPHA_TAGGED"
    return "UNKNOWN"

def reconcile(main_orders:List[Dict[str,Any]], algo_orders:List[Dict[str,Any]], owned_ids=None)->Dict[str,Any]:
    owned=[]; unknown=[]; live=[]; filled=[]
    ids=set(owned_ids or [])
    for r in list(main_orders or []):
        cls=classify_order(r,ids); (owned if cls in ("OWNED","ALPHA_TAGGED") else unknown).append(r)
        state=str(r.get("state") or "").lower()
        if state in ("live","partially_filled"): live.append(r)
        if state=="filled" or float(r.get("accFillSz") or 0)>0: filled.append(r)
    alpha_algos=[a for a in (algo_orders or []) if str(a.get("algoClOrdId") or a.get("attachAlgoClOrdId") or "").startswith("AX") or str(a.get("tag") or "")=="ALPHAX"]
    return {"owned":owned,"unknown":unknown,"live":live,"filled":filled,"alpha_algos":alpha_algos,
            "safe_to_adopt_only":True,"unknown_count":len(unknown)}
