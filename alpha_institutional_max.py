"""Institutional MAX control-plane: composes data, execution, risk, and audit gates."""
from __future__ import annotations
from alpha_l2_replay import validate_l2
from alpha_risk_kernel import gate
from alpha_smart_execution import plan
from alpha_event_store import EventStore

def readiness(*, data_ok:bool, l2_ok:bool, risk:dict, execution:dict, audit_ok:bool)->dict:
    reasons=[]
    if not data_ok: reasons.append('market_data')
    if not l2_ok: reasons.append('l2_data')
    if not risk.get('ok',False): reasons.extend('risk:'+x for x in risk.get('reasons',[]))
    if execution.get('capped') and execution.get('planned_notional',0)<=0: reasons.append('execution_capacity')
    if not audit_ok: reasons.append('audit')
    return {'ready':not reasons,'mode':'TRADE' if not reasons else 'FAIL_CLOSED','reasons':reasons}
