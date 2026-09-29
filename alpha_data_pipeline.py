"""ALPHA-X Real Data Production Pipeline 16.0.

Turns the RealDataStore from a library into an auditable data-production service.
It never fabricates historical microstructure: trades/books/OI coverage is only
credited for observations that were actually collected.
"""
from __future__ import annotations
import hashlib, json, os, sqlite3, threading, time
import numpy as np
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import pandas as pd
from loguru import logger

from alpha_real_data import (
    OKXPublic, RealDataStore, fetch_funding_history, fetch_bars_history,
    collect_once, augment_candles, cross_market_from_store, source_gate, VERSION as REAL_DATA_VERSION,
    _inst_id,
)

VERSION = "ALPHA-X-REAL-DATA-PRODUCTION-16.0"
ROOT = Path(__file__).parent
DEFAULT_DB = ROOT / "alpha_market_data.sqlite3"
DEFAULT_DATASET_DIR = ROOT / "alpha_datasets"
SOURCE_COLUMNS_CROSS = ("btc_ret_1", "eth_ret_1", "btc_eth_spread", "venue_basis_bps")
SOURCE_GROUPS = {
    "funding": ("funding_rate", "funding_rate_realized", "funding_rate_event"),
    "open_interest": ("open_interest", "oi_change_pct", "oi"),
    "order_flow": ("ofi", "aggressive_buy_ratio", "trade_imbalance", "trade_count", "trade_volume_usd"),
    "l2": ("bid_depth_usd", "ask_depth_usd", "spread_bps", "depth_imbalance"),
    "cross_market": ("btc_ret_1", "eth_ret_1", "btc_eth_spread", "venue_basis_bps"),
}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def db_inventory(db_path: str | Path, inst_id: str) -> Dict[str, Any]:
    """Return auditable counts/ranges for one instrument."""
    p = Path(db_path)
    if not p.exists():
        return {"exists": False, "db": str(p), "inst_id": inst_id}
    st = RealDataStore(p)
    try:
        out = {"exists": True, "db": str(p), "inst_id": inst_id, "tables": {}}
        for table in ("bars", "funding", "oi", "trades", "books"):
            row = st.cx.execute(
                f"SELECT COUNT(*), MIN(ts), MAX(ts) FROM {table} WHERE inst_id=?", (inst_id,)
            ).fetchone()
            out["tables"][table] = {"count": int(row[0] or 0), "min_ts": row[1], "max_ts": row[2]}
        return out
    finally:
        st.close()


def _timeframe_minutes(timeframe: str) -> int:
    """Parse supported candle intervals without silently treating hours as 15m."""
    tf = str(timeframe or "15m").strip().lower()
    if tf.endswith("m"):
        n = int(tf[:-1]);
    elif tf.endswith("h"):
        n = int(tf[:-1]) * 60;
    elif tf.endswith("d"):
        n = int(tf[:-1]) * 24 * 60;
    else:
        raise ValueError(f"不支持的K线周期: {timeframe}")
    if n < 15 or n % 15 != 0:
        raise ValueError(f"V16→V17周期转换要求目标周期为15分钟的整数倍: {timeframe}")
    return n

def _bar_frame(st: RealDataStore, inst_id: str, start_ts: Optional[int], end_ts: Optional[int], timeframe: str = "15m") -> pd.DataFrame:
    """Load historical bars without trusting a stale/incorrect confirm flag.

    Old database rows may have confirm=0 even though their candle interval is long
    completed.  The authoritative completion rule here is timestamp < current candle
    bucket.  This preserves historical rows while still excluding the currently
    forming candle, preventing both false data shortages and look-ahead leakage.
    """
    d = st.fetch("bars", inst_id, start_ts, end_ts)
    if d.empty:
        return d
    mins = _timeframe_minutes(timeframe)
    interval_ms = mins * 60_000
    now_ms = int(time.time() * 1000)
    completed_cutoff = (now_ms // interval_ms) * interval_ms
    d = d.copy()
    d["ts"] = pd.to_numeric(d["ts"], errors="coerce")
    d = d.dropna(subset=["ts"])
    d = d[d["ts"] < completed_cutoff].copy()
    d = d.drop_duplicates("ts").sort_values("ts")
    d["confirm"] = 1
    return d.reset_index(drop=True)


def _gap_stats(ts: Iterable[int], expected_ms: int) -> Dict[str, Any]:
    vals = sorted(set(int(x) for x in ts if x is not None))
    if len(vals) < 2:
        return {"rows": len(vals), "expected_ms": expected_ms, "gaps": 0, "max_gap_bars": 0, "missing_bars_est": 0}
    diffs = [b-a for a,b in zip(vals, vals[1:])]
    missing = sum(max(0, int(round(d/expected_ms))-1) for d in diffs if d > expected_ms)
    max_gap = max([int(round(d/expected_ms))-1 for d in diffs if d > expected_ms] or [0])
    return {"rows": len(vals), "expected_ms": expected_ms, "gaps": sum(d > expected_ms for d in diffs),
            "max_gap_bars": max_gap, "missing_bars_est": missing}


def _canonical_manifest_sha(manifest: Dict[str, Any]) -> str:
    x = dict(manifest)
    x.pop("manifest_sha256", None)
    raw = json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _group_coverage(df: pd.DataFrame, group: str) -> float:
    cols = [c for c in SOURCE_GROUPS.get(group, ()) if c in df.columns]
    if not cols or len(df) == 0:
        return 0.0
    # A source is considered present for a row when at least one canonical
    # feature from that source is finite. This prevents a mere column name from
    # being treated as real data coverage.
    vals = df[cols].apply(pd.to_numeric, errors="coerce")
    return float(np.isfinite(vals.to_numpy()).any(axis=1).mean())


def build_dataset(
    inst_id: str,
    db_path: str | Path = DEFAULT_DB,
    timeframe: str = "15m",
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
    min_core_coverage: float = 0.80,
    require_microstructure: bool = False,
    cross_market: bool = False,
    output_dir: str | Path = DEFAULT_DATASET_DIR,
) -> Dict[str, Any]:
    """Materialize a training-ready, point-in-time dataset plus auditable manifest.

    Core gate = completed bars + funding. Historical OI is NOT treated as a
    required bootstrap source because the public OKX OI endpoint used by this
    collector is a current snapshot endpoint, not a paginated historical-OI
    backfill equivalent to history-candles. OI/trades/books are optional real
    observations: whatever is actually collected is persisted and aligned;
    missing history remains missing and is never fabricated. Cross-market is
    *actually materialized* when requested; it is never advertised merely
    because the flag was set.
    """
    iid = _inst_id(inst_id)
    mins = _timeframe_minutes(timeframe)
    if start_ts is not None and end_ts is not None and int(start_ts) > int(end_ts):
        raise ValueError("构建数据集的开始时间不能晚于结束时间")
    st = RealDataStore(db_path)
    try:
        # V16 数据仓库原始K线以15m为主；1h/更高周期必须先做真实OHLCV聚合，
        # 不能把15m行直接贴上“1h”标签，否则会导致V17研究时间轴和样本量错误。
        source_timeframe = "15m" if timeframe != "15m" else timeframe
        bars = _bar_frame(st, iid, start_ts, end_ts, source_timeframe)
        if bars.empty:
            raise ValueError(f"没有已完成K线: {iid}")
        if str(timeframe) != "15m":
            target_mins = mins
            bucket_ms = target_mins * 60_000
            source_ms = 15 * 60_000
            expected_per_bucket = max(1, target_mins // 15)
            b = bars.copy().sort_values("ts")
            b["bucket"] = (b["ts"] // bucket_ms) * bucket_ms
            # Only materialize fully completed higher-timeframe candles.
            # A partially collected hour/day must never be handed to V17 as a
            # complete bar because that changes both labels and sample counts.
            current_bucket = (int(time.time() * 1000) // bucket_ms) * bucket_ms
            b = b[b["bucket"] < current_bucket]
            g = (b.groupby("bucket", as_index=False)
                    .agg(open=("open","first"), high=("high","max"), low=("low","min"),
                         close=("close","last"), volume=("volume","sum"),
                         _source_count=("ts","count")))
            g = g[g["_source_count"] == expected_per_bucket]
            bars = (g.drop(columns=["_source_count"])
                    .rename(columns={"bucket":"ts"}))
            bars["confirm"] = 1
            bars = bars[["ts","open","high","low","close","volume","confirm"]].sort_values("ts").reset_index(drop=True)
            if bars.empty:
                raise ValueError(f"没有足够的完整{timeframe}历史K线: {iid}（需要每根{timeframe}包含完整15m数据）")
        enriched, audit = augment_candles(bars, st, iid, timeframe, strict=False)

        cross_audit = {"enabled": bool(cross_market), "coverage": 0.0, "features": []}
        if cross_market:
            enriched = cross_market_from_store(enriched, st)
            cross_audit["features"] = [c for c in SOURCE_COLUMNS_CROSS if c in enriched.columns]
            cross_audit["coverage"] = _group_coverage(enriched, "cross_market")
            if cross_audit["coverage"] < float(min_core_coverage):
                raise ValueError(f"CROSS_MARKET 数据覆盖不足: {cross_audit['coverage']:.3f} < {float(min_core_coverage):.3f}")

        ratios = {g: _group_coverage(enriched, g) for g in ("funding", "open_interest", "order_flow", "l2", "cross_market")}
        # 核心要求：只要求资金费率够80%（资金费率可补历史数据）
        # 持仓量/成交/盘口只能实时采集，有多少用多少，不强制要求
        core = {k: ratios.get(k, 0.0) for k in ("funding",)}
        core_ready = all(v >= float(min_core_coverage) for v in core.values())
        optional = {k: ratios.get(k, 0.0) for k in ("open_interest", "order_flow", "l2")}
        micro = {k: ratios.get(k, 0.0) for k in ("order_flow", "l2")}
        micro_ready = all(v >= float(min_core_coverage) for v in micro.values())
        if require_microstructure and not micro_ready:
            raise ValueError("REAL_DATA_MICRO_STRICT 覆盖不足: " + ",".join(k for k,v in micro.items() if v < min_core_coverage))
        if not core_ready:
            raise ValueError("REAL_DATA_CORE 数据覆盖不足（资金费率）: " + ",".join(k for k,v in core.items() if v < min_core_coverage))

        outdir = Path(output_dir); outdir.mkdir(parents=True, exist_ok=True)
        tag = f"{iid.replace('-', '_')}_{timeframe}"
        csv_path = outdir / f"{tag}.csv"
        manifest_path = outdir / f"{tag}.manifest.json"
        enriched.to_csv(csv_path, index=False)
        inv = db_inventory(db_path, iid)
        dataset_sha = _sha256_bytes(csv_path.read_bytes())
        manifest = {
            "version": VERSION,
            "real_data_version": REAL_DATA_VERSION,
            "instrument": iid,
            "timeframe": timeframe,
            "rows": int(len(enriched)),
            "start_ts": int(enriched.ts.min()),
            "end_ts": int(enriched.ts.max()),
            "requested_start_ts": int(start_ts) if start_ts is not None else None,
            "requested_end_ts": int(end_ts) if end_ts is not None else None,
            "completed_bars_only": True,
            "no_bfill": True,
            "point_in_time_join": True,
            "source_audit": audit,
            "coverage_ratios": ratios,
            "core_gate": {"ready": core_ready, "min_coverage": float(min_core_coverage), "sources": core},
            "microstructure_gate": {"ready": micro_ready, "required": bool(require_microstructure), "sources": micro},
            "historical_source_capabilities": {
                "bars": "paginated_history",
                "funding": "paginated_history_limited_by_exchange_history",
                "open_interest": "current_snapshot_only_in_this_collector",
                "trades": "continuous_collection_only_for_research_period",
                "l2": "continuous_collection_only_for_research_period",
            },
            "missing_source_policy": "persist_real_observations_and_build_with_available_data; never_fabricate_missing_oi",
            "cross_market": cross_audit,
            "cross_market_enabled": bool(cross_market),
            "gap_stats": _gap_stats(enriched.ts.tolist(), mins * 60_000),
            "columns": list(enriched.columns),
            "db_inventory": inv,
            "dataset_sha256": dataset_sha,
            "created_at": time.time(),
        }
        manifest["manifest_sha256"] = _canonical_manifest_sha(manifest)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return {"ready": True, "dataset": str(csv_path), "manifest": str(manifest_path), "manifest_sha256": manifest["manifest_sha256"], "audit": manifest}
    finally:
        st.close()


class DataCollector:
    """Durable background collector. Public data only; never places orders."""
    def __init__(self, symbols: List[str], db_path: str | Path = DEFAULT_DB,
                 interval: float = 3.0, bar_refresh: float = 60.0, funding_refresh: float = 900.0,
                 bootstrap_pages: int = 0):
        self.symbols = [_inst_id(s) for s in symbols]
        self.db_path = str(db_path)
        self.interval = max(1.0, float(interval))
        self.bar_refresh = max(15.0, float(bar_refresh))
        self.funding_refresh = max(60.0, float(funding_refresh))
        self.bootstrap_pages = max(0, int(bootstrap_pages))
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.RLock()
        self.stats: Dict[str, Any] = {"running": False, "cycles": 0, "errors": [], "last": {}, "started_at": None, "logs": [], "symbols": self.symbols}

    def start(self) -> Dict[str, Any]:
        with self.lock:
            if self.thread and self.thread.is_alive():
                return self.status()
            self.stop_event.clear()
            self.stats.update({"running": True, "started_at": time.time(), "cycles": 0, "errors": [], "last": {}, "logs": [], "symbols": self.symbols})
            logger.info(f"[数据采集] 启动｜币种={', '.join(self.symbols)}｜间隔={self.interval:.1f}秒｜K线刷新={self.bar_refresh:.0f}秒｜资金费率刷新={self.funding_refresh:.0f}秒")
            self.thread = threading.Thread(target=self._run, name="alpha-real-data", daemon=True)
            self.thread.start()
            return self.status()

    def stop(self) -> Dict[str, Any]:
        self.stop_event.set()
        t = self.thread
        if t and t.is_alive():
            t.join(timeout=5)
        with self.lock:
            self.stats["running"] = False
        logger.info("[数据采集] 已停止")
        return self.status()

    def status(self) -> Dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.stats, ensure_ascii=False, default=str))

    def _record_error(self, iid: str, exc: Exception):
        logger.error(f"[数据采集] {iid}｜失败｜{exc}")
        with self.lock:
            self.stats["errors"].append({"ts": time.time(), "inst_id": iid, "error": str(exc)})
            self.stats["errors"] = self.stats["errors"][-50:]

    def _run(self):
        client = OKXPublic()
        store = RealDataStore(self.db_path)
        next_bar = {s: 0.0 for s in self.symbols}
        next_funding = {s: 0.0 for s in self.symbols}
        try:
            # Optional one-time bootstrap. Zero means do not pretend to backfill.
            logger.info(f"[数据采集] 工作线程启动｜共{len(self.symbols)}个币种")
            if self.bootstrap_pages:
                for iid in self.symbols:
                    if self.stop_event.is_set(): break
                    try:
                        logger.info(f"[数据采集] {iid}｜历史数据补充开始｜页数={self.bootstrap_pages}")
                        bars_r = fetch_bars_history(client, store, iid, "15m", self.bootstrap_pages)
                        funding_r = fetch_funding_history(client, store, iid, min(self.bootstrap_pages, 20))
                        # OKX public OI endpoint is a current snapshot endpoint;
                        # do NOT pretend bootstrap_pages can backfill historical OI.
                        # OI starts accumulating from real collection cycles below.
                        counts = store.source_counts(iid)
                        logger.info(
                            f"[数据采集] {iid}｜历史数据补充完成｜K线新增={bars_r.get('inserted',0)}｜"
                            f"Funding新增={funding_r.get('inserted',0)}｜OI历史补齐=不支持｜当前OI累计={counts.get('oi',0)}"
                        )
                    except Exception as exc:
                        self._record_error(iid, exc)
            while not self.stop_event.is_set():
                now = time.time()
                for iid in self.symbols:
                    if self.stop_event.is_set(): break
                    r = {"oi": 0, "trades": 0, "books": 0, "bars": 0, "funding": 0, "errors": {}}
                    # 每个数据源独立处理：某一个接口失败，不得阻断其它数据源。
                    try:
                        one = collect_once(client, store, iid, 20)
                        r.update({k: one.get(k, 0) for k in ("oi", "trades", "books")})
                    except Exception as exc:
                        r["errors"]["实时市场数据"] = str(exc)
                        self._record_error(iid, RuntimeError(f"实时市场数据获取失败: {exc}"))
                    if now >= next_bar[iid]:
                        try:
                            r["bars"] = store.insert_bars(client.history_candles_all(iid, "15m", pages=2))
                            next_bar[iid] = now + self.bar_refresh
                        except Exception as exc:
                            r["errors"]["K线"] = str(exc)
                            self._record_error(iid, RuntimeError(f"K线获取失败: {exc}"))
                            next_bar[iid] = now + min(self.bar_refresh, 60.0)
                    if now >= next_funding[iid]:
                        try:
                            r["funding"] = fetch_funding_history(client, store, iid, pages=1)["inserted"]
                            next_funding[iid] = now + self.funding_refresh
                        except Exception as exc:
                            r["errors"]["资金费率"] = str(exc)
                            self._record_error(iid, RuntimeError(f"资金费率获取失败: {exc}"))
                            next_funding[iid] = now + min(self.funding_refresh, 300.0)
                    with self.lock:
                        self.stats["last"][iid] = {"ts": now, **r}
                    if r.get("errors"):
                        logger.warning(f"[数据采集] {iid}｜本轮部分失败｜{r['errors']}")
                    else:
                        logger.info(f"[数据采集] {iid}｜本轮完成｜K线={r.get('bars',0)}｜Funding={r.get('funding',0)}｜Trades={r.get('trades',0)}｜L2={r.get('books',0)}｜OI={r.get('oi',0)}")
                with self.lock:
                    self.stats["cycles"] += 1
                    # 记录详细日志
                    log_entry = {
                        "ts": now,
                        "cycle": self.stats["cycles"],
                        "symbols": {}
                    }
                    for iid in self.symbols:
                        last_data = self.stats["last"].get(iid, {})
                        log_entry["symbols"][iid] = {
                            "bars": last_data.get("bars", 0) if isinstance(last_data.get("bars"), (int, float)) else (last_data.get("bars", {}).get("inserted", 0) if isinstance(last_data.get("bars"), dict) else 0),
                            "funding": last_data.get("funding", 0),
                            "trades": last_data.get("trades", 0),
                            "books": last_data.get("books", 0),
                            "oi": last_data.get("oi", 0),
                            "errors": last_data.get("errors", {}),
                        }
                    self.stats["logs"].append(log_entry)
                    self.stats["logs"] = self.stats["logs"][-100:]  # 只保留最近100条
                self.stop_event.wait(self.interval)
        finally:
            store.close()
            with self.lock:
                self.stats["running"] = False


_COLLECTOR: Optional[DataCollector] = None
_COLLECTOR_LOCK = threading.RLock()


def collector_start(symbols: List[str], **kwargs) -> Dict[str, Any]:
    global _COLLECTOR
    with _COLLECTOR_LOCK:
        # 每次启动都先停止旧的，再用新币种创建新的（确保改币种后重启生效）
        if _COLLECTOR is not None:
            try:
                _COLLECTOR.stop()
            except Exception:
                pass
        _COLLECTOR = DataCollector(symbols, **kwargs)
        return _COLLECTOR.start()


def collector_stop() -> Dict[str, Any]:
    with _COLLECTOR_LOCK:
        return _COLLECTOR.stop() if _COLLECTOR else {"running": False, "reason": "not_started"}


def collector_status() -> Dict[str, Any]:
    with _COLLECTOR_LOCK:
        return _COLLECTOR.status() if _COLLECTOR else {"running": False, "reason": "not_started"}


def production_config() -> Dict[str, Any]:
    syms = [s.strip() for s in os.getenv("ALPHA_REAL_DATA_SYMBOLS", "BTC-USDT-SWAP").split(",") if s.strip()]
    return {"version": VERSION, "db": os.getenv("ALPHA_REAL_DATA_DB", str(DEFAULT_DB)),
            "symbols": [_inst_id(s) for s in syms], "interval": float(os.getenv("ALPHA_REAL_DATA_INTERVAL", "3")),
            "bar_refresh": float(os.getenv("ALPHA_REAL_DATA_BAR_REFRESH", "60")),
            "funding_refresh": float(os.getenv("ALPHA_REAL_DATA_FUNDING_REFRESH", "900")),
            "bootstrap_pages": int(os.getenv("ALPHA_REAL_DATA_BOOTSTRAP_PAGES", "0")),
            "autostart": os.getenv("ALPHA_REAL_DATA_AUTOSTART", "0") == "1"}


def maybe_autostart() -> Dict[str, Any]:
    cfg = production_config()
    if cfg["autostart"]:
        return collector_start(cfg.pop("symbols"), db_path=cfg.pop("db"), interval=cfg.pop("interval"),
                               bar_refresh=cfg.pop("bar_refresh"), funding_refresh=cfg.pop("funding_refresh"),
                               bootstrap_pages=cfg.pop("bootstrap_pages"))
    return {"running": False, "autostart": False}

if __name__ == "__main__":
    import argparse
    ap=argparse.ArgumentParser(description="ALPHA-X 16.0 real-data production")
    ap.add_argument("command", choices=["status","start","stop","build"])
    ap.add_argument("--inst-id", default="BTC-USDT-SWAP")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--output-dir", default=str(DEFAULT_DATASET_DIR))
    ap.add_argument("--bootstrap-pages", type=int, default=0)
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--require-microstructure", action="store_true")
    args=ap.parse_args()
    if args.command=="status":
        print(json.dumps({"version":VERSION,"collector":collector_status(),"inventory":db_inventory(args.db,_inst_id(args.inst_id))},ensure_ascii=False,indent=2))
    elif args.command=="start":
        print(json.dumps(collector_start([args.inst_id],db_path=args.db,interval=args.interval,bootstrap_pages=args.bootstrap_pages),ensure_ascii=False,indent=2))
    elif args.command=="stop":
        print(json.dumps(collector_stop(),ensure_ascii=False,indent=2))
    else:
        print(json.dumps(build_dataset(args.inst_id,args.db,output_dir=args.output_dir,require_microstructure=args.require_microstructure),ensure_ascii=False,indent=2))
