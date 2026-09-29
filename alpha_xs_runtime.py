"""
ALPHA-X FAST · v5-XS 横截面中性 —— 组合层运行时（定时调仓 / 看门狗快出 / maker 限价 / 宽熔断）
====================================================================================
独立于 v3/v4/v5 逐币引擎，只服务 v5-XS 组合层日频调仓。所有"手动点③"和"定时自动调仓"
都走同一个 run_rebalance()，保证两条路径完全一致、不会一个能跑一个不能跑。

四件事（均有开关，默认值见 DEFAULTS）：
  1) 定时调仓（闹钟）：后台线程每天 UTC 调度时间（默认 00:30 = 北京 08:30）自动实盘调仓一次；
     当天漏跑（宕机重启）会在到点后补跑，绝不连续两天不调仓（回测：隔日调样本外由盈转亏）。
  2) 看门狗（快出）：每日调仓前剔除"无 K 线 / 24h 成交额低于池中位数 1/10"的币；若其有持仓，
     有行情就平仓、无行情就列入 blocked 告警，其余币照常调仓——修复"一个坏币整批拒单"。
     固定池本身不被自动改写（慢进只给候选建议，人工季度审核，不追热点）。
  3) maker 限价：调仓单先挂 post-only maker，超时未成交余量自动转市价，保证该换的仓换完。
  4) -10% 宽熔断（默认关）：较上一调仓日权益回撤达到阈值时，当天全平、不开新仓，次日恢复。

安全：定时自动下单默认关闭，需在网页/配置显式打开；所有状态持久化到 alpha_xs_runtime.json。
"""
from __future__ import annotations
import os
import json
import time
import threading
import datetime as dt
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    from loguru import logger
except Exception:  # pragma: no cover
    import logging
    logger = logging.getLogger("xs_runtime")

import alpha_xs_neutral as XS
import alpha_xs_executor as XE

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "alpha_xs_runtime.json")

# 固定 31 观察池（现货习惯写法；运行时会自动把 PEPE 等解析为 OKX 千倍合约 1000PEPE）。
# 164 币大样本回测结论：动态成交额 top N 全面跑输固定池，故坚持固定池，不做每日热点切换。
FIXED_UNIVERSE = ["WIF", "SUI", "SEI", "APT", "INJ", "TIA", "OP", "ARB", "WLD", "FET", "RUNE",
                  "AAVE", "CRV", "ENS", "GALA", "SAND", "AXS", "CHZ", "LDO", "ALT", "JTO",
                  "PEPE", "SHIB", "FLOKI", "MEME", "BONK", "RENDER", "STX", "IMX", "ARKM", "ORDI"]

DEFAULTS: Dict[str, Any] = {
    "schedule_enabled": os.getenv("OKX_XS_AUTO_ENABLED", "0") in ("1", "true", "yes", "on"),
    "schedule_time": os.getenv("OKX_XS_AUTO_TIME", "00:30"),     # UTC；北京 = +8h（00:30 = 北京08:30）
    "watchdog_enabled": os.getenv("OKX_XS_WATCHDOG", "1") in ("1", "true", "yes", "on"),
    "watchdog_liquidity_ratio": 0.10,   # 24h 成交额低于"有效池中位数"的该比例则踢出
    "maker_enabled": os.getenv("OKX_XS_MAKER", "1") in ("1", "true", "yes", "on"),
    "maker_ttl": float(os.getenv("OKX_XS_MAKER_TTL", "8") or 8),
    "maker_improve_bps": float(os.getenv("OKX_XS_MAKER_IMPROVE_BPS", "0.5") or 0.5),
    "breaker_enabled": os.getenv("OKX_XS_BREAKER", "0") in ("1", "true", "yes", "on"),
    "breaker_pct": float(os.getenv("OKX_XS_BREAKER_PCT", "-0.10") or -0.10),
    "k": 5, "lookback": 288, "gross": 2.0,
}

_LOCK = threading.RLock()
_thread_started = False
_client_provider: Optional[Callable[[], Any]] = None
_running = False
_tick_interval = 20.0

_state: Dict[str, Any] = {
    "config": {},
    "last_run_date": "",
    "last_run_at": 0.0,
    "last_run": None,
    "equity_anchor": None,
    "breaker": {"active": False, "date": "", "pct": None, "reason": ""},
    "recent_logs": [],
}


# ============================ 配置与状态 ============================
def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _load_state_locked() -> None:
    global _state
    cfg = dict(DEFAULTS)
    persisted = {}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                persisted = json.load(f) or {}
        except Exception as e:
            logger.warning(f"[XS运行时] 状态文件读取失败，使用默认: {e}")
    cfg.update(persisted.get("config") or {})
    _state["config"] = cfg
    for key in ("last_run_date", "last_run_at", "last_run", "equity_anchor", "breaker", "recent_logs"):
        if key in persisted:
            _state[key] = persisted[key]
    if not isinstance(_state.get("recent_logs"), list):
        _state["recent_logs"] = []


def _save_state_locked() -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(_state, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        logger.warning(f"[XS运行时] 状态持久化失败: {e}")


_load_state_locked()


def get_config() -> Dict[str, Any]:
    with _LOCK:
        return dict(_state["config"])


_BOOL_KEYS = ("schedule_enabled", "watchdog_enabled", "maker_enabled", "breaker_enabled")
_NUM_RANGES = {
    "maker_ttl": (0.0, 180.0),
    "maker_improve_bps": (0.0, 20.0),
    "watchdog_liquidity_ratio": (0.01, 1.0),
    "k": (1, 15),
    "lookback": (24, 1000),
    "gross": (0.1, 5.0),
}


def set_config(patch: Dict[str, Any]) -> Dict[str, Any]:
    """更新运行时配置并持久化。严格白名单 + 范围校验：任何未知键或非法值直接报错，不静默吞掉。"""
    if not isinstance(patch, dict) or not patch:
        raise ValueError("没有要更新的配置")
    allowed = set(_BOOL_KEYS) | set(_NUM_RANGES.keys()) | {"schedule_time", "breaker_pct"}
    unknown = [k for k in patch if k not in allowed]
    if unknown:
        raise ValueError(f"未知配置项：{unknown}；允许的字段：{sorted(allowed)}")
    with _LOCK:
        cfg = _state["config"]
        if "schedule_time" in patch:
            t = str(patch["schedule_time"]).strip()
            parts = t.split(":")
            if len(parts) != 2 or not (parts[0].isdigit() and parts[1].isdigit()):
                raise ValueError("时间格式必须为 HH:MM（UTC）")
            hh, mm = int(parts[0]), int(parts[1])
            if not (0 <= hh < 24 and 0 <= mm < 60):
                raise ValueError("时间超出范围（小时0-23、分钟0-59）")
            patch["schedule_time"] = f"{hh:02d}:{mm:02d}"
        if "breaker_pct" in patch:
            bp = float(patch["breaker_pct"])
            if bp >= 0 or bp < -0.5:
                raise ValueError("熔断阈值必须是 -50% 到 -1% 之间的负数（如 -0.10）")
            patch["breaker_pct"] = bp
        for key in _BOOL_KEYS:
            if key in patch:
                patch[key] = bool(patch[key])
        for key, (lo, hi) in _NUM_RANGES.items():
            if key in patch:
                try:
                    val = int(float(patch[key])) if key in ("k", "lookback") else float(patch[key])
                except (TypeError, ValueError):
                    raise ValueError(f"{key} 必须是数字")
                if not (lo <= val <= hi):
                    raise ValueError(f"{key}={val} 超出允许范围 [{lo}, {hi}]")
                patch[key] = val
        cfg.update(patch)
        _save_state_locked()
        _log("配置已更新：" + json.dumps(patch, ensure_ascii=False, default=str))
        return dict(cfg)


def _log(msg: str, level: str = "info") -> None:
    line = {"ts": _now_utc().isoformat(), "level": level, "msg": msg}
    logs = _state.setdefault("recent_logs", [])
    logs.append(line)
    if len(logs) > 40:
        del logs[:-40]
    try:
        (getattr(logger, level if hasattr(logger, level) else "info") or logger.info)(f"[XS运行时] {msg}")
    except Exception:
        pass


def next_run_time(cfg: Optional[Dict[str, Any]] = None, now: Optional[dt.datetime] = None) -> str:
    cfg = cfg or get_config()
    now = now or _now_utc()
    hh, mm = (int(x) for x in str(cfg["schedule_time"]).split(":"))
    t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    today = now.strftime("%Y-%m-%d")
    # 今天已经（手动或定时）实盘调过，则到点也会被 scheduler_tick 判 already_run 跳过，
    # 故"下次"必须直接显示明天，避免界面显示今晚、实际不跑的误导。
    already_today = _state.get("last_run_date", "") == today
    if already_today or t <= now:
        t += dt.timedelta(days=1)
    return t.isoformat()


def status() -> Dict[str, Any]:
    with _LOCK:
        cfg = dict(_state["config"])
        return {
            "config": cfg,
            "schedule_enabled": cfg["schedule_enabled"],
            "watchdog_enabled": cfg["watchdog_enabled"],
            "maker_enabled": cfg["maker_enabled"],
            "breaker_enabled": cfg["breaker_enabled"],
            "entry_mode": "maker_market" if cfg["maker_enabled"] else "market",
            "schedule_time_utc": cfg["schedule_time"],
            "schedule_time_beijing": _beijing(cfg["schedule_time"]),
            "next_run_utc": next_run_time(cfg) if cfg["schedule_enabled"] else None,
            "last_run_date": _state.get("last_run_date", ""),
            "last_run_at": _state.get("last_run_at", 0.0),
            "last_run": _state.get("last_run"),
            "equity_anchor": _state.get("equity_anchor"),
            "breaker": _state.get("breaker"),
            "running": _running,
            "fixed_universe_size": len(FIXED_UNIVERSE),
            "recent_logs": list(_state.get("recent_logs", []))[-8:],
            "server_time_utc": _now_utc().isoformat(),
        }


def _beijing(hhmm_utc: str) -> str:
    hh, mm = (int(x) for x in hhmm_utc.split(":"))
    total = (hh * 60 + mm + 8 * 60) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


# ============================ 符号解析 / 看门狗 ============================
_SYMBOL_INDEX_CACHE: Dict[int, Dict[str, str]] = {}


def _symbol_index(client: Any) -> Dict[str, str]:
    """返回 {真实交易base: ccxt符号}，优先 ccxt 已加载的 markets；缺失时用全市场永续快照兜底。
    canonical_base 必须与 fetch_swap_tickers() 给出的 base 完全一致，看门狗/价格才不会对不上。"""
    ex = getattr(client, "_exchange", None)
    markets = getattr(ex, "markets", None)
    if markets:
        idx: Dict[str, str] = {}
        for cs in markets.keys():
            if isinstance(cs, str) and cs.endswith("/USDT:USDT"):
                idx[cs.split("/")[0]] = cs
        return idx
    cid = id(client)
    if cid in _SYMBOL_INDEX_CACHE:
        return _SYMBOL_INDEX_CACHE[cid]
    idx = {}
    try:
        for it in (client.fetch_swap_tickers() or []):
            cs = it.get("ccxt")
            if isinstance(cs, str) and cs.endswith("/USDT:USDT"):
                idx[cs.split("/")[0]] = cs
    except Exception:
        pass
    _SYMBOL_INDEX_CACHE[cid] = idx
    return idx


def resolve_ccxt(client: Any, base: str) -> Tuple[str, str]:
    """把池子中的名字解析为真实可交易的 OKX 永续 ccxt 符号。
    优先 {B}/USDT:USDT；否则尝试 1000{B}/USDT:USDT（PEPE/SHIB/FLOKI/BONK 是千倍合约）。
    返回 (ccxt_symbol, canonical_base)。两处都拿不到时按原样返回（会在拉K线时进入 failed）。"""
    b = str(base).upper().replace("/USDT:USDT", "").strip()
    idx = _symbol_index(client)
    direct = f"{b}/USDT:USDT"
    if b in idx:
        return idx[b], b
    if b.startswith("1000") and b[4:] in idx:
        return idx[b[4:]], b[4:]
    k1000 = f"1000{b}"
    if k1000 in idx:
        return idx[k1000], k1000
    return direct, b


def watchdog_screen(good_bases: List[str], qv_map: Dict[str, float], ratio: float = 0.10) -> Tuple[List[str], List[Dict[str, Any]]]:
    """纯函数：在"有 K 线"的币里，按 24h 成交额相对池中位数筛掉流动性枯竭的币。
    防误杀两道闸：
      1) 全市场快照整体取不到（中位数=0）时不踢任何币；
      2) 某币成交额取不到（q<=0，常见于永续 quoteVolume 字段缺失/快照异常）时也保留——
         一个能拉到完整 K 线的币几乎不可能 24h 零成交，"取不到数据"不等于"流动性枯竭"。
    只有"明确取到成交额、且低于中位数 ratio"才踢。返回 (保留, 剔除明细)。"""
    vals = [float(qv_map.get(b, 0) or 0) for b in good_bases]
    vals = [v for v in vals if v > 0]
    med = float(np.median(vals)) if vals else 0.0
    if med <= 0:
        return list(good_bases), []
    keep, rejected = [], []
    for b in good_bases:
        q = float(qv_map.get(b, 0) or 0)
        if q <= 0:
            keep.append(b)  # 成交额未取到：保守保留，避免把数据缺失误判成流动性枯竭
        elif q < med * ratio:
            rejected.append({"base": b, "quote_volume_24h": round(q, 0),
                             "median": round(med, 0),
                             "reason": f"24h成交额 {q:,.0f} 低于池中位数 {med:,.0f} 的 {ratio:.0%}，看门狗快出"})
        else:
            keep.append(b)
    return keep, rejected


def slow_in_candidates(client: Any, top_n: int = 10) -> List[Dict[str, Any]]:
    """慢进【只读候选建议】，不自动改固定池。列出当前不在固定池、但 24h 成交额靠前的币，
    供人工季度审核（上线满 90 天 + 连续 4 周稳定才人工加入）。拿不到快照返回空。"""
    try:
        snap = client.fetch_swap_tickers()
    except Exception:
        return []
    fixed = set(FIXED_UNIVERSE) | {f"1000{x}" for x in FIXED_UNIVERSE}
    rows = []
    for it in snap:
        base = str(it.get("ccxt", "")).split("/")[0]
        if base in fixed:
            continue
        q = float(it.get("quote_volume") or 0)
        if q > 0:
            rows.append({"base": base, "quote_volume_24h": round(q, 0),
                         "note": "候选，需人工确认上线满90天且连续4周成交额稳定，再季度审核加入"})
    rows.sort(key=lambda x: x["quote_volume_24h"], reverse=True)
    return rows[:top_n]


# ============================ 核心调仓（手动 / 定时共用） ============================
def _breaker_triggered(anchor: Optional[float], equity: Optional[float],
                       enabled: bool, breaker_pct: float) -> Tuple[bool, Optional[float]]:
    """纯函数：较上一调仓日权益锚点回撤达到阈值则熔断。
    关闭、无锚点（首日）、无权益均不触发。返回 (是否熔断, 回撤比例)。"""
    if not enabled or not anchor or equity is None:
        return False, None
    try:
        anchor_f = float(anchor); eq_f = float(equity)
        dd = eq_f / anchor_f - 1.0
        # 用权益阈值比较（避免收益率浮点误差使"恰好-10%"漏触发），加 1e-9 容差
        threshold = anchor_f * (1.0 + float(breaker_pct))
        trig = eq_f <= threshold + 1e-9
    except (TypeError, ValueError, ZeroDivisionError):
        return False, None
    return trig, round(dd, 4)


def _fetch_klines(client: Any, bases: List[str], lookback: int) -> Tuple[Dict[str, pd.Series], List[Dict[str, Any]], Dict[str, str]]:
    """返回 {canonical_base: close Series}、失败明细、{canonical_base: ccxt_symbol}。"""
    closes: Dict[str, pd.Series] = {}
    failed: List[Dict[str, Any]] = []
    sym_map: Dict[str, str] = {}
    for raw_base in bases:
        try:
            sym, canonical = resolve_ccxt(client, raw_base)
            raw = client.get_ohlcv(sym, "5m", limit=lookback + 2)
            n_raw = len(raw) if raw else 0
            if not raw or n_raw < lookback + 1:
                failed.append({"base": canonical, "reason": f"K线不足({n_raw}根)"})
                continue
            df = pd.DataFrame(raw, columns=["ts", "o", "h", "l", "c", "v"])
            df["t"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
            closes[canonical] = df.set_index("t")["c"].astype(float)
            sym_map[canonical] = sym
        except Exception as e:
            failed.append({"base": str(raw_base).upper(), "reason": f"K线/行情异常: {str(e)[:80]}"})
    return closes, failed, sym_map


def _quote_volume_map(client: Any) -> Dict[str, float]:
    """一次性拉全市场永续快照，返回 {canonical_base: 24h USDT 成交额}；失败返回空 dict（不踢币）。"""
    qv: Dict[str, float] = {}
    fn = getattr(client, "fetch_swap_tickers", None)
    if not callable(fn):
        return qv
    try:
        for it in client.fetch_swap_tickers():
            base = str(it.get("ccxt", "")).split("/")[0]
            qv[base] = float(it.get("quote_volume") or 0)
    except Exception as e:
        logger.warning(f"[XS运行时] 全市场快照获取失败，本次跳过流动性筛选: {e}")
    return qv


def _last_price_map(client: Any) -> Dict[str, float]:
    """全市场快照里的最新价，用于平仓"今天跌出池/被踢"但仍有报价的币。"""
    px: Dict[str, float] = {}
    fn = getattr(client, "fetch_swap_tickers", None)
    if not callable(fn):
        return px
    try:
        for it in client.fetch_swap_tickers():
            base = str(it.get("ccxt", "")).split("/")[0]
            last = float(it.get("last") or 0)
            if last > 0:
                px[base] = last
    except Exception:
        pass
    return px


def preview_basket(client: Any, *, k: int = 5, lookback: int = 288, gross: float = 2.0,
                   bases: Optional[List[str]] = None) -> Dict[str, Any]:
    """纯信号预览（不下单、不读持仓），供①篮子端点使用；与③调仓同一套固定池/符号解析/看门狗。"""
    cfg = get_config()
    bases = bases or FIXED_UNIVERSE
    if not getattr(client, "is_connected", False):
        return {"success": False, "detail": "生成XS篮子需要先连接OKX读取实时K线（信号不会下单）"}
    closes, failed, _ = _fetch_klines(client, bases, lookback)
    CL = pd.DataFrame(closes).sort_index()
    if CL.shape[1] < 2 * k + 5:
        return {"success": False, "detail": f"有效K线币种仅{CL.shape[1]}个，不足{2*k+5}个，放弃生成篮子（不凑数）",
                "kline_failed": failed}
    good = list(CL.columns)
    rejected: List[Dict[str, Any]] = []
    if cfg["watchdog_enabled"]:
        qv = _quote_volume_map(client)
        good, rejected = watchdog_screen(good, qv, cfg["watchdog_liquidity_ratio"])
    if len(good) < 2 * k + 5:
        return {"success": False, "detail": f"看门狗过滤后可排名币仅{len(good)}个，不足{2*k+5}",
                "kline_failed": failed, "watchdog_rejected": rejected}
    score = XS.momentum_score(CL, lookback)
    basket = XS.select_basket(score, k=k, tradable=good)
    if basket.get("reason") != "ok":
        return {"success": False, "detail": basket.get("reason", "篮子生成失败")}
    w = XS.target_weights(basket, gross=gross)
    members = list(basket["longs"]) + list(basket["shorts"])
    return {"success": True, "as_of": str(CL.index[-1]), "lookback_5m": lookback, "k": k, "gross": gross,
            "universe_used": CL.shape[1], "kline_failed": failed, "watchdog_rejected": rejected,
            "longs": basket["longs"], "shorts": basket["shorts"],
            "weights": {s: round(float(w[s]), 4) for s in members},
            "momentum_24h": {s: round(float(score[s]), 5) for s in members}}


def run_rebalance(client: Any, *, live: bool, confirm: bool, equity: Optional[float] = None,
                  manual: bool = False, k: Optional[int] = None,
                  lookback: Optional[int] = None, gross: Optional[float] = None,
                  bases: Optional[List[str]] = None,
                  cfg_override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """手动/定时共用的一次完整调仓。live=False 仅干跑（自动调度一定传 live=True）。
    全程异常捕获，返回结构化结果；任何失败都不抛穿，便于网页与定时器安全调用。"""
    global _running, _state
    with _LOCK:
        if _running:
            return {"success": False, "detail": "上一次 XS 调仓仍在执行中，本次跳过，避免重复下单"}
        _running = True
    try:
        cfg = dict(get_config())
        if cfg_override:
            cfg.update(cfg_override)
        bases = bases or FIXED_UNIVERSE
        k = max(1, int(k if k is not None else cfg["k"]))
        lookback = max(24, int(lookback if lookback is not None else cfg["lookback"]))
        gross = float(gross if gross is not None else cfg["gross"])

        if live and not confirm:
            return {"success": False, "detail": "实盘调仓需二次确认（live=true 且 confirm=true），本次未下单"}
        if not getattr(client, "is_connected", False):
            return {"success": False, "detail": "XS调仓需要先连接 OKX"}

        # 1) 权益 —— 目标仓位必须锚定【账户总权益 totalEq】（含占用保证金/浮盈浮亏）。
        #    绝不能用“可用余额 availEq”：已持仓时可用余额=总权益−占用保证金，会大幅偏小（如总权益74、
        #    可用仅23），若用它算目标仓位会导致每腿只剩 1/3、纯新开仓币低于最小下单额被跳过、仓位缩水。
        #    资金释放由执行器“先平仓后开仓”保证，不依赖调仓当下的可用余额。
        eq = equity
        avail_eq = None
        if not eq:
            try:
                bal = client.get_balance()
                eq = float(bal.get("equity") or bal.get("adjusted_equity") or bal.get("available_equity") or 0)
                avail_eq = float(bal.get("available_equity") or 0)
            except Exception:
                eq = 0.0
        if not eq or eq <= 0:
            return {"success": False, "detail": "取不到账户总权益且未传 equity，无法计算仓位，拒绝下单"}

        # 2) 拉 K 线
        closes, kline_failed, sym_map = _fetch_klines(client, bases, lookback)
        CL = pd.DataFrame(closes).sort_index()
        if CL.shape[1] < 2 * k + 5:
            return {"success": False, "detail": f"有效K线币种仅 {CL.shape[1]} 个，不足 {2*k+5}，放弃本次调仓（不凑数）",
                    "kline_failed": kline_failed}

        # 3) 看门狗（快出）
        good_bases = list(CL.columns)
        liq_rejected: List[Dict[str, Any]] = []
        qv_map: Dict[str, float] = {}
        if cfg["watchdog_enabled"]:
            qv_map = _quote_volume_map(client)
            good_bases, liq_rejected = watchdog_screen(good_bases, qv_map, cfg["watchdog_liquidity_ratio"])
        if len(good_bases) < 2 * k + 5:
            return {"success": False, "detail": f"看门狗过滤后可排名币仅 {len(good_bases)} 个，不足 {2*k+5}，放弃本次调仓",
                    "kline_failed": kline_failed, "watchdog_rejected": liq_rejected}

        # 4) 持仓（用于平掉跌出/被踢的币、以及熔断全平）
        try:
            positions = client.get_positions()
        except Exception:
            positions = []
        held = XE.positions_to_signed(positions)
        held_bases = [b for b, a in held.items() if abs(a) > 0]

        # 5) 熔断判定（较上一调仓日权益回撤）
        anchor = _state.get("equity_anchor")
        breaker = {"enabled": cfg["breaker_enabled"], "triggered": False, "pct": None, "reason": ""}
        trig, dd = _breaker_triggered(anchor, eq, cfg["breaker_enabled"], cfg["breaker_pct"])
        if trig:
            breaker.update(triggered=True, pct=dd,
                           reason=f"较上一调仓日权益 {float(anchor):.0f} 回撤 {dd*100:.1f}%，触发 {cfg['breaker_pct']*100:.0f}% 宽熔断，今天全平且不开新仓")

        # 6) 目标篮子 / 目标权重
        if breaker["triggered"]:
            basket = {"longs": [], "shorts": [], "reason": "breaker"}
            w = pd.Series(0.0, index=sorted(set(held_bases)))
        else:
            score = XS.momentum_score(CL, lookback)
            basket = XS.select_basket(score, k=k, tradable=good_bases)
            if basket.get("reason") != "ok":
                return {"success": False, "detail": basket.get("reason", "篮子生成失败"),
                        "kline_failed": kline_failed, "watchdog_rejected": liq_rejected}
            w = XS.target_weights(basket, gross=gross)

        # 7) 价格：目标币用 K 线收盘；持仓但今天被踢/跌出的币用快照最新价；都没有则进 blocked
        prices: Dict[str, float] = {s: float(CL[s].iloc[-1]) for s in CL.columns}
        if held_bases:
            snap_px = _last_price_map(client)
            for b in held_bases:
                if b not in prices or not np.isfinite(prices.get(b, np.nan)):
                    if snap_px.get(b):
                        prices[b] = snap_px[b]

        # 8) 计划订单（风控 + 精确换手；坏币进 blocked 而不是整批拒单）
        plan = XE.plan_orders(w, prices, positions, float(eq), max_gross=gross)
        if not plan.get("ok"):
            return {"success": False, "detail": f"风控拦截：{plan.get('reason')}，本次未下单",
                    "watchdog_rejected": liq_rejected, "kline_failed": kline_failed}

        # 9) 执行（maker_market 或 market）
        entry_mode = "maker_market" if cfg["maker_enabled"] else "market"
        result = XE.execute_plan(
            plan, client, live=live,
            entry_mode=entry_mode,
            maker_ttl=cfg["maker_ttl"],
            maker_improve_bps=cfg["maker_improve_bps"])

        mode = "实盘已下单" if live else "干跑预览(未下单)"
        if breaker["triggered"]:
            mode = "熔断全平" + ("(已下单)" if live else "(干跑)")
        members = list(w.index)
        summary = {
            "success": True, "mode": mode, "live": bool(live), "auto": not manual,
            "equity": round(float(eq), 2), "equity_drawdown_vs_anchor": round(dd, 4) if dd is not None else None,
            "as_of": str(CL.index[-1]), "universe_used": CL.shape[1], "k": k, "gross": gross,
            "longs": basket.get("longs", []), "shorts": basket.get("shorts", []),
            "weights": {s: round(float(w[s]), 4) for s in members},
            "entry_mode": entry_mode,
            "plan": [{"symbol": s["symbol"], "side": s["side"], "notional": s["notional"],
                      "reduce_only": s["reduce_only"], "why": s["why"]} for s in plan["steps"]],
            "executed": result.get("executed", []), "failed": result.get("failed", []),
            "blocked": result.get("blocked", []),
            "maker_filled": result.get("maker_filled", 0), "market_fallback": result.get("market_fallback", 0),
            "kline_failed": kline_failed, "watchdog_rejected": liq_rejected,
            "breaker": breaker,
            "breaker_triggered": bool(breaker["triggered"]),
            "n_orders": plan.get("n_orders", 0),
        }

        # 10) 实盘成交后更新运行状态
        if live:
            with _LOCK:
                today = _now_utc().strftime("%Y-%m-%d")
                _state["last_run_date"] = today
                _state["last_run_at"] = time.time()
                _state["last_run"] = {"date": today, "mode": mode, "as_of": summary["as_of"],
                                      "n_orders": summary["n_orders"],
                                      "failed": len(summary["failed"]),
                                      "blocked": len(summary["blocked"]),
                                      "longs": summary["longs"], "shorts": summary["shorts"],
                                      "breaker": breaker["triggered"]}
                if breaker["triggered"]:
                    _state["breaker"] = {"active": True, "date": today,
                                         "pct": breaker["pct"], "reason": breaker["reason"]}
                    _state["equity_anchor"] = round(float(eq), 2)   # 熔断后重置基准，次日恢复
                else:
                    _state["breaker"] = {"active": False, "date": "", "pct": None, "reason": ""}
                    _state["equity_anchor"] = round(float(eq), 2)
                _save_state_locked()
            _kick_names = ",".join(str(x.get("base", "")) for x in liq_rejected) if liq_rejected else ""
            _kick_txt = f"踢币{len(liq_rejected)}" + (f"[{_kick_names}]" if _kick_names else "")
            _log(f"{mode} | 权益{eq:.0f} | 多{summary['longs']} 空{summary['shorts']} | "
                 f"单{summary['n_orders']} maker{summary['maker_filled']} 市价补{summary['market_fallback']} "
                 f"失败{len(summary['failed'])} 卡住{len(summary['blocked'])} {_kick_txt}"
                 + (" | 熔断" if breaker["triggered"] else ""),
                 "error" if (summary["failed"] or summary["blocked"]) else "info")
        return summary
    except Exception as e:
        logger.exception("[XS运行时] 调仓异常")
        return {"success": False, "detail": f"XS调仓异常: {e}"}
    finally:
        with _LOCK:
            _running = False


# ============================ 定时调度 ============================
_scheduler_thread: Optional[threading.Thread] = None


def start_scheduler(client_provider: Callable[[], Any]) -> bool:
    """FastAPI 启动时调用，传入返回 okx_client 的函数（避免循环 import）。幂等。
    返回 True 表示本次真的启动了线程，False 表示线程已在运行。"""
    global _thread_started, _client_provider, _scheduler_thread
    _client_provider = client_provider
    if _thread_started and _scheduler_thread is not None and _scheduler_thread.is_alive():
        return False
    _thread_started = True
    _scheduler_thread = threading.Thread(target=_scheduler_loop, name="xs-scheduler", daemon=True)
    _scheduler_thread.start()
    _log(f"定时调仓线程已启动（开关={get_config()['schedule_enabled']}，UTC {get_config()['schedule_time']}）")
    return True


def stop_scheduler(timeout: float = 2.0) -> None:
    """停止调度循环（主要用于测试/优雅关闭；daemon 线程随进程退出也会结束）。"""
    global _thread_started, _scheduler_thread
    _thread_started = False
    th = _scheduler_thread
    if th is not None and th.is_alive():
        th.join(timeout=timeout)
    _scheduler_thread = None


def _scheduler_loop() -> None:
    while _thread_started:
        try:
            scheduler_tick()
        except Exception as e:
            logger.warning(f"[XS运行时] 调度心跳异常: {e}")
        # 可被 stop_scheduler 提前唤醒：分段 sleep
        slept = 0.0
        while _thread_started and slept < _tick_interval:
            time.sleep(min(1.0, _tick_interval - slept)); slept += 1.0


def scheduler_tick(now: Optional[dt.datetime] = None) -> Optional[Dict[str, Any]]:
    """到点且当天未跑则自动实盘调仓。纯逻辑可单测（now 可注入）。
    返回 {'ran': bool, 'reason': ..., 'result': ...}，便于排查与测试。"""
    cfg = get_config()
    now = now or _now_utc()
    today = now.strftime("%Y-%m-%d")
    hhmm = now.strftime("%H:%M")
    with _LOCK:
        if not cfg["schedule_enabled"]:
            return {"ran": False, "reason": "disabled"}
        if _state.get("last_run_date") == today:
            return {"ran": False, "reason": "already_run"}
        if _running:
            return {"ran": False, "reason": "running"}
        if hhmm < cfg["schedule_time"]:
            return {"ran": False, "reason": "not_due"}  # 零填充 HH:MM 字符串比较等价于时间比较
        client = _client_provider() if _client_provider else None
        if client is None or not getattr(client, "is_connected", False):
            _log("到点但 OKX 未连接，本次不调，稍后心跳会重试（保证当天最终能调）", "warning")
            return {"ran": False, "reason": "not_connected"}
    _log(f"到点（UTC {cfg['schedule_time']}）开始自动实盘调仓")
    res = run_rebalance(client, live=True, confirm=True, manual=False)
    if res.get("success"):
        # 兜底：由调度器保证"当天只跑一次"（run_rebalance 也会记录详情，此处幂等覆盖）
        with _LOCK:
            if _state.get("last_run_date") != today:
                _state["last_run_date"] = today
                _state["last_run_at"] = time.time()
                _save_state_locked()
        return {"ran": True, "reason": None, "result": res}
    _log(f"自动调仓失败：{res.get('detail')}，下一次心跳重试", "error")
    return {"ran": False, "reason": "rebalance_failed", "result": res}
