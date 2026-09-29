"""ALPHA-X Institutional 11.0: production hardening kernel.

Focus: exchange-truth reconciliation, immutable fill/bill normalization,
clock/lease safety, configuration fail-closed checks, and operational SLOs.
No network calls are made by this module; adapters can feed it OKX REST/WS data.
"""
from __future__ import annotations
import hashlib, json, math, os, socket, time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

VERSION = "ALPHA-X-INSTITUTIONAL-11.0"


def finite(x, default=0.0):
    try:
        v=float(x)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def stable_id(*parts: Any) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:32]


def normalize_fill(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize OKX fill/WS records into one deterministic schema."""
    inst = str(row.get("instId", row.get("symbol", "")))
    ord_id = str(row.get("ordId", row.get("order_id", "")))
    trade_id = str(row.get("tradeId", row.get("trade_id", "")))
    fill_time = int(finite(row.get("fillTime", row.get("fill_time", row.get("ts", 0)))))
    px = finite(row.get("fillPx", row.get("fill_px", row.get("price", 0))))
    sz = abs(finite(row.get("fillSz", row.get("fill_sz", row.get("qty", 0)))))
    fee = finite(row.get("fee", row.get("fillFee", 0)))
    pnl = finite(row.get("fillPnl", row.get("pnl", 0)))
    key = stable_id(inst, ord_id, trade_id, fill_time, px, sz)
    return {
        "fill_key": key, "inst_id": inst, "ord_id": ord_id,
        "cl_ord_id": str(row.get("clOrdId", row.get("client_order_id", ""))),
        "trade_id": trade_id, "fill_time": fill_time,
        "fill_px": px, "fill_sz": sz, "fee": fee,
        "fee_ccy": str(row.get("feeCcy", row.get("fee_ccy", ""))),
        "fill_pnl": pnl, "exec_type": str(row.get("execType", "")),
        "tag": str(row.get("tag", "")),
    }


def normalize_bill(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize OKX account-bill records for fee/funding/PnL attribution."""
    bill_id = str(row.get("billId", row.get("bill_id", "")))
    ts = int(finite(row.get("ts", row.get("time", 0))))
    amount = finite(row.get("balChg", row.get("bal_chg", row.get("pnl", 0))))
    fee = finite(row.get("fee", 0))
    key = stable_id(bill_id, ts, row.get("type", ""), amount, fee)
    return {
        "bill_key": key, "bill_id": bill_id, "ts": ts,
        "inst_id": str(row.get("instId", "")),
        "ord_id": str(row.get("ordId", "")),
        "cl_ord_id": str(row.get("clOrdId", "")),
        "type": str(row.get("type", "")),
        "sub_type": str(row.get("subType", row.get("sub_type", ""))),
        "amount": amount, "fee": fee,
        "fee_ccy": str(row.get("ccy", row.get("feeCcy", ""))),
        "pnl": finite(row.get("pnl", 0)),
        "tag": str(row.get("tag", "")),
    }


def dedupe_fills(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for row in rows or []:
        x = normalize_fill(row)
        if x["fill_key"] in seen:
            continue
        seen.add(x["fill_key"]); out.append(x)
    return sorted(out, key=lambda x: (x["fill_time"], x["trade_id"]))


def reconcile_orders(rest_orders: Iterable[Dict[str, Any]], ws_orders: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Exchange truth wins. WS is merged, REST fills gaps after reconnect."""
    merged: Dict[str, Dict[str, Any]] = {}
    for source, rows in (("rest", rest_orders), ("ws", ws_orders)):
        for r in rows or []:
            oid = str(r.get("ordId", r.get("ord_id", "")))
            if not oid:
                continue
            prev = merged.get(oid, {})
            cur = dict(prev); cur.update(r); cur["source"] = source if source == "ws" else prev.get("source", "rest")
            # Terminal state from exchange is authoritative.
            if str(r.get("state", "")) in {"filled", "canceled", "mmp_canceled"}:
                cur["state"] = r["state"]; cur["terminal"] = True
            merged[oid] = cur
    return {"orders": list(merged.values()), "count": len(merged), "ok": True}


@dataclass
class ClockGuard:
    max_skew_ms: int = 2000
    server_ts_ms: Optional[int] = None
    local_ts_ms: Optional[int] = None

    def check(self, local_ts_ms: Optional[int] = None, server_ts_ms: Optional[int] = None) -> Dict[str, Any]:
        self.local_ts_ms = int(local_ts_ms if local_ts_ms is not None else time.time() * 1000)
        if server_ts_ms is not None:
            self.server_ts_ms = int(server_ts_ms)
        if self.server_ts_ms is None:
            return {"ok": False, "reason": "server_time_unknown", "skew_ms": None}
        skew = self.local_ts_ms - self.server_ts_ms
        return {"ok": abs(skew) <= self.max_skew_ms, "skew_ms": skew, "max_skew_ms": self.max_skew_ms}


class Lease:
    """Single-host HA lease. Failure to acquire means no new live orders."""
    def __init__(self, path: Path, ttl_sec: int = 30, owner: Optional[str] = None):
        self.path = Path(path); self.ttl = int(ttl_sec)
        self.owner = owner or f"{socket.gethostname()}:{os.getpid()}"

    def acquire(self, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else float(now)
        if self.path.exists():
            try:
                cur = json.loads(self.path.read_text())
                if cur.get("owner") != self.owner and now - float(cur.get("heartbeat", 0)) < self.ttl:
                    return False
            except Exception:
                return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"owner": self.owner, "heartbeat": now}, separators=(",", ":")))
        tmp.replace(self.path)
        return True

    def heartbeat(self, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else float(now)
        if not self.path.exists(): return False
        try:
            cur = json.loads(self.path.read_text())
            if cur.get("owner") != self.owner: return False
            cur["heartbeat"] = now
            tmp = self.path.with_suffix(".tmp"); tmp.write_text(json.dumps(cur, separators=(",", ":"))); tmp.replace(self.path)
            return True
        except Exception:
            return False

    def release(self) -> bool:
        try:
            cur = json.loads(self.path.read_text())
            if cur.get("owner") != self.owner: return False
            self.path.unlink(missing_ok=True); return True
        except Exception:
            return False


def production_config_check(env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    e = dict(os.environ if env is None else env)
    failures: List[str] = []
    host = e.get("ALPHA_BIND_HOST", "127.0.0.1")
    key = e.get("ALPHA_API_KEY", "")
    live = e.get("ALPHA_LIVE_ALLOWED", "0") == "1"
    if host not in {"127.0.0.1", "localhost", "::1"} and not key:
        failures.append("non_loopback_requires_api_key")
    if live and len(e.get("ALPHA_LIVE_API_KEY", "")) < 32:
        failures.append("live_key_too_short")
    if live and e.get("OKX_DEMO_MODE", "0") == "1":
        failures.append("live_enabled_with_demo_mode")
    if e.get("ALPHA_AUTOPROMOTE", "0") == "1":
        failures.append("autopromotion_forbidden")
    return {"ok": not failures, "failed": failures, "bind_host": host, "live": live}


def slo_snapshot(*, ws_age_sec: float, reconcile_age_sec: float, clock_ok: bool,
                  breaker_open: bool, queue_depth: int, max_ws_age: float = 60,
                  max_reconcile_age: float = 30, max_queue: int = 100) -> Dict[str, Any]:
    checks = {
        "ws_fresh": ws_age_sec <= max_ws_age,
        "reconciliation_fresh": reconcile_age_sec <= max_reconcile_age,
        "clock_ok": bool(clock_ok), "breaker_closed": not breaker_open,
        "queue_ok": int(queue_depth) <= max_queue,
    }
    return {"version": VERSION, "ready": all(checks.values()), "checks": checks,
            "ws_age_sec": ws_age_sec, "reconcile_age_sec": reconcile_age_sec,
            "queue_depth": queue_depth}
