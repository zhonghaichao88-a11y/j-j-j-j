"""ALPHA-X Institutional 15.0 production capstone kernel.
Network-free primitives for production readiness, HA fencing, data contracts,
canary promotion, disaster recovery and deterministic run manifests.
"""
from __future__ import annotations
import hashlib, json, os, time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

VERSION = "ALPHA-X-INSTITUTIONAL-15.0"


def stable_id(*parts: Any) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:32]

@dataclass(frozen=True)
class DataContract:
    venue: str
    instrument: str
    channel: str
    schema_version: int = 1
    required_fields: tuple = ("ts_exchange", "seq")
    def validate(self, row: Dict[str, Any]) -> Dict[str, Any]:
        missing=[x for x in self.required_fields if x not in row]
        return {"ok": not missing, "missing": missing, "schema_version": self.schema_version}

@dataclass
class FencingToken:
    epoch: int
    owner: str
    issued_at: float
    ttl_sec: int = 30
    def valid(self, now: Optional[float]=None) -> bool:
        now=time.time() if now is None else float(now)
        return self.epoch>0 and bool(self.owner) and now-self.issued_at <= self.ttl_sec

class EpochFence:
    """Monotonic epoch fence: stale workers cannot become execution leaders."""
    def __init__(self, path: Path): self.path=Path(path)
    def issue(self, owner: str, now: Optional[float]=None) -> FencingToken:
        now=time.time() if now is None else float(now)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        epoch=0
        if self.path.exists():
            try: epoch=int(json.loads(self.path.read_text()).get("epoch",0))
            except Exception: epoch=0
        epoch+=1
        token=FencingToken(epoch, str(owner), now)
        tmp=self.path.with_suffix('.tmp'); tmp.write_text(json.dumps(asdict(token),separators=(',',':'))); tmp.replace(self.path)
        return token
    def current(self) -> Optional[FencingToken]:
        if not self.path.exists(): return None
        try: return FencingToken(**json.loads(self.path.read_text()))
        except Exception: return None
    def authorize(self, token: FencingToken, now: Optional[float]=None) -> bool:
        cur=self.current()
        return bool(cur and token.epoch==cur.epoch and token.owner==cur.owner and token.valid(now))
    def renew(self, token: FencingToken, now: Optional[float]=None) -> Optional[FencingToken]:
        """Renew only if this process is still the current fencing owner.
        Never re-issues a new epoch here: a takeover by another process must remain a hard block.
        """
        now=time.time() if now is None else float(now)
        cur=self.current()
        if not cur or token.epoch!=cur.epoch or token.owner!=cur.owner:
            return None
        renewed=FencingToken(cur.epoch,cur.owner,now,cur.ttl_sec)
        tmp=self.path.with_suffix('.tmp'); tmp.write_text(json.dumps(asdict(renewed),separators=(',',':'))); tmp.replace(self.path)
        return renewed

@dataclass(frozen=True)
class RunManifest:
    run_id: str
    model_fingerprint: str
    data_fingerprint: str
    config_fingerprint: str
    created_at: int
    mode: str
    def digest(self) -> str:
        return stable_id(self.run_id,self.model_fingerprint,self.data_fingerprint,self.config_fingerprint,self.created_at,self.mode)

def make_manifest(model: Any, data: Any, config: Any, mode: str="shadow", now: Optional[int]=None) -> RunManifest:
    def fp(x): return hashlib.sha256(json.dumps(x,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()
    ts=int(time.time()) if now is None else int(now)
    rid=stable_id(fp(model),fp(data),fp(config),ts,mode)
    return RunManifest(rid,fp(model),fp(data),fp(config),ts,mode)

def canary_gate15(*, data_ok: bool, risk_ok: bool, reconcile_ok: bool, execution_ok: bool,
                 model_approved: bool, capacity_ok: bool, kill_switch: bool=False) -> Dict[str,Any]:
    checks={"data":bool(data_ok),"risk":bool(risk_ok),"reconciliation":bool(reconcile_ok),
            "execution":bool(execution_ok),"model":bool(model_approved),"capacity":bool(capacity_ok),
            "kill_switch_clear":not bool(kill_switch)}
    return {"version":VERSION,"ready":all(checks.values()),"checks":checks,"mode":"CANARY"}

def production_gate15(*, market_data_ok: bool, clock_ok: bool, risk_ok: bool,
                      reconciliation_ok: bool, execution_ok: bool, ha_ok: bool,
                      audit_ok: bool, recovery_ok: bool, model_ok: bool,
                      kill_switch: bool=False) -> Dict[str,Any]:
    checks={"market_data":bool(market_data_ok),"clock":bool(clock_ok),"risk":bool(risk_ok),
            "reconciliation":bool(reconciliation_ok),"execution":bool(execution_ok),
            "ha_fencing":bool(ha_ok),"audit":bool(audit_ok),"recovery":bool(recovery_ok),
            "model_governance":bool(model_ok),"kill_switch_clear":not bool(kill_switch)}
    return {"version":VERSION,"ready":all(checks.values()),"checks":checks,
            "action":"ALLOW_NEW_RISK" if all(checks.values()) else "FAIL_CLOSED"}

def recovery_plan(*, exchange_reachable: bool, rest_truth: bool, ws_fresh: bool,
                  local_state_consistent: bool, open_orders_known: bool) -> Dict[str,Any]:
    checks={"exchange_reachable":bool(exchange_reachable),"rest_truth":bool(rest_truth),
            "ws_fresh":bool(ws_fresh),"local_state_consistent":bool(local_state_consistent),
            "open_orders_known":bool(open_orders_known)}
    return {"safe_to_resume":all(checks.values()),"checks":checks,
            "action":"RESUME" if all(checks.values()) else "RECONCILE_THEN_RESUME"}

def immutable_event_hash(prev_hash: str, event: Dict[str,Any]) -> str:
    raw=json.dumps(event,sort_keys=True,separators=(',',':'))
    return hashlib.sha256((str(prev_hash)+raw).encode()).hexdigest()
