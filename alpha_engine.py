"""ALPHA-X ULTRA
独立于旧 ML/Trader 的自适应量化系统。

核心原则：
- 3 类方向模型 + NO-TRADE 市场状态
- 研发/验证/最终测试严格隔离，最终测试不参与参数选择
- 多窗口 walk-forward 选择，而不是单一 holdout
- 无 bfill；1h 只使用已完成小时柱
- 标签与回测的 TP/SL 定义一致，保守处理同柱双触发
- Paper 与 Live 状态分离；Live 风控以 OKX 真实账户权益为准
- Live 只执行 ALPHA-X 自己登记的仓位，未知仓位绝不接管
"""
from __future__ import annotations
import json, math, os, threading, time, traceback
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
import numpy as np
import pandas as pd
from loguru import logger
from sklearn.ensemble import RandomForestClassifier, ExtraTreesClassifier, HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import log_loss, accuracy_score
from okx_client import okx_client
from config import config
from alpha_live import alpha_live
from alpha_ultra_stack import build_context, stress_multiplier
from alpha_institutional import strategy_committee, risk_adjusted_confidence
from alpha_ledger import record as ledger_record, trade_attribution, recent as ledger_recent
from alpha_research import register as research_register, status as research_status, drift as research_drift
from alpha_attribution import trade_metrics as alpha_trade_metrics, summary as alpha_trade_summary, full_report as alpha_full_attribution
from alpha_governance import compare as governance_compare
from alpha_autonomy import regime_attribution, execution_summary, autonomous_decision
from alpha_risk import risk_budget, covariance_weights
from alpha_ops import record as ops_record, open_breaker as ops_open_breaker, breaker_status as ops_breaker_status, client_id as ops_client_id, open_orders as ops_open_orders
from alpha_production_integrated import pretrade_pipeline, issue_execution_fence, authorize_execution, renew_execution_fence
from alpha_ws import AlphaPrivateWS
from alpha_multi_core import meta_input, fit_meta, meta_proba, blend_proba, meta_explanation, factor_dict
from alpha_alpha_research2 import source_audit, parameter_stability, CostModel, stressed_edge, select_meta_oof
from alpha_real_data import RealDataStore, augment_candles, augment_candles_selected, source_gate, cross_market_from_store
from alpha_regime import classify_regime
from alpha_multi_timeframe import analyze_multi_timeframe
from alpha_lead_lag import analyze_lead_lag
from alpha_strategy_health import record_trade as strategy_health_record, advise as strategy_health_advise, report as strategy_health_report
from alpha_entry_optimizer import evaluate as evaluate_entry_location
from alpha_fast_mode import predict as fast_predict, check as fast_data_check, position_reversal_check as fast_position_reversal_check
import alpha_fast_mode as _fast_mode
import alpha_fast_universe as fast_universe
import alpha_fast_v6 as fast_v6
import alpha_fast_v7 as fast_v7
import tv_universe

ROOT = Path(__file__).parent
STORE = ROOT / "alpha_models"
STORE.mkdir(exist_ok=True)
STATE_FILE = ROOT / "alpha_state.json"
# 本进程启动时刻/身份：网页据此确认“看到的后端”是不是正在下单的那个实例、有没有被重启、来自哪个文件夹。
PROCESS_START_TS = time.time()
PROCESS_PID = os.getpid()
PROCESS_CWD = str(ROOT)
# 每次交付包的可见版本标识：网页据此显示，用户可一眼确认“打开的是不是最新包”。
ALPHA_BUILD = "FAST-v7 · build 2026-09-27（V7自制终端+涨幅榜自动交易；旧模式完整保留）"
VERSION = "ALPHA-X-INSTITUTIONAL-15.0-ALPHA-CORE-RESEARCH2"

DEFAULT = {
    "timeframe": "15m", "lookback": 10000,
    "dev_ratio": 0.70, "purge_bars": 48,
    "fee_pct": 0.0005, "slippage_pct": 0.0005,
    "risk_pct": 0.05, "leverage": 3, "max_positions": 4, "max_same_side": 0,
    "max_notional_pct": 0.40,
    # CVaR: soft threshold resizes moderate tail risk; hard threshold is a true block.
    "risk_cvar_soft_limit": 0.05, "risk_cvar_hard_limit": 0.12,
    "cooldown_minutes": 15,
    "max_daily_loss_pct": 0.30, "max_consecutive_losses": 30,
    "model_max_age_days": 7, "min_val_trades": 8, "min_test_trades": 8,
    "entry_min": 0.52, "entry_max": 0.78,
    "tp_grid": [0.012, 0.018, 0.025, 0.035],
    "sl_grid": [0.008, 0.012, 0.018, 0.025],
    "horizon_grid": [12, 24, 40],
    "walk_forward_folds": 3,
    "paper_start_balance": 10000.0,
    "real_data_mode": "off",
    "real_data_db": str(ROOT / "alpha_market_data.sqlite3"),
    "real_data_min_coverage": 0.80,
    "real_data_cross_market": False,
    # Opportunity-preserving execution: soften intermediate filters, retain hard safety gates.
    "min_execution_confidence": 0.50,
    "hard_execution_confidence": 0.44,
    "trial_execution_confidence": 0.33,
    "trial_hard_confidence": 0.26,
    "max_execution_stress": 0.92,
    "hard_execution_stress": 0.985,
    "spread_risk_soft": 0.0025,
    "spread_risk_hard": 0.0045,
    "min_liquidity_multiple": 5.0,
    "max_impact_bps": 30.0,
    "clock_max_skew_ms": 3000,
    # Exit controller: preserve the original TP+SL path; trail is an opt-in handoff.
    "trend_trail_enabled": True,
    "trail_prepare_progress": 0.72,
    "trail_switch_progress": 0.90,
    "trail_min_trend_score": 0.012,
    "trail_min_strength": 1.15,
    "trail_callback_atr": 2.2,
    "trail_min_distance_pct": 0.006,
    "trail_max_distance_pct": 0.035,
    "trail_amend_seconds": 20,
    "trail_tp_min_progress_pct": 0.003,
    "trail_tp_max_extension_pct": 0.12,
    "trail_tp_freeze_score": 0.55,
    "trail_tp_rearm_score": 0.72,
    "trail_tp_min_step_pct": 0.002,
    # Exchange-hosted native trailing stop defaults.
    "native_trail_activation_pct": 0.02,
    "native_trail_callback_pct": 0.02,
}

# 页面可实时控制的交易前拦截开关。每个开关都由后端真实读取，默认全部开启。
DEFAULT_GATE_SWITCHES = {
    "model_ready": True,
    "duplicate_position": True,
    "max_positions": True,
    "cooldown": True,
    "risk_budget": True,
    "market_quality": True,
    "liquidity": True,
    "capacity": True,
    "impact_cost": True,
    "spread": True,
    "confidence": True,
    "stress": True,
    "portfolio_weight": True,
    "reconciliation": True,
    "execution": True,
    "recovery_health": True,
    "model_gate": True,
    "clock": True,
    "audit": True,
    "ha_fencing": True,
    # Optional trend-trailing handoff. OFF = original ALPHA-X TP+SL only.
    "trend_trail": True,
    # Optional staged profit-taking. OFF by default so existing behavior is unchanged.
    "partial_tp": False,
    # Optional adaptive context: Regime + 4H/1H/15M/5M resonance. OFF restores the original path.
    "regime_mtf": True,
    # Optional cross-asset Lead-Lag context; never a hard entry block.
    "lead_lag": True,
    # Optional final entry-price confirmation; OFF fully bypasses this layer.
    "entry_price": True,
    # V6.3 research-strategy live permission. Explicit and OFF by default: when ON,
    # V6.3 signals pass through the SAME full institutional live pipeline as others.
    "v63_live": False,
}

LOCK = threading.RLock()
CONTROL_LOCK = threading.RLock()
LOOP_THREAD = None
RUNNING_CFG = None   # 交易循环正在使用的配置（同一个对象）；运行中修改仓位/风险/杠杆就改它

def _control_serialized(fn):
    from functools import wraps
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with CONTROL_LOCK:
            return fn(*args, **kwargs)
    return wrapped

ALPHA_WS = None
INTEGRATION_FENCE = None
FENCE_HEARTBEAT_THREAD = None
FENCE_STOP_EVENT = threading.Event()
TRAIN_LOCKS_LOCK = threading.RLock()
TRAIN_SYMBOL_LOCKS = {}
# 记录所有交易周期当前实际运行的训练币种数量；不改变每个周期最多3币并行，只动态分配树模型CPU。
ACTIVE_TRAIN_LOCK = threading.RLock()
ACTIVE_TRAINING_TASKS = 0
STATE = {
    "running": False, "mode": "paper", "started_at": None, "last": {},
    "positions": {}, "history": [], "paper_trades": [], "live_trades": [],
    "balance": 10000.0, "equity": 10000.0, "live_start_equity": None, "live_error": "", "risk_block": "",
    "day_start_equity": 10000.0, "day": "", "consecutive_losses": 0,
    "last_cycle": None, "cycle_errors": 0, "ws": {}, "attribution": [],
    "gate_switches": dict(DEFAULT_GATE_SWITCHES),
    "strategy_mode": "SHORT",
    "auto_select": False,
    "close_recovery": {},
    "protection_blocks": {},
    "flat_cleanup_pending": {},
    "flat_fill_pending": {},
    "maker_skip_until": {},
    "v6_attempted": {},
    "v7_attempted": {},
    "maker_stats": {"signals": 0, "filled": 0, "no_fill": 0, "partial": 0, "market_fallback": 0, "by_status": {}},
    "fast_entry_mode": str(os.getenv("OKX_FAST_ENTRY_MODE", "market") or "market").strip().lower(),
    "maker_params": {},
    "fast_engine_version": str(os.getenv("OKX_FAST_ENGINE", "v4") or "v4").strip().lower(),
    "fast_runtime": {"eval": 0, "flat": 0, "signal": 0, "error": 0,
                     "last_eval_ts": 0.0, "last_eval_symbol": "",
                     "last_signal_ts": 0.0, "last_signal_symbol": "", "last_signal": ""},
}

# === FAST maker(post-only 限价)进场配置 ===
# OKX_FAST_ENTRY_MODE:
#   market       = 现状：市价 taker 进场（默认，行为不变）
#   maker        = 只挂 post-only 限价单，成交即 maker；超时/价格跑掉就撤单不追（推荐模拟盘验证后使用）
#   maker_market = 先挂 post-only，超时未成交再回退市价 taker（保证成交，但可能又付 taker）
FAST_ENTRY_MODE = str(os.getenv("OKX_FAST_ENTRY_MODE", "market") or "market").strip().lower()
FAST_MAKER_TTL = float(os.getenv("OKX_FAST_MAKER_TTL", "45") or 45)            # 每笔挂单最长等待秒数
FAST_MAKER_REPRICE = int(float(os.getenv("OKX_FAST_MAKER_REPRICE", "2") or 2)) # post-only 提交被拒时按新盘口重挂次数
FAST_MAKER_IMPROVE_BPS = float(os.getenv("OKX_FAST_MAKER_IMPROVE_BPS", "0.5") or 0.5)  # 在最优买/卖价基础上向中间靠多少bps（提高成交概率，仍保证maker）
FAST_MAKER_CHASE_BPS = float(os.getenv("OKX_FAST_MAKER_CHASE_BPS", "12") or 12)        # 价格朝有利方向跑掉多少bps就放弃不追
FAST_MAKER_SKIP_SECONDS = int(float(os.getenv("OKX_FAST_MAKER_SKIP_SECONDS", "240") or 240))  # 一次未成交后，同币冷却多久再挂

# maker 参数允许范围（页面/接口改值时做钳制，防止填错导致挂单异常）
_MAKER_PARAM_BOUNDS = {
    "ttl": (5.0, 180.0), "reprice": (0, 5), "improve_bps": (0.0, 20.0),
    "chase_bps": (2.0, 100.0), "skip_seconds": (0, 1800),
}
FAST_ENTRY_MODES = ("market", "maker", "maker_market")


def _fast_entry_mode() -> str:
    """当前进场方式：优先读运行时/页面设置（持久化在 STATE），其次读环境变量默认。"""
    m = str(STATE.get("fast_entry_mode") or FAST_ENTRY_MODE or "market").strip().lower()
    return m if m in FAST_ENTRY_MODES else "market"


def _maker_params() -> dict:
    """当前 maker 参数：页面运行时值覆盖环境变量默认。"""
    base = {"ttl": FAST_MAKER_TTL, "reprice": FAST_MAKER_REPRICE, "improve_bps": FAST_MAKER_IMPROVE_BPS,
            "chase_bps": FAST_MAKER_CHASE_BPS, "skip_seconds": FAST_MAKER_SKIP_SECONDS}
    try:
        base.update({k: v for k, v in (STATE.get("maker_params") or {}).items() if k in base})
    except Exception:
        pass
    return base


def set_fast_entry(mode: str = None, params: dict = None):
    """页面/接口实时设置进场方式与 maker 参数；立即生效并持久化。返回当前配置。"""
    with LOCK:
        if mode is not None:
            mode = str(mode).strip().lower()
            if mode not in FAST_ENTRY_MODES:
                raise ValueError(f"未知进场方式: {mode}")
            STATE["fast_entry_mode"] = mode
        if params:
            mp = dict(STATE.get("maker_params") or {})
            for k, v in params.items():
                if v is None or k not in _MAKER_PARAM_BOUNDS:
                    continue
                lo, hi = _MAKER_PARAM_BOUNDS[k]
                try:
                    val = float(v)
                except Exception:
                    continue
                val = max(lo, min(hi, val))
                mp[k] = int(val) if k in ("reprice", "skip_seconds") else float(val)
            STATE["maker_params"] = mp
    _persist()
    return get_fast_entry_config()


def get_fast_entry_config() -> dict:
    return {"mode": _fast_entry_mode(), "params": _maker_params(),
            "modes": list(FAST_ENTRY_MODES), "stats": STATE.get("maker_stats") or {}}


def _fast_maker_enabled(pred: dict) -> bool:
    """仅 FAST 策略且显式开启 maker/maker_market 时启用；其它策略与默认都走原市价路径。"""
    if not _is_fast(pred):
        return False
    return _fast_entry_mode() in ("maker", "maker_market", "postonly", "post_only")


# === FAST 策略引擎版本（页面可切，实时生效并持久化）===
# v5        = v4 区间回归 + 三道质量闸门（1H延伸/4H趋势/布林爆炸/实体确认），趋势回踩 sleeve 默认关
# v4        = 区间双引擎，默认只跑区间均值回归（趋势 sleeve 关）——回测 maker 下 66%/PF1.11
# v4_trend  = v4 基础上额外打开趋势延续 sleeve（实验，该 sleeve 在 INS/OOS 未验证扣费后正 edge）
# v3        = 完全回退旧版纯裸K（taker 下 37.8%/PF0.57 亏钱，仅对比/紧急回退，勿实盘）
FAST_ENGINE_VERSIONS = ("v7", "v62", "v6", "v5", "v4", "v4_trend", "v3")
_FAST_ENGINE_FLAGS = {
    "v7":       (True, False),
    "v62": (True, False),
    "v6":       (True, False),
    "v5":       (True, False),   # v5 由 FAST_ACTIVE_VERSION 路由，这里给安全的 v4 基线开关
    "v4":       (True, False),   # (FAST_V4_ENSEMBLE, FAST_V4_TREND_SLEEVE)
    "v4_trend": (True, True),
    "v3":       (False, False),
}
FAST_ENGINE_CN = {
    "v7": "V7 自制交易终端·涨幅榜选币+指标信号自动交易（模拟优先，未验证盈利）",
    "v62": "V6.3 全市场状态自适应裸K（本地模拟，未通过压力盈利验证）",
    "v6": "V6.1 结构止盈与盈利保护（实验版）",
    "v5": "v5 自适应（区间回归+质量闸门：少而精，建议maker，小币实测）",
    "v4": "v4 区间双引擎（震荡高抛低吸）",
    "v4_trend": "v4 + 趋势sleeve（实验：强趋势里额外追单，未验证盈利）",
    "v3": "v3 旧版纯裸K（回测扣费后亏钱，仅对比/回退，勿实盘）",
}


def _fast_engine_version() -> str:
    v = str(STATE.get("fast_engine_version") or "v4").strip().lower()
    return v if v in FAST_ENGINE_VERSIONS else "v4"


def _apply_fast_engine(v: str) -> None:
    """把引擎版本落到 alpha_fast_mode 的模块级开关；mode 内为运行时全局读取，立即生效。"""
    ens, sleeve = _FAST_ENGINE_FLAGS[v]
    _fast_mode.FAST_V4_ENSEMBLE = ens
    _fast_mode.FAST_V4_TREND_SLEEVE = sleeve
    # v5 由独立版本号路由；v3/v4/v4_trend 仍只看上面两个布尔开关（旧行为零改动）。
    if hasattr(_fast_mode, "set_active_version"):
        _fast_mode.set_active_version(v)
    else:
        _fast_mode.FAST_ACTIVE_VERSION = v


def set_fast_engine(version: str = None):
    """页面/接口实时切换策略引擎版本；立即生效并持久化。返回当前配置。"""
    version = str(version or "").strip().lower()
    if version not in FAST_ENGINE_VERSIONS:
        raise ValueError(f"未知策略引擎版本: {version}")
    with LOCK:
        STATE["fast_engine_version"] = version
        _apply_fast_engine(version)
    _persist()
    return get_fast_engine_config()


def get_fast_engine_config() -> dict:
    v = _fast_engine_version()
    ens, sleeve = _FAST_ENGINE_FLAGS[v]
    return {"version": v, "versions": list(FAST_ENGINE_VERSIONS), "labels": FAST_ENGINE_CN,
            "ensemble": ens, "trend_sleeve": sleeve, "label": FAST_ENGINE_CN[v]}


def _note_fast_eval(pred: dict, symbol: str = "") -> None:
    """记录 FAST 每次逐币评估的心跳（区分 在跑但FLAT / 真出信号 / 异常），供网页证明系统在扫描。"""
    try:
        sig = str((pred or {}).get("signal") or "FLAT").upper()
        now = time.time()
        with LOCK:
            rt = STATE.setdefault("fast_runtime", {"eval": 0, "flat": 0, "signal": 0, "error": 0,
                                                   "last_eval_ts": 0.0, "last_eval_symbol": "",
                                                   "last_signal_ts": 0.0, "last_signal_symbol": "", "last_signal": ""})
            rt["eval"] = int(rt.get("eval", 0)) + 1
            rt["last_eval_ts"] = now
            rt["last_eval_symbol"] = str(symbol or "")
            if sig in ("LONG", "SHORT"):
                rt["signal"] = int(rt.get("signal", 0)) + 1
                sid=(pred.get('fast_strategy') or {}).get('signal_id')
                seen=rt.setdefault('last_unique_signal',{})
                if sid and seen.get(symbol)!=sid:
                    seen[symbol]=sid
                    rt['unique_signal']=int(rt.get('unique_signal',0))+1
                rt["last_signal_ts"] = now
                rt["last_signal_symbol"] = str(symbol or "")
                rt["last_signal"] = sig
            elif sig == "FLAT":
                rt["flat"] = int(rt.get("flat", 0)) + 1
            else:
                rt["error"] = int(rt.get("error", 0)) + 1
    except Exception:
        pass


def _note_fast_phase(phase: str, **kw) -> None:
    """记录扫描所处阶段（selecting=全市场选币预热中 / scanning=已选完、逐币决策中），避免预热时网页误报‘可能停了’。"""
    try:
        with LOCK:
            rt = STATE.setdefault("fast_runtime", {"eval": 0, "flat": 0, "signal": 0, "error": 0,
                                                   "last_eval_ts": 0.0, "last_eval_symbol": "",
                                                   "last_signal_ts": 0.0, "last_signal_symbol": "", "last_signal": ""})
            rt["phase"] = str(phase)
            rt["phase_ts"] = time.time()
            for k, v in kw.items():
                rt[k] = v
    except Exception:
        pass


def get_fast_runtime() -> dict:
    """FAST 扫描心跳快照（含服务器当前时间，前端据此算‘几秒前还在扫’）。"""
    with LOCK:
        rt = dict(STATE.get("fast_runtime") or {})
    now = time.time()
    rt["server_ts"] = now
    rt["running"] = bool(STATE.get("running"))
    rt["mode"] = str(STATE.get("mode") or "")
    rt["auto_select"] = bool(STATE.get("auto_select"))
    # 进程身份：用来核对网页连的后端是不是正在下单的那个实例/文件夹、有没有重启过
    rt["start_ts"] = PROCESS_START_TS
    rt["uptime_s"] = max(0, int(now - PROCESS_START_TS))
    rt["pid"] = PROCESS_PID
    rt["cwd"] = PROCESS_CWD
    # 把后台最近的网络/选币错误透到网页（地区403、代理超时等一眼可见），不必翻黑终端
    err = str(STATE.get("live_error") or "").strip()
    rt["cycle_errors"] = int(STATE.get("cycle_errors") or 0)
    rt["last_cycle_ts"] = float(STATE.get("last_cycle") or 0.0)
    rt["last_error"] = err[:220]
    rt["last_error_ts"] = float(STATE.get("last_error_ts") or 0.0)
    loe = STATE.get("last_open_error") or {}
    if isinstance(loe, dict) and loe.get("error"):
        # 只回传堆栈最后 3 行（含文件与行号），够定位又不刷屏
        tail = str(loe.get("trace_tail") or "").strip().splitlines()
        rt["last_open_error"] = {"symbol": loe.get("symbol"), "error": str(loe.get("error"))[:220],
                                 "trace_tail": "\n".join(t for t in tail if t.strip())[-360:],
                                 "ts": float(loe.get("ts") or 0.0)}
    else:
        rt["last_open_error"] = None
    return rt


def _activity(message: str, level: str = "info") -> None:
    """统一运行活动日志：同时进入后台黑窗口和网页工作日志。"""
    try:
        fn = getattr(logger, level, logger.info)
        fn(f"[ALPHA-X 工作] {message}")
    except Exception:
        pass


def _persist() -> None:
    try:
        with LOCK:
            safe = {k: v for k, v in STATE.items() if k != "last"}
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(STATE_FILE)
    except Exception as exc:
        logger.warning(f"[ALPHA-X] 状态保存失败: {exc}")


def _load_state() -> None:
    if not STATE_FILE.exists():
        return
    try:
        old = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        with LOCK:
            for k in STATE:
                if k in old and k not in ("running", "last"):
                    STATE[k] = old[k]
            STATE["running"] = False
            STATE["last"] = {}
    except Exception as exc:
        logger.warning(f"[ALPHA-X] 状态读取失败，将使用空状态: {exc}")


_load_state()

# 兼容旧状态文件：缺少的新开关自动补齐为开启。
with LOCK:
    _gs = STATE.get("gate_switches") or {}
    STATE["gate_switches"] = {**DEFAULT_GATE_SWITCHES, **{k: bool(v) for k,v in _gs.items() if k in DEFAULT_GATE_SWITCHES}}

# 启动时把持久化的策略引擎版本落到 alpha_fast_mode 模块开关（默认 v4）。
_apply_fast_engine(_fast_engine_version())

def _managed_close(symbol: str, side: str):
    """统一管理强制平仓：失败即持久化残仓恢复状态，成功才清除。"""
    try:
        result=alpha_live.close(symbol,side)
        with LOCK:
            STATE.setdefault("close_recovery",{}).pop(symbol,None)
        _persist()
        return result
    except Exception as exc:
        with LOCK:
            STATE.setdefault("close_recovery",{})[symbol]={"side":side,"error":str(exc),"updated_at":time.time()}
            STATE["risk_block"]=f"{symbol} 平仓未归零，禁止重新开仓"
        _persist()
        raise

def _block_protection_failure(symbol: str, side: str, reason: str):
    """TP/SL protection failure creates a persistent symbol-level quarantine until manually cleared."""
    with LOCK:
        STATE.setdefault("protection_blocks", {})[symbol] = {"side": side, "reason": str(reason), "updated_at": time.time(), "manual_clear_required": True}
        STATE["risk_block"] = f"{symbol} TP/SL保护失败，禁止重新开仓，需人工解除保护锁"
    _persist()
    _activity(f"实盘 {symbol}：TP/SL保护失败，已建立保护锁；未人工解除前禁止再次开仓", "error")

def clear_protection_block(symbol: str = ""):
    with LOCK:
        blocks = STATE.setdefault("protection_blocks", {})
        removed = blocks.pop(symbol, None) if symbol else dict(blocks)
        if not symbol:
            blocks.clear()
    _persist()
    return {"ok": True, "cleared": removed}

def _resume_close_recovery() -> bool:
    """重启/下一轮优先恢复未完成平仓；存在残仓恢复任务时绝不允许新开仓。"""
    with LOCK:
        pending=dict(STATE.get("close_recovery") or {})
    if not pending:
        return True
    all_clear=True
    for symbol,row in pending.items():
        side=str((row or {}).get("side") or "")
        if not side:
            all_clear=False; continue
        try:
            _activity(f"实盘 {symbol}：发现未完成平仓恢复任务，优先清扫残仓，禁止新开仓", "warning")
            _managed_close(symbol,side)
            _activity(f"实盘 {symbol}：平仓恢复成功，交易所确认仓位归零")
        except Exception as exc:
            all_clear=False
            _activity(f"实盘 {symbol}：平仓恢复仍未完成，继续禁止新开仓：{exc}", "error")
    return all_clear

def gate_enabled(name: str) -> bool:
    with LOCK:
        return bool((STATE.get("gate_switches") or DEFAULT_GATE_SWITCHES).get(name, DEFAULT_GATE_SWITCHES.get(name, True)))

def _disable_trend_trail_runtime() -> dict:
    """One-click OFF: block new handoffs and restore active trailing positions to original TP+SL."""
    results = {}
    with LOCK:
        positions = {s: dict(p) for s,p in (STATE.get("positions") or {}).items()}
    for symbol, p in positions.items():
        if str(p.get("exit_mode") or "NORMAL") not in ("TREND_TRAIL","TRANSITION"):
            continue
        try:
            side=p.get("side"); tp=float(p.get("original_tp_price") or p.get("tp") or 0); sl=float(p.get("original_sl_price") or p.get("sl") or 0)
            protected_sl=float(p.get("sl") or sl)
            if p.get("partial_tp_sl_stage") and p.get("partial_tp_sl_stage")!="NONE":
                if side=="long": sl=max(sl,protected_sl)
                elif side=="short": sl=min(sl,protected_sl)
            tp_id=p.get("tp_attach_clordid"); sl_id=p.get("sl_attach_clordid")
            if not side or tp<=0 or sl<=0 or not tp_id or not sl_id:
                raise RuntimeError("缺少原TP/SL恢复所需状态")
            rb=alpha_live.restore_tp_sl(symbol,side,tp,sl,tp_id,sl_id)
            if not rb.get("verified"): raise RuntimeError(rb.get("error") or "原TP+SL恢复未验证")
            with LOCK:
                livep=STATE.get("positions",{}).get(symbol)
                if livep is not None:
                    livep.update({"exit_mode":"NORMAL","trail_active":False,"trail_sl":sl,"tp":tp,"sl":sl,"transition_error":""})
            ledger_record("TREND_TRAIL_DISABLED_ROLLBACK",symbol,{"tp":tp,"sl":sl})
            _activity(f"实盘 {symbol}：Trend Trailing开关已关闭，原TP+SL已恢复并验证，回到NORMAL","warning")
            results[symbol]={"restored":True}
        except Exception as exc:
            with LOCK:
                livep=STATE.get("positions",{}).get(symbol)
                if livep is not None:
                    livep["exit_mode"]="RECOVERY"; livep["trail_active"]=False; livep["transition_error"]=f"关闭Trend Trailing后的原保护恢复失败: {exc}"
            ops_open_breaker(f"TREND_TRAIL_DISABLE_RECOVERY_REQUIRED:{symbol}")
            _activity(f"实盘 {symbol}：Trend Trailing关闭后原TP+SL恢复失败，进入RECOVERY：{exc}","error")
            results[symbol]={"restored":False,"error":str(exc)}
    _persist(); return results

def _current_position_contracts(symbol: str, p: dict) -> float:
    cs=config.trading.get_ccxt_symbol(symbol); side=str(p.get("side") or "")
    try:
        rows=alpha_live.positions()
        for r in rows:
            if r.get("symbol")!=cs: continue
            try: mode=alpha_live.pos_mode()
            except Exception: mode="long_short_mode"
            if mode!="long_short_mode" or not side or r.get("side")==side:
                return float(r.get("contracts") or 0)
    except Exception as exc:
        logger.warning(f"[ALPHA-X] 读取实时仓位失败，使用本地数量: {exc}")
    return float(p.get("filled") or 0)


def _wait_algo_inactive(symbol: str, side: str, cid: str, timeout: float=5.0):
    deadline=time.time()+float(timeout)
    while time.time()<deadline:
        rows=alpha_live._protection_rows(symbol,side,[cid])
        row=next((x for x in rows if alpha_live._algo_client_id(x)==cid),None)
        if not row or not alpha_live._algo_is_active(row): return True
        time.sleep(0.25)
    return False


def _cancel_active_algo(symbol: str, side: str, cid: str):
    rows=alpha_live._protection_rows(symbol,side,[cid])
    row=next((x for x in rows if alpha_live._algo_client_id(x)==cid and alpha_live._algo_is_active(x)),None)
    if not row: return False
    alpha_live.cancel_algo(symbol,algo_cl_ord_id=cid)
    if not _wait_algo_inactive(symbol,side,cid,5.0):
        raise RuntimeError(f"算法单 {cid} 撤单后仍处于有效状态")
    return True


def _native_trail_config(p: dict, cfg: dict):
    if p.get("fast_version")=="v7": return fast_v7.native_trail_config(p)
    entry=float(p.get("entry") or 0); d=1 if p.get("side")=="long" else -1
    activation=float(cfg.get("native_trail_activation_pct",0.02))
    callback=float(cfg.get("native_trail_callback_pct",0.02))
    active=entry*(1+d*activation)
    return active, max(0.002,min(callback,0.10))


def _native_partial_specs(symbol: str, p: dict, contracts: float):
    side=str(p.get("side") or ""); entry=float(p.get("entry") or 0); d=1 if side=="long" else -1
    base_tp=float(p.get("base_tp_pct") or 0.02)
    amounts=alpha_live.split_contracts(symbol,contracts,[0.30,0.30,0.40])
    raw=[("TP1",0.50,amounts[0]),("TP2",0.80,amounts[1]),("FINAL",1.00,amounts[2])]
    specs=[]
    for name,progress,qty in raw:
        if qty<=0: continue
        if progress>=1.0:
            price=float(p.get("original_tp_price") or p.get("tp") or 0)
            if p.get("fast_version")=="v7" and p.get("v7_runner_active"):
                price=fast_v7.runner_target(p)
        else:
            target_pct=max(0.002,min(base_tp*progress,0.25))
            if p.get("fast_version")=="v6":
                target_pct=fast_v6.partial_target_pct(p,progress)
            elif p.get("fast_version")=="v7":
                target_pct=fast_v7.partial_target_pct(p,progress)
            if target_pct>=base_tp:
                continue
            price=entry*(1+d*target_pct)
        specs.append({"name":name,"price":float(price),"qty":float(qty)})
    return specs


def _arm_native_trail(symbol: str, p: dict, cfg: dict):
    if p.get("native_trail_armed"): return True
    side=str(p.get("side") or ""); qty=_current_position_contracts(symbol,p)
    if qty<=0: return False
    active,callback=_native_trail_config(p,cfg)
    fixed_sl=str(p.get("sl_attach_clordid") or "")   # 固定灾难SL，必须保留兜底
    trail=alpha_live.place_native_trailing(symbol,side,qty,callback,active)
    trail_id=trail.get("client_id")
    runner_attempted=False;runner_rolled_back=False
    try:
        status=alpha_live.protection_status_set(symbol,side,[trail_id],wait_timeout=3.0)
        if not status.get("verified"):
            raise RuntimeError("原生移动止损未保持有效")
        runner_price=None
        if p.get("fast_version")=="v7" and (p.get("v7_exit_config") or {}).get("runner"):
            runner_price=fast_v7.runner_target(p)
            runner_attempted=True
            result=alpha_live.amend_tp_only(symbol,side,runner_price,p.get("tp_attach_clordid"),wait_timeout=4.0)
            if not result.get("verified"):
                rollback=alpha_live.amend_tp_only(symbol,side,float(p.get("tp") or p.get("original_tp_price")),p.get("tp_attach_clordid"),wait_timeout=4.0)
                runner_rolled_back=bool(rollback.get("verified"))
                if not runner_rolled_back:raise RuntimeError("Runner目标更新与回滚未确认，保留固定SL并等待恢复")
                raise RuntimeError("Runner目标未确认，已恢复原目标")
        # 关键安全设计：绝不撤销固定SL。move_order_stop 设了激活价(activePx)，价格涨到
        # 激活价(默认+2%)之前移动单不触发；此期间若直接反向，由固定SL兜底。移动单激活后
        # 负责追踪，固定SL作为灾难止损与其并存，任一触发即平仓，残留单在平仓时统一清理。
        with LOCK:
            livep=STATE["positions"].get(symbol)
            if livep:
                livep["native_trail_id"]=trail_id
                livep["native_trail_armed"]=True
                livep["exit_mode"]="TREND_TRAIL"
                livep["trail_active"]=True
                livep["native_trail_active_price"]=active
                livep["native_trail_callback"]=callback
                if runner_price is not None:
                    livep["tp"]=runner_price; livep["v7_runner_active"]=True
                    for spec in livep.get("partial_tp_native_specs",[]):
                        if spec.get("name")=="FINAL":spec["price"]=runner_price
        _persist()
        _activity(f"实盘 {symbol}：已叠加OKX原生追踪止损（固定灾难SL保留兜底），激活价 {active:.8g}，回撤 {callback:.2%}")
        return True
    except Exception as exc:
        if runner_attempted and not runner_rolled_back:
            try:
                rb=alpha_live.amend_tp_only(symbol,side,float(p.get("tp") or p.get("original_tp_price")),p.get("tp_attach_clordid"),wait_timeout=4.0)
                runner_rolled_back=bool(rb.get("verified"))
            except Exception:runner_rolled_back=False
            if not runner_rolled_back:
                # Keep the confirmed native trail plus fixed SL; persist recovery identity.
                with LOCK:
                    livep=STATE["positions"].get(symbol)
                    if livep:
                        livep.update(native_trail_id=trail_id,native_trail_armed=True,exit_mode="RECOVERY",transition_error=str(exc))
                _persist()
                raise RuntimeError("Runner更新状态不明，已保留并登记追踪单及固定SL，等待恢复") from exc
        try: _cancel_active_algo(symbol,side,trail_id)
        except Exception: pass
        # 移动单接管失败：固定SL从未被撤，仓位仍有完整保护，直接向上抛错由上层回滚。
        raise RuntimeError(f"原生移动止损接管失败，固定止损仍在兜底: {exc}") from exc


def _disarm_native_trail(symbol: str, p: dict):
    side=str(p.get("side") or "")
    trail_id=str(p.get("native_trail_id") or "")
    if p.get("fast_version")=="v7" and p.get("v7_runner_active"):
        result=alpha_live.amend_tp_only(symbol,side,float(p.get("original_tp_price")),p.get("tp_attach_clordid"),wait_timeout=4.0)
        if not result.get("verified"):raise RuntimeError("关闭追踪前恢复原目标未确认，继续保留追踪单")
        with LOCK:
            livep=STATE["positions"].get(symbol)
            if livep:
                livep["tp"]=float(p["original_tp_price"]);livep["v7_runner_active"]=False
                for spec in livep.get("partial_tp_native_specs",[]):
                    if spec.get("name")=="FINAL":spec["price"]=float(p["original_tp_price"])
    # 固定SL在arm期间从未被撤，这里只需撤销叠加的移动单。
    if p.get("native_trail_armed") and trail_id:
        _cancel_active_algo(symbol,side,trail_id)
    # 确认固定灾难SL仍然有效；若意外丢失则立即按原止损补挂，绝不留裸仓。
    fixed_sl=str(p.get("sl_attach_clordid") or "")
    fixed_ok=False
    if fixed_sl:
        try:
            st=alpha_live.protection_status_set(symbol,side,[fixed_sl],wait_timeout=3.0)
            fixed_ok=bool(st.get("verified"))
        except Exception:
            fixed_ok=False
    if not fixed_ok:
        qty=_current_position_contracts(symbol,p)
        original_sl=float(p.get("original_sl_price") or p.get("sl") or 0)
        if qty>0 and original_sl>0:
            fixed=alpha_live.place_native_sl(symbol,side,qty,original_sl,trigger_type="mark")
            fixed_id=fixed.get("client_id")
            with LOCK:
                livep=STATE["positions"].get(symbol)
                if livep: livep["sl_attach_clordid"]=fixed_id
        else:
            raise RuntimeError("取消移动止损时固定SL缺失且无法补挂")
    with LOCK:
        livep=STATE["positions"].get(symbol)
        if livep:
            livep["native_trail_id"]=""
            livep["native_trail_armed"]=False
            livep["exit_mode"]="NORMAL"
            livep["trail_active"]=False
    _persist()
    _activity(f"实盘 {symbol}：已撤销OKX原生追踪止损，固定灾难SL继续兜底")
    return True


def _arm_native_partial(symbol: str, p: dict, cfg: dict):
    if p.get("native_partial_armed"): return True
    side=str(p.get("side") or ""); qty=_current_position_contracts(symbol,p)
    if qty<=0: return False
    specs=_native_partial_specs(symbol,p,qty)
    if len(specs)<2:
        with LOCK:
            livep=STATE["positions"].get(symbol)
            if livep: livep["native_partial_unavailable"]="仓位张数不足，无法满足最小分批单位"
        _persist()
        _activity(f"实盘 {symbol}：分批止盈未接管：交易所最小下单单位不支持拆分，保留原TP", "warning")
        return False
    old_tp=str(p.get("tp_attach_clordid") or ""); created=[]
    try:
        for spec in specs:
            r=alpha_live.place_native_tp(symbol,side,spec["qty"],spec["price"])
            created.append(r.get("client_id"))
            spec["client_id"]=r.get("client_id")
        if old_tp:
            _cancel_active_algo(symbol,side,old_tp)
        status=alpha_live.protection_status_set(symbol,side,created,wait_timeout=3.0)
        if not status.get("verified"): raise RuntimeError("原生分批止盈单未全部保持有效")
        with LOCK:
            livep=STATE["positions"].get(symbol)
            if livep:
                livep["partial_tp_order_ids"]=created
                livep["tp_attach_clordid"]=created[-1]
                livep["native_partial_armed"]=True
                livep["partial_tp_native_specs"]=specs
                livep["partial_tp_base_qty"]=qty
                livep["partial_tp_steps"]=["NATIVE_EXCHANGE_HOSTED"]
        _persist()
        _activity(f"实盘 {symbol}：分批止盈已改为OKX原生条件单并托管，共 {len(created)} 张止盈单")
        return True
    except Exception as exc:
        for cid in created:
            try: _cancel_active_algo(symbol,side,cid)
            except Exception: pass
        if old_tp:
            rows=alpha_live._protection_rows(symbol,side,[old_tp])
            if not any(alpha_live._algo_client_id(x)==old_tp and alpha_live._algo_is_active(x) for x in rows):
                try:
                    fallback=alpha_live.place_native_tp(symbol,side,qty,float(p.get("original_tp_price") or p.get("tp")))
                    with LOCK:
                        livep=STATE["positions"].get(symbol)
                        if livep: livep["tp_attach_clordid"]=fallback.get("client_id")
                    _persist()
                except Exception: pass
        raise RuntimeError(f"原生分批止盈接管失败，已尝试恢复原TP: {exc}") from exc


def _disarm_native_partial(symbol: str, p: dict):
    if not p.get("native_partial_armed"): return True
    side=str(p.get("side") or ""); qty=_current_position_contracts(symbol,p)
    original_tp=float(p.get("original_tp_price") or p.get("tp") or 0)
    if p.get("fast_version")=="v7" and p.get("v7_runner_active"): original_tp=fast_v7.runner_target(p)
    ids=list(p.get("partial_tp_order_ids") or [])
    if qty<=0 or original_tp<=0:
        for cid in ids:
            _cancel_active_algo(symbol,side,cid)
        return True
    full=alpha_live.place_native_tp(symbol,side,qty,original_tp)
    full_id=full.get("client_id")
    try:
        for cid in ids:
            _cancel_active_algo(symbol,side,cid)
        status=alpha_live.protection_status_set(symbol,side,[full_id],wait_timeout=3.0)
        if not status.get("verified"): raise RuntimeError("单一止盈单未保持有效")
        with LOCK:
            livep=STATE["positions"].get(symbol)
            if livep:
                livep["tp_attach_clordid"]=full_id
                livep["tp"]=original_tp
                livep["native_partial_armed"]=False
                livep["partial_tp_order_ids"]=[]
                livep["partial_tp_native_specs"]=[]
                livep["partial_tp_steps"]=[]
        _persist()
        _activity(f"实盘 {symbol}：已取消OKX原生分批止盈，恢复单一止盈 {original_tp:.8g}")
        return True
    except Exception as exc:
        try: _cancel_active_algo(symbol,side,full_id)
        except Exception: pass
        raise RuntimeError(f"取消原生分批止盈失败，已尝试保留原保护: {exc}") from exc


def _cleanup_flat_position_algos(symbol: str, p: dict):
    """仓位已确认归零：撤销该仓位所有本系统算法单(TP/SL/移动/分批)，交易所端兜底。"""
    side=str(p.get("side") or "")
    # 第一重：撤本地记录的全部订单ID（含叠加移动单 native_trail_id）
    ids=[p.get("tp_attach_clordid"),p.get("sl_attach_clordid"),p.get("native_trail_id")]
    ids.extend(p.get("partial_tp_order_ids") or [])
    seen=set()
    for cid in ids:
        cid=str(cid or "")
        if not cid or cid in seen: continue
        seen.add(cid)
        try: _cancel_active_algo(symbol,side,cid)
        except Exception as exc: logger.debug(f"[ALPHA-X] 本地记录单撤销 {cid}: {exc}")
    # 第二重：交易所端兜底，拉取并撤销该币所有 AX 前缀仍挂单，防止本地记录不全。
    try:
        res=alpha_live.cancel_all_my_algos(symbol,side)
        if res.get("cancelled"):
            _activity(f"实盘 {symbol}：交易所兜底撤销残留算法单 {len(res['cancelled'])} 张")
        # A successful cancel request is not proof that the algo is gone.
        remaining=[]; errors=list(res.get('errors') or [])
        for order_type in ('conditional','move_order_stop'):
            try:
                remaining.extend(x for x in alpha_live.pending_algos(symbol,order_type)
                    if str(x.get('algoClOrdId') or x.get('attachAlgoClOrdId') or '').startswith(alpha_live.PREFIX)
                    and (not side or str(x.get('posSide') or '').lower() in ('',side.lower())))
            except Exception as exc: errors.append(f'{order_type}复查失败:{exc}')
        if remaining:errors.append('残留订单:'+','.join(str(x.get('algoClOrdId') or x.get('algoId')) for x in remaining))
        res['errors']=errors
        if errors: raise RuntimeError('; '.join(errors))
        return res
    except Exception as exc:
        logger.warning(f"[ALPHA-X] 交易所兜底清理失败 {symbol}: {exc}")
        raise

def _flat_cleanup_or_block(symbol: str, p: dict) -> bool:
    try:
        _cleanup_flat_position_algos(symbol,p)
        with LOCK: STATE.setdefault('flat_cleanup_pending',{}).pop(symbol,None)
        return True
    except Exception as exc:
        with LOCK:
            STATE.setdefault('flat_cleanup_pending',{})[symbol]={**p,'cleanup_error':str(exc)}
            STATE.setdefault('protection_blocks',{})[symbol]={
                'side':p.get('side'),'reason':'已平仓但残留算法单未撤净: '+str(exc),
                'updated_at':time.time(),'manual_clear_required':False}
        _activity(f'实盘 {symbol}：已平仓，残留单清理未验证，锁定该币并继续重试：{exc}','error')
        _persist()
        return False

def _retry_flat_cleanup():
    with LOCK: pending={s:dict(p) for s,p in STATE.get('flat_cleanup_pending',{}).items()}
    for symbol,p in pending.items():
        try:
            cs=config.trading.get_ccxt_symbol(symbol)
            if any(x.get('symbol')==cs and float(x.get('contracts') or 0)>0 for x in alpha_live.positions()):continue
            if _flat_cleanup_or_block(symbol,p):
                with LOCK:
                    block=STATE.setdefault('protection_blocks',{}).get(symbol) or {}
                    if not block.get('manual_clear_required'):STATE['protection_blocks'].pop(symbol,None)
                _activity(f'实盘 {symbol}：残留算法单已复查清零，解除自动锁定')
                _persist()
        except Exception as exc: _activity(f'实盘 {symbol}：残留单重试等待交易所查询：{exc}','warning')

def _retry_flat_fills():
    with LOCK: pending={s:dict(p) for s,p in STATE.get('flat_fill_pending',{}).items()}
    for symbol,p in pending.items():
        fill=_exchange_close_fill(symbol,p)
        px=float(fill.get('price') or 0); qty=float(fill.get('qty') or 0)
        if px<=0 or qty<=0: continue
        try: attr=trade_attribution(symbol,float(p.get('entry') or px),px,p['side'],qty,
                                    exit_fee=float(fill.get('fee') or 0),reason='EXCHANGE_POSITION_GONE')
        except Exception as exc: attr={'net_pnl':0.,'error':str(exc)}
        with LOCK:
            if symbol not in STATE.get('flat_fill_pending',{}):continue
            STATE['flat_fill_pending'].pop(symbol,None)
            STATE.setdefault('attribution',[]).append({**attr,'symbol':symbol,'side':p['side'],'time':time.time()})
            STATE.setdefault('live_trades',[]).append({'time':time.time(),'symbol':symbol,'side':p['side'],
                'action':'CLOSE','reason':'EXCHANGE_POSITION_GONE','filled':qty,'average':px,
                'flat_confirmed':True,'attribution':attr,'exchange_trade_id':fill.get('trade_id')})
            STATE['history']=STATE['live_trades'][-300:]
        _activity(f'实盘 {symbol}：已补齐平仓成交对账')
        _persist()


def _apply_exit_switch(kind: str, enabled: bool):
    with LOCK:
        positions={s:dict(p) for s,p in STATE.get("positions",{}).items() if p.get("live")}
    for symbol,p in positions.items():
        if kind=="trend_trail":
            if enabled: _arm_native_trail(symbol,p,DEFAULT)
            else: _disarm_native_trail(symbol,p)
        elif kind=="partial_tp":
            if enabled: _arm_native_partial(symbol,p,DEFAULT)
            else: _disarm_native_partial(symbol,p)


def set_gate_switches(values: Dict[str, Any]) -> Dict[str, bool]:
    with LOCK:
        current=dict(DEFAULT_GATE_SWITCHES)
        current.update({k: bool(v) for k,v in (STATE.get("gate_switches") or {}).items() if k in DEFAULT_GATE_SWITCHES})
        target=dict(current)
        for k,v in (values or {}).items():
            if k in DEFAULT_GATE_SWITCHES: target[k]=bool(v)
    changes=[(k,current.get(k),target.get(k)) for k in ("trend_trail","partial_tp") if current.get(k)!=target.get(k)]
    applied=[]
    try:
        for kind,_,enabled in changes:
            _apply_exit_switch(kind,bool(enabled))
            applied.append((kind,enabled))
    except Exception as exc:
        for kind,enabled in reversed(applied):
            try: _apply_exit_switch(kind,not bool(enabled))
            except Exception as rollback_exc:
                ops_open_breaker(f"NATIVE_EXIT_SWITCH_ROLLBACK_FAILED:{kind}")
                logger.error(f"[ALPHA-X] 原生退出开关回滚失败: {rollback_exc}")
        raise RuntimeError(f"交易所原生退出开关切换失败，已回滚: {exc}") from exc
    with LOCK:
        STATE["gate_switches"]=target
    _persist()
    return target

def _df(candles) -> pd.DataFrame:
    if candles is None:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    if candles and isinstance(candles[0], dict):
        d = pd.DataFrame(candles)
        required=["ts","open","high","low","close","volume"]
        if any(c not in d.columns for c in required):
            return pd.DataFrame(columns=required)
    else:
        d = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close", "volume"])
    for c in ["ts", "open", "high", "low", "close", "volume"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    for c in d.columns:
        if c not in {"ts","open","high","low","close","volume"}:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna().drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    return d


def _drop_incomplete(d: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if d.empty:
        return d
    mins = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240}.get(timeframe, 15)
    now_ms = int(time.time() * 1000)
    last_ts = int(d.ts.iloc[-1])
    if now_ms - last_ts < mins * 60_000:
        return d.iloc[:-1].copy()
    return d


def _rsi(s: pd.Series, n: int = 14) -> pd.Series:
    x = s.diff(); up = x.clip(lower=0); dn = -x.clip(upper=0)
    rs = up.ewm(alpha=1 / n, adjust=False).mean() / (dn.ewm(alpha=1 / n, adjust=False).mean() + 1e-12)
    return 100 - 100 / (1 + rs)


def _completed_h1(d: pd.DataFrame) -> pd.DataFrame:
    x = d.set_index(pd.to_datetime(d.ts, unit="ms", utc=True))
    h1 = x.resample("1h", label="left", closed="left").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna()
    # 当前未完成小时不进入任何 15m 特征。
    hour_start = pd.Timestamp.now(tz="UTC").floor("h")
    h1 = h1[h1.index < hour_start]
    return h1


def features(df: pd.DataFrame) -> Tuple[pd.DataFrame, list]:
    d = df.copy(); c, h, l, o, v = d.close, d.high, d.low, d.open, d.volume
    for n in (5, 10, 20, 40, 80, 160):
        ema = c.ewm(span=n, adjust=False).mean()
        d[f"ret{n}"] = c.pct_change(n)
        d[f"dist_ema{n}"] = c / (ema + 1e-12) - 1
        d[f"vol{n}"] = c.pct_change().rolling(n).std()
    tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    # ATR is stored as a price-normalized percentage. Keep the units consistent
    # everywhere so trailing distance never divides by price a second time.
    d["atr5"] = tr.rolling(5).mean() / (c + 1e-12)
    d["atr14"] = tr.rolling(14).mean() / (c + 1e-12)
    d["atr50"] = tr.rolling(50).mean() / (c + 1e-12)
    d["rsi7"] = _rsi(c, 7) / 100; d["rsi14"] = _rsi(c, 14) / 100
    d["range_pct"] = (h-l) / (c + 1e-12); d["body_pct"] = (c-o) / (o + 1e-12)
    d["upper_wick"] = (h-np.maximum(o,c)) / (c + 1e-12)
    d["lower_wick"] = (np.minimum(o,c)-l) / (c + 1e-12)
    d["vol_ratio"] = v / (v.rolling(20).mean() + 1e-12)
    obv = (np.sign(c.diff()).fillna(0) * v).cumsum(); d["obv_slope"] = obv.pct_change(20)

    h1 = _completed_h1(d)
    if not h1.empty:
        h1["ema50"] = h1.close.ewm(span=50, adjust=False).mean()
        h1["ema200"] = h1.close.ewm(span=200, adjust=False).mean()
        h1["rsi"] = _rsi(h1.close, 14) / 100
        h1["ret8"] = h1.close.pct_change(8)
        idx = pd.to_datetime(d.ts, unit="ms", utc=True)
        # 只 forward-fill 已完成小时数据。
        for col in ("ema50", "ema200", "rsi", "ret8"):
            d[f"h1_{col}"] = h1[col].reindex(idx, method="ffill").to_numpy()
    else:
        for col in ("ema50", "ema200", "rsi", "ret8"):
            d[f"h1_{col}"] = np.nan
    d["h1_trend"] = d.h1_ema50 / (d.h1_ema200 + 1e-12) - 1
    d["trend_score"] = d.close / (d.close.ewm(span=80, adjust=False).mean() + 1e-12) - 1 + d.h1_trend
    d["vol_regime"] = d.vol20 / (d.vol80 + 1e-12)
    d["trend_strength"] = (d.dist_ema40.abs() + d.h1_trend.abs()) / (d.atr14 + 1e-12)
    # Optional independent information sources. Never fabricate missing history.
    external=("funding_rate","open_interest","oi_change_pct","liquidation_buy_usd","liquidation_sell_usd","liquidation_net_usd",
              "ofi","aggressive_buy_ratio","trade_imbalance","bid_depth_usd","ask_depth_usd","spread_bps","depth_imbalance",
              "btc_ret_1","eth_ret_1","btc_eth_spread","venue_basis_bps")
    for c in external:
        if c in d.columns:
            d[c]=pd.to_numeric(d[c],errors="coerce")
    d = d.replace([np.inf, -np.inf], np.nan).reset_index(drop=True)
    # Raw source metadata/prices are not model features; retain derived microstructure
    # and derivative signals while preventing timestamp/provenance leakage.
    excluded={"ts","open","high","low","close","volume","funding_ts","bid_px","ask_px"}
    cols = [z for z in d.columns if z not in excluded]
    return d, cols


def labels(df: pd.DataFrame, tp: float, sl: float, horizon: int, end_limit: Optional[int] = None) -> np.ndarray:
    """严格按各方向自己的 TP/SL 第一触发定义：0 SHORT, 1 FLAT, 2 LONG。"""
    n = len(df); y = np.ones(n, dtype=np.int8)
    hi, lo, close = df.high.to_numpy(), df.low.to_numpy(), df.close.to_numpy()
    for i in range(n):
        hard_end = n if end_limit is None else min(n, int(end_limit))
        end = min(hard_end, i + horizon + 1)
        if i + 1 >= end:
            continue
        ep = close[i]
        long_tp, long_sl = ep*(1+tp), ep*(1-sl)
        short_tp, short_sl = ep*(1-tp), ep*(1+sl)
        result = 1
        for j in range(i+1, end):
            long_tp_hit = hi[j] >= long_tp; long_sl_hit = lo[j] <= long_sl
            short_tp_hit = lo[j] <= short_tp; short_sl_hit = hi[j] >= short_sl
            # 同一根 K 线方向内部同时触发：不知道真实路径，保守记 FLAT。
            long_amb = long_tp_hit and long_sl_hit
            short_amb = short_tp_hit and short_sl_hit
            if long_amb or short_amb:
                result = 1; break
            # 同一根 K 线同时触及多空获利边界，真实路径未知，保守记 FLAT。
            if long_tp_hit and short_tp_hit:
                result = 1; break
            if long_tp_hit:
                result = 2; break
            if short_tp_hit:
                result = 0; break
        y[i] = result
    return y




def dynamic_tp_sl(base_tp: float, base_sl: float, market: Optional[Dict[str, Any]] = None, side: str = "long") -> Dict[str, Any]:
    """根据实时市场状态动态调整 TP/SL。

    原则：
    - TREND：给盈利目标更多空间；止损只小幅放宽，随后由风险预算按更大的 SL 自动缩小仓位。
    - STRESS/高波动：TP/SL 都可放宽，但绝不提高单笔美元风险；仓位由新 SL 重新反推。
    - RANGE：缩短目标并适度收紧止损，避免在震荡里追远目标。
    - EXTREME/NO-TRADE：不产生新交易目标。
    - live 持仓后，SL 只能收紧不能放宽，避免动态调整把最初风险越调越大。
    """
    m=market or {}
    tp=max(float(base_tp),0.006); sl=max(float(base_sl),0.004)
    regime=str(m.get("regime") or "NORMAL").upper()
    stress=max(0.0,min(1.0,float(m.get("stress") or 0.0)))
    trend=abs(float(m.get("trend_score") or 0.0))
    vol=max(float(m.get("vol_regime") or 1.0),0.0)
    atr=max(float(m.get("atr14") or 0.0),0.0)
    spread=max(float(m.get("spread_pct") or 0.0),0.0)
    trend_strength=min(1.0, trend/0.02)
    vol_strength=min(1.0,max(0.0,(vol-1.0)/1.5))
    if atr>=0.025: vol_strength=max(vol_strength,0.65)

    tp_mult=1.0; sl_mult=1.0; reason=[regime]
    if regime=="TREND":
        tp_mult=1.12+0.25*trend_strength
        sl_mult=1.03+0.08*trend_strength
        reason.append("趋势增强，放大利润空间")
    elif regime=="STRESS":
        tp_mult=1.08+0.12*vol_strength
        sl_mult=1.18+0.20*vol_strength
        reason.append("波动升高，扩大价格缓冲并降低仓位")
    elif regime=="RANGE":
        tp_mult=0.90; sl_mult=0.92
        reason.append("震荡市场，缩短目标并收紧风险")
    else:
        if vol_strength>0.25:
            tp_mult=1.05+0.10*vol_strength; sl_mult=1.05+0.12*vol_strength
            reason.append("波动高于常态")

    if stress>0.50:
        tp_mult*=1.0+0.05*stress
        sl_mult*=1.0+0.08*stress
    if spread>0.0015:
        tp_mult*=0.90; sl_mult*=0.90
        reason.append("点差偏宽，收紧交易目标")

    # funding crowding: when funding is extreme against the intended direction,
    # do not block by itself; reduce the profit target and tighten risk instead.
    funding=m.get("funding_rate")
    try:
        fr=float(funding) if funding is not None else 0.0
        against=(side=="long" and fr>0.0015) or (side=="short" and fr<-0.0015)
        if against:
            tp_mult*=0.92; sl_mult*=0.90; reason.append("资金费率拥挤，降低目标")
    except Exception:
        pass

    out_tp=max(0.006,min(0.08,tp*tp_mult))
    out_sl=max(0.004,min(0.05,sl*sl_mult))
    return {"tp":out_tp,"sl":out_sl,"tp_mult":out_tp/tp,"sl_mult":out_sl/sl,
            "regime":regime,"reason":"；".join(reason)}


def _models():
    # 保留每个交易周期最多3币并行；多个周期同时训练时，按当前全局活跃训练数动态分摊CPU。
    # 只调整并行线程数，不改变模型算法、树数量、深度、随机种子或训练数据。
    try:
        cpus = max(1, int(os.cpu_count() or 1))
        with ACTIVE_TRAIN_LOCK:
            active = max(1, int(ACTIVE_TRAINING_TASKS))
        model_jobs = max(1, cpus // active)
    except Exception:
        model_jobs = -1
    return [
        RandomForestClassifier(n_estimators=320, max_depth=14, min_samples_leaf=5, class_weight="balanced_subsample", random_state=42, n_jobs=model_jobs),
        ExtraTreesClassifier(n_estimators=320, max_depth=16, min_samples_leaf=4, class_weight="balanced", random_state=7, n_jobs=model_jobs),
        Pipeline([("scale", StandardScaler()), ("lr", LogisticRegression(max_iter=1800, class_weight="balanced", C=.7))]),
        HistGradientBoostingClassifier(max_iter=220, max_leaf_nodes=17, l2_regularization=1.5, random_state=11),
    ]


def _proba(models, X) -> np.ndarray:
    out=[]
    for m in models:
        p=m.predict_proba(X); z=np.zeros((len(X),3))
        for k,c in enumerate(m.classes_): z[:, int(c)] = p[:, k]
        out.append(z)
    return np.mean(out, axis=0)


def _trade_metrics(trades, start_balance=10000.0) -> Dict[str, Any]:
    if not trades:
        return {"total_return_pct": 0.0, "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
                "expectancy_usdt": 0.0, "max_drawdown_pct": 0.0, "sharpe": 0.0, "sortino": 0.0,
                "calmar": 0.0, "avg_win": 0.0, "avg_loss": 0.0, "profit_loss_ratio": 0.0, "trades": []}
    pnls=np.array([float(t["pnl"]) for t in trades], dtype=float); wins=pnls[pnls>0]; losses=pnls[pnls<0]
    curve=np.r_[start_balance, start_balance+np.cumsum(pnls)]; peak=np.maximum.accumulate(curve)
    dd=np.max((peak-curve)/np.maximum(peak,1e-12))*100
    rets=pnls/max(start_balance,1e-12)
    sharpe=float(np.mean(rets)/(np.std(rets)+1e-12)*math.sqrt(len(rets))) if len(rets)>1 else 0.0
    downside=np.std(rets[rets<0]) if np.any(rets<0) else 0.0
    sortino=float(np.mean(rets)/(downside+1e-12)*math.sqrt(len(rets))) if len(rets)>1 else 0.0
    pf=float(wins.sum() / max(-losses.sum(),1e-12)) if len(losses) else (99.0 if len(wins) else 0.0)
    ret_pct=float((curve[-1]/start_balance-1)*100)
    calmar=ret_pct/max(dd,1e-12)
    return {"total_return_pct":ret_pct,"total_trades":len(trades),"win_rate":float(len(wins)/len(trades)),
            "profit_factor":pf,"expectancy_usdt":float(np.mean(pnls)),"max_drawdown_pct":float(dd),
            "sharpe":sharpe,"sortino":sortino,"calmar":float(calmar),
            "avg_win":float(wins.mean()) if len(wins) else 0.0,"avg_loss":float(losses.mean()) if len(losses) else 0.0,
            "profit_loss_ratio":float(wins.mean()/max(-losses.mean(),1e-12)) if len(wins) and len(losses) else 0.0,
            "trades":trades}


def backtest(d, probs, tp, sl, entry, fee, slip, horizon, start_balance=10000.0, notional_pct=.10):
    cash=float(start_balance); peak=cash; pos=None; trades=[]
    for i in range(len(d)):
        px=float(d.close.iloc[i])
        if pos:
            hi=float(d.high.iloc[i]); lo=float(d.low.iloc[i]); exit_px=None; reason=None
            if pos["side"]=="long":
                if lo<=pos["sl"]: exit_px=pos["sl"]; reason="SL"
                elif hi>=pos["tp"]: exit_px=pos["tp"]; reason="TP"
            else:
                if hi>=pos["sl"]: exit_px=pos["sl"]; reason="SL"
                elif lo<=pos["tp"]: exit_px=pos["tp"]; reason="TP"
            if exit_px is None and i-pos["i"]>=horizon: exit_px=px; reason="TIME"
            # Funding is charged at explicit funding events only. Positive funding
            # costs longs and credits shorts; negative funding does the reverse.
            if pos and bool(d.get("funding_event", pd.Series([0]*len(d))).iloc[i] if "funding_event" in d.columns else False):
                fr=float(d.get("funding_rate_event", pd.Series([0.0]*len(d))).iloc[i] if "funding_rate_event" in d.columns else 0.0)
                side_sign=1.0 if pos["side"]=="long" else -1.0
                fpnl=-side_sign*pos["notional"]*fr
                cash+=fpnl
                pos.setdefault("funding_pnl",0.0); pos["funding_pnl"]+=fpnl
            if exit_px is not None:
                gross=(exit_px/pos["entry"]-1)*(1 if pos["side"]=="long" else -1)
                net=gross-fee*2-slip*2; pnl=pos["notional"]*net + float(pos.get("funding_pnl",0.0)); cash+=pos["notional"]*net
                trades.append({"side":pos["side"],"pnl":pnl,"ret":pnl/max(pos["notional"],1e-12),"reason":reason,"funding_pnl":float(pos.get("funding_pnl",0.0))}); pos=None
        if pos is None and i < len(d)-1:
            p=probs[i]; side="long" if p[2]>=entry and p[2]>p[0] else ("short" if p[0]>=entry and p[0]>p[2] else None)
            if side:
                mkt={"regime":"NORMAL","stress":0.0,"trend_score":float(d.get("trend_score",pd.Series([0.0]*len(d))).iloc[i] or 0),
                     "vol_regime":float(d.get("vol_regime",pd.Series([1.0]*len(d))).iloc[i] or 1),
                     "atr14":float(d.get("atr14",pd.Series([0.0]*len(d))).iloc[i] or 0),"spread_pct":0.0}
                if mkt["atr14"]>=0.05 or mkt["vol_regime"]>=2.8: mkt["regime"]="EXTREME"
                elif mkt["atr14"]>=0.025 or mkt["vol_regime"]>=1.8: mkt["regime"]="STRESS"; mkt["stress"]=0.65
                elif abs(mkt["trend_score"])>=0.02: mkt["regime"]="TREND"
                elif abs(mkt["trend_score"])<=0.004 and mkt["vol_regime"]<=1.2: mkt["regime"]="RANGE"
                dyn=dynamic_tp_sl(tp,sl,mkt,side=side)
                ep=px*(1+slip if side=="long" else 1-slip)
                # Re-size by the dynamically adjusted stop distance while keeping the backtest risk budget fixed.
                dyn_notional=cash*notional_pct*sl/max(dyn["sl"],0.002)
                pos={"side":side,"entry":ep,"tp":ep*(1+dyn["tp"] if side=="long" else 1-dyn["tp"]),"sl":ep*(1-dyn["sl"] if side=="long" else 1+dyn["sl"]),"i":i,"notional":min(cash*notional_pct,dyn_notional),"dynamic":dyn}
        peak=max(peak,cash)
    if pos:
        gross=(float(d.close.iloc[-1])/pos["entry"]-1)*(1 if pos["side"]=="long" else -1)
        pnl=pos["notional"]*(gross-fee*2-slip*2)+float(pos.get("funding_pnl",0.0)); cash+=pos["notional"]*(gross-fee*2-slip*2)
        trades.append({"side":pos["side"],"pnl":pnl,"ret":pnl/max(pos["notional"],1e-12),"reason":"END","funding_pnl":float(pos.get("funding_pnl",0.0))})
    return _trade_metrics(trades, start_balance)


def _grade(m: Dict[str, Any]) -> str:
    if m["total_trades"] < 10 or m["profit_factor"] < 1.0 or m["expectancy_usdt"] <= 0: return "WATCH"
    if m["profit_factor"] >= 1.35 and m["sharpe"] >= .9 and m["max_drawdown_pct"] <= 15: return "S"
    if m["profit_factor"] >= 1.15 and m["sharpe"] >= .45 and m["max_drawdown_pct"] <= 25: return "A"
    if m["profit_factor"] >= 1.02 and m["expectancy_usdt"] > 0: return "B"
    return "WATCH"


def _candidate_score(m: Dict[str, Any]) -> float:
    if m["total_trades"] < 8: return -1e9
    # 以风险调整后收益为主，避免单纯追求胜率。
    return (m["expectancy_usdt"] * math.log1p(m["total_trades"]) * max(0.25, min(2.0, m["profit_factor"]))
            + 0.8*m["sharpe"] + 0.25*m["sortino"] - 0.20*m["max_drawdown_pct"])


def _folds(n: int, dev_end: int, purge: int, k: int):
    # expanding train + validation windows；validation 从不回看 test。
    v_start = max(int(dev_end*0.38), 500)
    edges=np.linspace(v_start, dev_end, k+1, dtype=int)
    out=[]
    for i in range(k):
        val_end=int(edges[i+1]); val_start=int(edges[i]); train_end=val_start-purge
        if train_end>500 and val_end-val_start>=180:
            out.append((0,train_end,val_start,val_end))
    return out


def _fit_for_candidate(fd, y, cols, train_idx):
    models=_models()
    X=fd[cols].iloc[train_idx]; yy=y[train_idx]
    for m in models: m.fit(X,yy)
    return models


def _normalize_strategy_mode(mode: str) -> str:
    m=str(mode or "SHORT").upper().strip()
    return m if m in {"SHORT","MID","LONG","MULTI","FAST"} else "SHORT"

def _strategy_profile(mode: str) -> dict:
    m=_normalize_strategy_mode(mode)
    if m=="FAST":
        return {"mode":"FAST","label":"快速实盘","timeframe":"15m","lookback_default":220,
                "tp_grid":[0.018],"sl_grid":[0.012],"horizon_grid":[16],"max_hold_factor":900,"min_hold_bars":4}
    if m=="MID":
        return {"mode":"MID","label":"中线","timeframe":"1h","lookback_default":10000,
                "tp_grid":[0.03,0.045,0.065,0.08],"sl_grid":[0.02,0.03,0.04,0.05],
                "horizon_grid":[12,24,48],"max_hold_factor":3600,"min_hold_bars":12}
    if m=="LONG":
        return {"mode":"LONG","label":"长线","timeframe":"1h","lookback_default":20000,
                "tp_grid":[0.09,0.12,0.15,0.20],"sl_grid":[0.05,0.06,0.08,0.10],
                "horizon_grid":[72,120,168],"max_hold_factor":3600,"min_hold_bars":72}
    if m=="MULTI":
        return {"mode":"MULTI","label":"多周期协同","timeframe":"1h","lookback_default":10000,
                "tp_grid":[0.03,0.045,0.065,0.08],"sl_grid":[0.02,0.03,0.04,0.05],
                "horizon_grid":[12,24,48],"max_hold_factor":3600,"min_hold_bars":12}
    return {"mode":"SHORT","label":"短线","timeframe":"15m","lookback_default":10000,
            "tp_grid":DEFAULT["tp_grid"],"sl_grid":DEFAULT["sl_grid"],
            "horizon_grid":DEFAULT["horizon_grid"],"max_hold_factor":900,"min_hold_bars":12}

def _model_path(symbol, strategy_mode: str = "SHORT"):
    base=symbol.replace('/','_').replace(':','_').replace('-','_')
    mode=_normalize_strategy_mode(strategy_mode)
    if mode=="SHORT":
        return STORE/(base+'.joblib')
    return STORE/(f"{base}_{mode.lower()}.joblib")



def _fit_walkforward_meta(fd: pd.DataFrame, y: np.ndarray, cols: list, folds, valid: np.ndarray, dev_end: int,
                         min_rows: int = 300) -> Tuple[Any, Dict[str, Any]]:
    """Train the second-stage Alpha Core strictly from walk-forward OOF data.

    For fold k, the meta model sees only OOF observations from folds < k.
    The final meta model is then fit on all OOF observations from the development
    period. This prevents a fold from grading a meta model that has already seen it.
    """
    oof=[]; ys=[]; fold_cache=[]
    for tr0,tr1,va0,va1 in folds:
        tr=np.where(valid & (np.arange(len(fd))>=tr0) & (np.arange(len(fd))<tr1))[0]
        va=np.where(valid & (np.arange(len(fd))>=va0) & (np.arange(len(fd))<va1) & (np.arange(len(fd))<dev_end))[0]
        if len(tr)<600 or len(va)<180 or len(np.unique(y[tr]))<3:
            continue
        base=_fit_for_candidate(fd,y,cols,tr)
        bp=_proba(base,fd[cols].iloc[va])
        mx=np.vstack([meta_input(fd.iloc[i].to_dict(), bp[j]) for j,i in enumerate(va)])
        oof.append(mx); ys.append(y[va]); fold_cache.append((va,bp,mx,y[va]))
    if not oof:
        return None,{"enabled":False,"reason":"no_oof"}
    Xall=np.vstack(oof); yall=np.concatenate(ys)
    # Proper fold-by-fold validation of the meta layer.
    meta_losses=[]; base_losses=[]; meta_acc=[]; base_acc=[]
    for k,(va,bp,mx,yv) in enumerate(fold_cache):
        prior_X=np.vstack(oof[:k]) if k else np.empty((0,Xall.shape[1]))
        prior_y=np.concatenate(ys[:k]) if k else np.empty((0,),dtype=int)
        if len(prior_X)<min_rows or len(np.unique(prior_y))<3:
            continue
        mm=fit_meta(prior_X,prior_y)
        if mm is None: continue
        mp=meta_proba(mm,mx)
        base_losses.append(float(log_loss(yv,bp,labels=[0,1,2])))
        meta_losses.append(float(log_loss(yv,mp,labels=[0,1,2])))
        base_acc.append(float(accuracy_score(yv,np.argmax(bp,axis=1))))
        meta_acc.append(float(accuracy_score(yv,np.argmax(mp,axis=1))))
    # Select meta architecture by nested temporal OOF, not by final test.
    meta_folds=[]
    starts=[]; cur=0
    for arr in ys:
        starts.append(cur); cur += len(arr)
    for k in range(1,len(starts)):
        tr=np.arange(0,starts[k],dtype=int); va=np.arange(starts[k],starts[k]+len(ys[k]),dtype=int)
        if len(tr)>=min_rows: meta_folds.append((tr,va))
    mm,meta_select=select_meta_oof(Xall,yall,meta_folds,min_train=min_rows)
    if mm is None:
        return None,{"enabled":False,"reason":"meta_fit_failed","oof_rows":int(len(Xall))}
    bl=float(np.mean(base_losses)) if base_losses else float("inf")
    ml=float(np.mean(meta_losses)) if meta_losses else float("inf")
    ba=float(np.mean(base_acc)) if base_acc else 0.0
    ma=float(np.mean(meta_acc)) if meta_acc else 0.0
    # Activation is decided only from nested walk-forward validation. The final
    # test window is not touched by this decision. Require both lower log-loss
    # and non-worse directional accuracy.
    improvement=(bl-ml)/max(abs(bl),1e-9) if np.isfinite(bl) and np.isfinite(ml) else 0.0
    enabled=bool(len(meta_losses)>=1 and improvement>=0.01 and ma>=ba)
    return mm,{"enabled":enabled,"oof_rows":int(len(Xall)),"meta_validation_folds":len(meta_losses),
               "base_logloss":bl,"meta_logloss":ml,"base_accuracy":ba,"meta_accuracy":ma,
               "relative_logloss_improvement":improvement,"selection_uses_test":False,
               "method":"nested_walk_forward_OOF_meta_model_selection","meta_selection":meta_select}


def _test_meta_candidate(fd, test_idx, models, meta_model, strength, base_probs):
    if meta_model is None: return base_probs
    mx=np.vstack([meta_input(fd.iloc[i].to_dict(), base_probs[j]) for j,i in enumerate(test_idx)])
    mp=meta_proba(meta_model,mx)
    return blend_proba(base_probs,mp,strength)

def _load_v16_training_candles(symbol: str, timeframe: str, db_path: str | Path):
    """Load the already-collected V16 dataset as the training source.

    In real-data(auto) mode we must not silently replace the user's stopped V16
    collection with a fresh OKX lookback request.  Use every completed bar that
    is actually present in the V16 SQLite store.  V16 currently persists 15m
    bars, so 1h training is built by deterministic 4x15m OHLCV aggregation.
    Optional funding/OI/trades/L2 are then attached from the same store using
    point-in-time joins; missing optional observations remain missing.
    """
    db = Path(db_path)
    if not db.exists():
        raise ValueError(f"V16真实数据仓库不存在：{db}")
    inst_id = str(symbol).upper() if "-SWAP" in str(symbol).upper() else str(symbol).upper().replace("/", "-") + "-SWAP"
    ds = RealDataStore(db)
    try:
        bars = ds.fetch("bars", inst_id)
        if bars.empty:
            raise ValueError(f"V16没有找到{inst_id}已采集K线，请先启动并补齐历史数据")
        bars = bars.copy()
        bars["ts"] = pd.to_numeric(bars["ts"], errors="coerce")
        for c in ("open", "high", "low", "close", "volume"):
            bars[c] = pd.to_numeric(bars[c], errors="coerce")
        bars = bars.dropna(subset=["ts","open","high","low","close"]).drop_duplicates("ts").sort_values("ts")
        # V16历史K线来自OKX history-candles；仍按当前时间再次排除正在形成的15m柱。
        cutoff = (int(time.time()*1000) // 900_000) * 900_000
        bars = bars[bars.ts.astype("int64") < cutoff].copy()
        if bars.empty:
            raise ValueError(f"V16没有已完成K线：{inst_id}")
        bars["ts"] = bars.ts.astype("int64")

        tf = str(timeframe).lower()
        if tf == "15m":
            candles = bars[["ts","open","high","low","close","volume"]].copy()
        elif tf == "1h":
            z = bars.set_index(pd.to_datetime(bars.ts, unit="ms", utc=True))
            agg = z.resample("1h", label="left", closed="left").agg(
                {"open":"first","high":"max","low":"min","close":"last","volume":"sum"}
            ).dropna(subset=["open","high","low","close"])
            candles = agg.reset_index().rename(columns={"index":"dt"})
            candles["ts"] = (candles["dt"].astype("int64") // 10**6).astype("int64")
            candles = candles[["ts","open","high","low","close","volume"]]
        else:
            raise ValueError(f"V16真实数据训练暂不支持周期：{timeframe}")

        candles = candles.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
        enriched, audit = augment_candles(candles, ds, inst_id, timeframe, strict=False)
        gate = source_gate(audit, 0.80)
        audit.update({"training_source":"V16本地真实数据仓库","all_collected_bars_used":True,
                      "stored_bar_rows":int(len(bars)),"training_bar_rows":int(len(candles)),
                      "source_gate":gate,"database":str(db)})
        return enriched, audit
    finally:
        ds.close()


def train(symbol, candles=None, settings=None):
    cfg={**DEFAULT,**(settings or {})}
    strategy_mode=_normalize_strategy_mode(cfg.get("strategy_mode", "SHORT"))
    profile=_strategy_profile(strategy_mode)
    if strategy_mode != "SHORT":
        cfg["timeframe"]=profile["timeframe"]
        cfg["tp_grid"]=profile["tp_grid"]
        cfg["sl_grid"]=profile["sl_grid"]
        cfg["horizon_grid"]=profile["horizon_grid"]
        cfg["lookback"]=max(int(cfg.get("lookback",profile["lookback_default"])), int(profile["lookback_default"]))
    symbol_lock_key = f"{symbol}|{strategy_mode}"
    with TRAIN_LOCKS_LOCK:
        symbol_lock = TRAIN_SYMBOL_LOCKS.setdefault(symbol_lock_key, threading.Lock())
    if not symbol_lock.acquire(blocking=False): raise RuntimeError(f"ALPHA-X {symbol} 的{profile['label']}模型已有训练任务运行，请等待完成")
    global ACTIVE_TRAINING_TASKS
    with ACTIVE_TRAIN_LOCK:
        ACTIVE_TRAINING_TASKS += 1
    try:
        logger.warning(f"[ALPHA-X] 开始训练 {symbol} (模式={profile['label']}, timeframe={cfg['timeframe']}, lookback={cfg['lookback']})")
        _activity(f"{symbol}：开始训练，准备真实数据")
        real_mode=str(cfg.get("real_data_mode","off")).lower()
        # auto=训练页面的“全部真实数据”：直接使用已经停止的V16数据仓库，
        # 不再重新请求一份lookback数量的K线覆盖掉V16历史采集结果。
        if candles is not None:
            d=_drop_incomplete(_df(candles),cfg["timeframe"])
        elif real_mode=="auto":
            d, v16_audit = _load_v16_training_candles(symbol, cfg["timeframe"], cfg.get("real_data_db", ROOT/"alpha_market_data.sqlite3"))
            real_audit = {"enabled":True,"mode":"auto","v16_direct":True,"v16":v16_audit}
        else:
            raw=okx_client.get_ohlcv(symbol=symbol,timeframe=cfg["timeframe"],limit=int(cfg["lookback"]))
            d=_drop_incomplete(_df(raw),cfg["timeframe"])
        logger.warning(f"[ALPHA-X] {symbol} 训练K线={len(d)} 根")
        _activity(f"{symbol}：真实训练K线准备完成，共 {len(d)} 根，开始特征与参数搜索")
        if 'real_audit' not in locals():
            real_audit={"enabled":False,"mode":real_mode,"reason":"disabled"}
        if real_mode in {"auto","strict","smart"} and not bool(real_audit.get("v16_direct")):
            db=Path(str(cfg.get("real_data_db",ROOT/"alpha_market_data.sqlite3")))
            if db.exists():
                ds=RealDataStore(db)
                try:
                    inst_id=symbol.upper() if "-SWAP" in symbol.upper() else symbol.upper().replace("/","-")+"-SWAP"
                    # smart模式：自动读v17研究结果选择数据源
                    if str(cfg.get("real_data_mode")).lower()=="smart":
                        from alpha_real_alpha_research import select_sources_from_research
                        sel = select_sources_from_research(inst_id)
                        if not sel.get("research_available", True):
                            raise ValueError(f"{symbol} 智能训练需要先完成对应的 V17 研究，当前没有可用研究结果")
                        use_cross = bool(sel.get("cross_market", False))
                        selected_sources={k:bool(sel.get(k,False)) for k in ("funding","open_interest","order_flow","l2")}
                        d,real_audit=augment_candles_selected(d,ds,inst_id,cfg["timeframe"],strict=False,
                                                               sources=selected_sources,
                                                               cross_market=use_cross)
                        real_audit["smart_selection"] = sel
                        real_audit["enabled"]=True
                    else:
                        d,real_audit=augment_candles(d,ds,inst_id,cfg["timeframe"],strict=False)
                        if bool(cfg.get("real_data_cross_market",False)):
                            d=cross_market_from_store(d,ds)
                            real_audit["cross_market_enabled"]=True
                            real_audit["cross_market_rows"]=int(d[[c for c in ("btc_ret_1","eth_ret_1") if c in d.columns]].notna().all(axis=1).sum()) if any(c in d.columns for c in ("btc_ret_1","eth_ret_1")) else 0
                        real_audit["enabled"]=True
                    gate=source_gate(real_audit,float(cfg.get("real_data_min_coverage",0.80)))
                    real_audit["gate"]=gate
                    mode_l=str(cfg.get("real_data_mode")).lower()
                    if mode_l=="strict" and not gate["ready"]:
                        raise ValueError("真实数据覆盖不足: "+",".join(gate["missing_or_weak"]))
                    if mode_l=="auto" and not gate["ready"]:
                        # auto模式：覆盖率不足不报错，有多少用多少，不够的自动用K线补上
                        weak_list=",".join(gate["missing_or_weak"])
                        logger.warning(f"[ALPHA-X] {symbol} v16数据覆盖率不足({weak_list})，auto模式自动降级：有多少用多少，其余用K线")
                        _activity(f"{symbol}：v16数据覆盖率不足({weak_list})，auto模式自动降级，有多少用多少")
                        real_audit["auto_fallback"]=True
                    if mode_l=="smart":
                        sel=real_audit.get("smart_selection",{})
                        selected=[k for k in ("funding","open_interest","order_flow","l2","cross_market") if bool(sel.get(k,False))]
                        ratios=gate.get("ratios",{})
                        weak=[k for k in selected if k in ratios and ratios[k] < float(cfg.get("real_data_min_coverage",0.80))]
                        if weak:
                            raise ValueError("V17 智能选择的数据源覆盖不足: "+",".join(weak))
                finally: ds.close()
            elif str(cfg.get("real_data_mode")).lower()=="strict":
                raise ValueError(f"REAL_DATA_STRICT 找不到数据仓库: {db}")
            else:
                real_audit={"enabled":False,"mode":"auto","reason":"database_missing","db":str(db)}
        if len(d)<2400:
            logger.warning(f"[ALPHA-X] {symbol} 数据不足: {len(d)}根 < 2400根，跳过")
            raise ValueError(f"{symbol} 数据不足：至少需要2400根已完成K线（实际{len(d)}根）")
        fd,cols=features(d); n=len(fd); valid=np.isfinite(fd[cols]).all(axis=1).to_numpy()
        dev_end=int(n*cfg["dev_ratio"]); folds=_folds(n,dev_end,int(cfg["purge_bars"]),int(cfg["walk_forward_folds"]))
        if len(folds)<2: raise ValueError("有效 walk-forward 窗口不足，请增加历史数据")
        test_start=dev_end+int(cfg["purge_bars"])
        test_idx=np.where(valid & (np.arange(n)>=test_start))[0]
        if len(test_idx)<300: raise ValueError("最终测试区间不足")
        best=None
        total_combos = len(cfg["tp_grid"])*len(cfg["sl_grid"])*len(cfg["horizon_grid"])
        # 智能模型竞技：先用一个开发期窗口做低成本海选，再把算力集中给前40%候选。
        # 这是训练加速层，不改变最终WFO/独立OOS测试标准；最终测试仍完全不参与选参。
        candidates=[]
        for tp in cfg["tp_grid"]:
            for sl in cfg["sl_grid"]:
                if sl<=0 or tp<=0: continue
                for horizon in cfg["horizon_grid"]:
                    candidates.append((float(tp),float(sl),int(horizon)))
        quick_rank=[]
        label_cache={}
        val_label_cache={}
        quick_fold=folds[0] if folds else None
        if quick_fold is not None:
            tr0,tr1,va0,va1=quick_fold
            for idx,(tp,sl,horizon) in enumerate(candidates,1):
                try:
                    key=(float(tp),float(sl),int(horizon))
                    yq=label_cache.setdefault(key, labels(d,tp,sl,horizon))
                    tr=np.where(valid & (np.arange(n)>=tr0) & (np.arange(n)<tr1))[0]
                    va=np.where(valid & (np.arange(n)>=va0) & (np.arange(n)<va1) & (np.arange(n)<dev_end))[0]
                    vkey=(float(tp),float(sl),int(horizon),int(va1))
                    y_val=val_label_cache.setdefault(vkey, labels(d,tp,sl,horizon,end_limit=va1))
                    if len(tr)<600 or len(va)<180 or len(np.unique(yq[tr]))<3 or len(np.unique(y_val[va]))<2:
                        continue
                    models=_fit_for_candidate(fd,yq,cols,tr)
                    pv=_proba(models,fd[cols].iloc[va]); dv=d.iloc[va].reset_index(drop=True)
                    best_e=None
                    for entry in np.arange(cfg["entry_min"],cfg["entry_max"]+.001,.02):
                        vm=backtest(dv,pv,tp,sl,float(entry),cfg["fee_pct"],cfg["slippage_pct"],horizon)
                        if vm["total_trades"]<cfg["min_val_trades"]: continue
                        sc=_candidate_score(vm)
                        if best_e is None or sc>best_e[0]: best_e=(sc,float(entry),vm)
                    if best_e: quick_rank.append({"params":(tp,sl,horizon),"score":float(best_e[0]),"entry":float(best_e[1]),"trades":int(best_e[2].get("total_trades",0))})
                except Exception as exc:
                    logger.debug(f"[ALPHA-X] {symbol} 海选候选失败 TP={tp} SL={sl} H={horizon}: {exc}")
        if not quick_rank:
            raise ValueError(f"{symbol} 智能海选没有形成可用候选")
        quick_rank.sort(key=lambda x:x["score"], reverse=True)
        top_score=quick_rank[0]["score"]
        keep_n=max(6,int(math.ceil(len(candidates)*0.40)))
        # 安全保留线：接近第一名的候选也保留，避免单窗口偶然性误淘汰。
        keep=[x for x in quick_rank[:keep_n] if x["score"] >= top_score*0.90]
        seen={x["params"] for x in keep}
        for x in quick_rank:
            if len(keep)>=keep_n: break
            if x["params"] not in seen:
                keep.append(x); seen.add(x["params"])
        keep_params=[x["params"] for x in keep]
        logger.warning(f"[ALPHA-X 竞技训练] {symbol} 第一轮海选完成：候选{len(candidates)}个 → 保留{len(keep_params)}个，淘汰{max(0,len(candidates)-len(keep_params))}个")
        _activity(f"{symbol}：智能模型竞技第一轮完成，{len(candidates)}个候选中保留{len(keep_params)}个进入深度WFO；每个候选由4种模型联合评估")
        combo_idx=0
        for tp,sl,horizon in keep_params:
            combo_idx += 1
            logger.warning(f"[ALPHA-X 竞技训练] {symbol} 深度WFO {combo_idx}/{len(keep_params)}：TP={tp*100:.1f}% SL={sl*100:.1f}% 周期={horizon}")
            y=label_cache.setdefault((float(tp),float(sl),int(horizon)), labels(d,tp,sl,horizon))
            fold_scores=[]; fold_metrics=[]; chosen_entries=[]
            for tr0,tr1,va0,va1 in folds:
                tr=np.where(valid & (np.arange(n)>=tr0) & (np.arange(n)<tr1))[0]
                va=np.where(valid & (np.arange(n)>=va0) & (np.arange(n)<va1) & (np.arange(n)<dev_end))[0]
                vkey=(float(tp),float(sl),int(horizon),int(va1))
                y_val=val_label_cache.setdefault(vkey, labels(d,tp,sl,horizon,end_limit=va1))
                if len(tr)<600 or len(va)<180 or len(np.unique(y[tr]))<3 or len(np.unique(y_val[va]))<2: continue
                models=_fit_for_candidate(fd,y,cols,tr); pv=_proba(models,fd[cols].iloc[va]); dv=d.iloc[va].reset_index(drop=True)
                best_e=None
                for entry in np.arange(cfg["entry_min"],cfg["entry_max"]+.001,.02):
                    vm=backtest(dv,pv,tp,sl,float(entry),cfg["fee_pct"],cfg["slippage_pct"],horizon)
                    if vm["total_trades"]<cfg["min_val_trades"]: continue
                    sc=_candidate_score(vm)
                    if best_e is None or sc>best_e[0]: best_e=(sc,float(entry),vm)
                if best_e:
                    fold_scores.append(best_e[0]); fold_metrics.append(best_e[2]); chosen_entries.append(best_e[1])
            if len(fold_scores)<2: continue
            avg_score=float(np.mean(fold_scores)); stability=float(np.std(fold_scores)); entry=float(np.median(chosen_entries))
            objective=avg_score-0.35*stability
            if best is None or objective>best["objective"]:
                best={"objective":objective,"tp":tp,"sl":sl,"horizon":horizon,"entry":entry,"fold_scores":fold_scores,"fold_metrics":fold_metrics}
                logger.warning(f"[ALPHA-X 竞技训练] {symbol} 当前领先：TP={tp*100:.1f}% SL={sl*100:.1f}% 周期={horizon} 入场={entry:.2f} 得分={objective:.4f}")
        if not best: raise ValueError(f"{symbol} 没有形成可靠的 walk-forward 候选")
        logger.warning(f"[ALPHA-X] {symbol} 参数搜索完成，最优: TP={best['tp']*100:.1f}% SL={best['sl']*100:.1f}% 周期={best['horizon']} 入场={best['entry']:.2f}")

        # Anti-overfit audit: the chosen parameter set must not be a razor-thin peak.
        # Build a compact neighborhood around the selected score from the WFO folds.
        stability_rows=[]
        for fs in best.get("fold_scores",[]): stability_rows.append({"net_expectancy_bps":float(fs)})
        stability=parameter_stability(stability_rows)
        avg_expectancy=float(np.mean([m.get("expectancy_usdt",0.0) for m in best.get("fold_metrics",[])]) if best.get("fold_metrics") else 0.0)
        cost=CostModel(fee_bps=float(cfg.get("fee_pct",0.0005))*10000,slippage_bps=float(cfg.get("slippage_pct",0.0005))*10000,
                       funding_bps_per_8h=float(cfg.get("funding_bps_per_8h",0.0)),impact_bps=float(cfg.get("impact_bps",0.0)))
        cost_stress=stressed_edge(max(0.0,avg_expectancy/max(float(cfg.get("paper_start_balance",10000.0)),1.0)*10000),
                                  float(best.get("horizon",12))*0.25,cost,extra_stress=float(cfg.get("cost_stress_multiplier",1.5)))

        # 选参已经结束；最终测试完全不参与选择。最终模型只在开发区间训练。
        logger.warning(f"[ALPHA-X] {symbol} 开始最终模型训练和测试...")
        final_train=np.where(valid & (np.arange(n)<dev_end-int(cfg["purge_bars"]))) [0]
        y=label_cache.setdefault((float(best["tp"]),float(best["sl"]),int(best["horizon"])), labels(d,best["tp"],best["sl"],best["horizon"]))
        if len(np.unique(y[final_train]))<3: raise ValueError("最终训练集类别不足")
        models=_fit_for_candidate(fd,y,cols,final_train)
        base_test_probs=_proba(models,fd[cols].iloc[test_idx])
        # Train the new Alpha Core only from development-period walk-forward OOF data.
        meta_model, alpha_audit=_fit_walkforward_meta(fd,y,cols,folds,valid,dev_end)
        alpha_strength=float(cfg.get("alpha_meta_strength",0.65))
        test_probs=_test_meta_candidate(fd,test_idx,models,meta_model,alpha_strength,base_test_probs) if alpha_audit.get("enabled") else base_test_probs
        test_d=d.iloc[test_idx].reset_index(drop=True)
        test_metrics=backtest(test_d,test_probs,best["tp"],best["sl"],best["entry"],cfg["fee_pct"],cfg["slippage_pct"],best["horizon"])
        base_test_metrics=backtest(test_d,base_test_probs,best["tp"],best["sl"],best["entry"],cfg["fee_pct"],cfg["slippage_pct"],best["horizon"])
        val_avg={k:float(np.mean([m[k] for m in best["fold_metrics"]])) for k in ("total_return_pct","total_trades","win_rate","profit_factor","expectancy_usdt","max_drawdown_pct","sharpe","sortino","calmar")}
        test_trade_count=int(test_metrics.get("total_trades",0) or 0)
        live_ready=bool(test_trade_count >= int(cfg.get("min_test_trades",DEFAULT.get("min_test_trades",8))))
        live_ready_reason=("最终测试交易数达到实盘最低要求" if live_ready else f"最终测试交易数不足：{test_trade_count} < {int(cfg.get('min_test_trades',DEFAULT.get('min_test_trades',8)))}")
        meta={"version":VERSION,"symbol":symbol,"strategy_mode":strategy_mode,"strategy_label":profile["label"],"timeframe":cfg["timeframe"],"trained_at":time.time(),"features":cols,
              "tp":best["tp"],"sl":best["sl"],"horizon":best["horizon"],"entry":best["entry"],
              "val_metrics":val_avg,"metrics":test_metrics,"base_test_metrics":base_test_metrics,"grade":_grade(test_metrics),"data_bars":n,
              "live_ready":live_ready,"live_ready_reason":live_ready_reason,"test_trade_count":test_trade_count,
              "split":{"development_end":dev_end,"test_start":test_start,"test_bars":len(test_idx),"purge_bars":int(cfg["purge_bars"])},
              "walk_forward":{"folds":len(best["fold_metrics"]),"scores":best["fold_scores"],"selection_uses_test":False},
              "alpha_core":{"name":"MULTI_ALPHA_META_RESEARCH2","enabled":bool(alpha_audit.get("enabled")),"strength":alpha_strength,"audit":alpha_audit},
              "research2":{"source_audit":source_audit(d.to_dict("records")),"real_data":real_audit,"parameter_stability":stability,"cost_stress":cost_stress,
                            "test_selection_uses_test":False,"requires_independent_sources_for_full_mode":True},
              "config_signature":{"fee_pct":cfg["fee_pct"],"slippage_pct":cfg["slippage_pct"],"purge_bars":cfg["purge_bars"]},
              "research_audit":{"final_test_touched_during_selection":False,"validation_label_cutoff":True,"incomplete_candles_removed":True,"meta_trained_only_on_oof_development":True},
              "strategy":{"mode":strategy_mode,"label":profile["label"],"primary_timeframe":cfg["timeframe"],"trend_timeframes":["4h","1d"] if strategy_mode=="LONG" else ["4h"],
                          "max_hold_seconds":int(best["horizon"]*profile["max_hold_factor"]),"training_target":"长周期TP/SL第一触发" if strategy_mode in ("MID","LONG","MULTI") else "短周期TP/SL第一触发"}}
        import joblib
        path=_model_path(symbol,strategy_mode); tmp=path.with_suffix(".tmp")
        joblib.dump({"models":models,"meta_model":meta_model,"meta":meta},tmp); tmp.replace(path)
        try: research_register(symbol, meta, role="candidate")
        except Exception as exc: logger.warning(f"[ALPHA-X] registry write failed: {exc}")
        logger.warning(f"[ALPHA-X] {symbol} 训练完成，收益率={meta.get('metrics',{}).get('total_return_pct',0):.2f}%，胜率={meta.get('metrics',{}).get('win_rate',0)*100:.1f}%，最终测试交易={test_trade_count}，实盘就绪={'是' if live_ready else '否'}")
        if live_ready:
            _activity(f"{symbol}：模型训练完成，最终测试达到实盘最低要求，可进入模拟盘/实盘")
        else:
            _activity(f"{symbol}：模型已保存用于研究/模拟，但暂不可实盘：{live_ready_reason}", "warning")
        return meta
    finally:
        with ACTIVE_TRAIN_LOCK:
            ACTIVE_TRAINING_TASKS = max(0, ACTIVE_TRAINING_TASKS - 1)
        symbol_lock.release()


def load(symbol, strategy_mode: Optional[str] = None):
    """安全加载指定周期模型。任何损坏/旧格式/结构异常都只返回 None，不向启动接口泄漏 NoneType.get。"""
    import joblib
    mode=_normalize_strategy_mode(strategy_mode or STATE.get("strategy_mode","SHORT"))
    p=_model_path(symbol,mode)
    if not p.exists() and mode=="SHORT":
        p=_model_path(symbol,"SHORT")
    if not p.exists():
        return None
    try:
        obj=joblib.load(p)
        if not isinstance(obj, dict):
            logger.warning(f"[ALPHA-X] 模型结构无效 {symbol}/{mode}：顶层对象不是字典")
            return None
        meta=obj.get("meta")
        if not isinstance(meta, dict):
            logger.warning(f"[ALPHA-X] 模型结构无效 {symbol}/{mode}：缺少模型元数据")
            return None
        trained_at=float(meta.get("trained_at",0) or 0)
        if trained_at<=0:
            return None
        age=(time.time()-trained_at)/86400
        if _normalize_strategy_mode(meta.get("strategy_mode","SHORT")) != mode:
            logger.warning(f"[ALPHA-X] 模型周期不匹配 {symbol}：文件={meta.get('strategy_mode')}，启动={mode}")
            return None
        if meta.get("version")!=VERSION or age>DEFAULT["model_max_age_days"]:
            return None
        features=meta.get("features")
        models=obj.get("models")
        if not isinstance(features,list) or not features or not isinstance(models,list) or len(models)<3:
            return None
        alpha_core=meta.get("alpha_core") or {}
        if not isinstance(alpha_core,dict):
            return None
        if alpha_core.get("enabled") and obj.get("meta_model") is None:
            return None
        return obj
    except Exception as exc:
        logger.warning(f"[ALPHA-X] 模型读取失败 {symbol}/{mode}：{type(exc).__name__}: {exc}")
        return None


def _advanced_context(symbol: str, fd_row: pd.Series) -> Dict[str, Any]:
    """实时微观结构/衍生品/市场状态过滤；外部数据缺失时只降级，不制造信号。"""
    try:
        ex=getattr(okx_client,"_exchange",None)
        cs=config.trading.get_ccxt_symbol(symbol)
        ctx=build_context(cs,{"trend_score":fd_row.get("trend_score",0),"vol_regime":fd_row.get("vol_regime",1),"atr14":fd_row.get("atr14",0)},ex)
        return ctx.as_dict()
    except Exception as exc:
        return {"spread_pct":None,"orderbook_imbalance":None,"funding_rate":None,"open_interest":None,"oi_change_pct":None,"regime":"UNKNOWN","stress":0.5,"no_trade":True,"reasons":["CONTEXT_ERROR"]}

def _prepare_realtime_model_data(symbol, d, meta):
    """让使用真实数据训练的模型在实时预测时获得同样的数据源；缺失则安全停机。"""
    rd=((meta.get("research2") or {}).get("real_data") or {})
    mode=str(rd.get("mode","off")).lower()
    if not rd.get("enabled") or mode=="off":
        return d, {"enabled":False,"mode":"off"}
    db=Path(str(DEFAULT.get("real_data_db",ROOT/"alpha_market_data.sqlite3")))
    if not db.exists():
        raise ValueError("模型需要真实数据，但本机真实数据仓库不存在")
    ds=RealDataStore(db)
    try:
        inst_id=symbol.upper() if "-SWAP" in symbol.upper() else symbol.upper().replace("/","-")+"-SWAP"
        if mode=="smart":
            sel=rd.get("smart_selection") or {}
            if not sel.get("research_available",True):
                raise ValueError("模型依赖 V17 智能选择，但没有有效研究结果")
            sources={k:bool(sel.get(k,False)) for k in ("funding","open_interest","order_flow","l2")}
            d,audit=augment_candles_selected(d,ds,inst_id,meta.get("timeframe",DEFAULT["timeframe"]),strict=False,sources=sources,cross_market=bool(sel.get("cross_market",False)))
        else:
            d,audit=augment_candles(d,ds,inst_id,meta.get("timeframe",DEFAULT["timeframe"]),strict=False)
            if bool(rd.get("cross_market_enabled",False)):
                d=cross_market_from_store(d,ds)
                audit["cross_market_enabled"]=True
        # A real-data model must not silently trade with weak/missing live features.
        gate=source_gate(audit,float(DEFAULT.get("real_data_min_coverage",0.80)))
        selected=[]
        if mode=="smart":
            sel=rd.get("smart_selection") or {}
            selected=[k for k in ("funding","open_interest","order_flow","l2","cross_market") if bool(sel.get(k,False))]
        if selected:
            ratios=gate.get("ratios",{})
            weak=[k for k in selected if k in ratios and ratios[k] < float(DEFAULT.get("real_data_min_coverage",0.80))]
            if weak: raise ValueError("实时真实数据覆盖不足: "+",".join(weak))
        elif mode in {"auto","strict"} and not gate.get("ready",False):
            raise ValueError("实时真实数据覆盖不足: "+",".join(gate.get("missing_or_weak",[])))
        return d,{"enabled":True,"mode":mode,"audit":audit,"gate":gate}
    finally:
        ds.close()

def _unified_adaptive_size_multiplier(regime=None, mtf=None, lead_lag=None,
                                     confidence=0.0, directional_margin=0.0, signal_tier="NORMAL", committee=None,
                                     regime_enabled=True, mtf_enabled=True, lead_lag_enabled=True):
    """将多个环境判断合并成一个双向仓位系数。

    正面环境可以适度加仓，负面环境降仓；多个模块不会相乘。
    最终范围固定为 0.55~1.20：1.00=中性，>1=环境支持加仓，<1=环境需要降仓。
    该系数只影响仓位，不改变交易方向，也不绕过硬风控。
    """
    items=[]

    def _regime_bias(x):
        if not x: return 0.0
        name=str(x.get("regime", "UNKNOWN"))
        if name=="EXTREME_VOL": return -0.95
        if name=="HIGH_VOL": return -0.55
        if name=="RANGE": return -0.30
        if name=="TRANSITION": return -0.10
        if name in ("TREND_UP","TREND_DOWN"):
            strength=min(1.0, abs(float(x.get("score",0) or 0)))
            eff=min(1.0, float(x.get("trend_efficiency",0) or 0)/0.45)
            stress=float(x.get("stress",0) or 0)
            return max(0.0, min(1.0, 0.35*strength+0.45*eff+0.20*(1.0-stress)))
        return 0.0

    def _mtf_bias(x):
        if not x or x.get("label","") in ("多周期数据不足",): return 0.0
        agreement=float(x.get("agreement",0) or 0)
        score=abs(float(x.get("score",0) or 0))
        direction=str(x.get("direction","NEUTRAL"))
        if direction=="NEUTRAL": return -0.05 if agreement<0.5 else 0.0
        if agreement>=0.75 and score>=0.18: return 0.85
        if agreement>=0.50 and score>=0.18: return 0.35
        return -0.35

    def _lead_bias(x):
        if not x: return 0.0
        label=str(x.get("label", ""))
        score=abs(float(x.get("score",0) or 0))
        rel=float(x.get("reliability",0) or 0)
        if "正在跟随" in label and score>=0.28 and rel>=0.64: return 0.75
        if "暂未跟随" in label and score>=0.28 and rel>=0.64: return -0.20
        return 0.0

    if regime_enabled: items.append((0.40, _regime_bias(regime)))
    if mtf_enabled: items.append((0.35, _mtf_bias(mtf)))
    if lead_lag_enabled: items.append((0.25, _lead_bias(lead_lag)))
    total=sum(w for w,_ in items)
    env_bias=(sum(w*b for w,b in items)/max(total,1e-9)) if items else 0.0
    conf=max(0.0,min(1.0,float(confidence or 0.0)))
    margin=max(0.0,min(1.0,float(directional_margin or 0.0)))
    conf_score=max(-1.0,min(1.0,(conf-0.60)/0.16))
    margin_score=max(-1.0,min(1.0,(margin-0.06)/0.10))
    tier_score=0.20 if str(signal_tier).upper()=="NORMAL" else (-0.10 if str(signal_tier).upper()=="TRIAL" else 0.0)
    com=committee or {}
    com_ag=max(0.0,min(1.0,float(com.get("agreement",0) or 0)))
    com_score=max(-1.0,min(1.0,abs(float(com.get("score",0) or 0))/0.45))
    committee_bias=(com_ag-0.5)*1.2*max(0.0,com_score)
    opp_bias=max(-1.0,min(1.0,0.50*conf_score+0.25*margin_score+0.10*tier_score+0.15*committee_bias))
    combined=0.60*env_bias+0.40*opp_bias
    unified=1.0 + (0.20*combined if combined>=0 else 0.45*combined)
    return float(max(0.55,min(1.20,unified)))


def predict(symbol,candles=None,strategy_mode: Optional[str] = None):
    strategy_mode=_normalize_strategy_mode(strategy_mode or STATE.get("strategy_mode","SHORT"))
    if strategy_mode=="FAST":
        _pred = fast_predict(symbol)
        try:
            _note_fast_eval(_pred, symbol)
        except Exception:
            pass
        return _pred
    obj=load(symbol,strategy_mode)
    if not obj: return {"signal":"FLAT","reason":f"没有有效{_strategy_profile(strategy_mode)['label']}模型，请先训练或模型已过期","model_ready":False,"strategy_mode":strategy_mode}
    meta=obj["meta"]; tf=meta.get("timeframe",DEFAULT["timeframe"]); strategy_mode=_normalize_strategy_mode(meta.get("strategy_mode",strategy_mode)); profile=_strategy_profile(strategy_mode)
    raw=candles or okx_client.get_ohlcv(symbol=symbol,timeframe=tf,limit=max(1200,int(meta.get("data_bars",3000)//2)))
    d=_drop_incomplete(_df(raw),tf)
    try:
        d,_realtime_audit=_prepare_realtime_model_data(symbol,d,meta)
    except Exception as exc:
        return {"signal":"FLAT","reason":"REAL_DATA_UNAVAILABLE:"+str(exc),"model_ready":False,"version":VERSION}
    fd,_=features(d)
    if fd.empty or len(fd)<200: return {"signal":"FLAT","reason":"实时特征不足","model_ready":False}
    try: x=fd[meta["features"]].iloc[[-1]]
    except KeyError: return {"signal":"FLAT","reason":"模型特征版本不兼容，请重新训练","model_ready":False}
    if not np.isfinite(x.to_numpy()).all(): return {"signal":"FLAT","reason":"实时特征存在缺失，进入 NO-TRADE","model_ready":False}
    base_p=_proba(obj["models"],x)
    p=base_p[0]
    meta_model=obj.get("meta_model")
    alpha_cfg=meta.get("alpha_core",{})
    meta_p=None
    if alpha_cfg.get("enabled") and meta_model is not None:
        mx=meta_input(fd.iloc[-1].to_dict(),p).reshape(1,-1)
        meta_p=meta_proba(meta_model,mx)[0]
        p=blend_proba(base_p,meta_p,float(alpha_cfg.get("strength",0.65)))[0]
    ctx=_advanced_context(symbol,fd.iloc[-1])
    ctx.update({"trend_score":float(fd.iloc[-1].get("trend_score",0) or 0),
                "vol_regime":float(fd.iloc[-1].get("vol_regime",1) or 1),
                "atr5":float(fd.iloc[-1].get("atr5",0) or 0),
                "atr14":float(fd.iloc[-1].get("atr14",0) or 0),
                "atr50":float(fd.iloc[-1].get("atr50",0) or 0),
                "trend_strength":float(fd.iloc[-1].get("trend_strength",0) or 0)})

    # 新增但可关闭：Regime + 多时间周期共振。它只提供市场环境与仓位调节信息，
    # 不在这里新增硬拦截，避免把原有信号链变成“多一层门禁”。
    adaptive_ctx={"enabled":False,"regime":{},"multi_timeframe":{},"lead_lag":{},"size_multiplier":1.0}
    if gate_enabled("regime_mtf"):

        try:
            regime=classify_regime(d)
            mtf=analyze_multi_timeframe(symbol, okx_client, base_timeframe=tf)
            adaptive_ctx={"enabled":True,"regime":regime,"multi_timeframe":mtf,
                          "size_multiplier":_unified_adaptive_size_multiplier(
                              regime, mtf, None, regime_enabled=True, mtf_enabled=True, lead_lag_enabled=False)}
            ctx.update({"adaptive_enabled":True, "regime":regime.get("regime","UNKNOWN"),
                        "regime_label":regime.get("label","未知市场状态"),
                        "regime_score":regime.get("score",0.0),
                        "regime_stress":regime.get("stress",0.0),
                        "mtf_direction":mtf.get("direction","NEUTRAL"),
                        "mtf_agreement":mtf.get("agreement",0.0),
                        "mtf_score":mtf.get("score",0.0),
                        "adaptive_size_multiplier":adaptive_ctx["size_multiplier"]})
            _activity(f"{symbol}：市场状态={regime.get('label','未知')}；多周期={mtf.get('label','未知')}；环境综合仓位系数={adaptive_ctx['size_multiplier']:.2f}")
        except Exception as exc:
            adaptive_ctx={"enabled":True,"error":str(exc),"regime":{},"multi_timeframe":{},"size_multiplier":1.0}
            ctx.update({"adaptive_enabled":True,"adaptive_error":str(exc)})
            _activity(f"{symbol}：Regime/多周期分析失败，降级使用原信号链：{exc}","warning")
    else:
        ctx.update({"adaptive_enabled":False,"regime":"DISABLED","regime_label":"自适应分析已关闭",
                    "mtf_direction":"DISABLED","mtf_agreement":0.0,"adaptive_size_multiplier":1.0})

    # Lead-Lag：直接读取OKX跨资产K线，作为交易质量增强信息。绝不新增硬拦截。
    lead_lag={"enabled":False,"direction":"NEUTRAL","label":"领先关系已关闭","score":0.0,"reliability":0.0,"size_multiplier":1.0,"relationships":[]}
    if gate_enabled("lead_lag"):
        try:
            lead_lag=analyze_lead_lag(symbol, okx_client, base_timeframe=tf)
            ctx.update({"lead_lag_enabled":True,"lead_lag_direction":lead_lag.get("direction","NEUTRAL"),
                        "lead_lag_score":lead_lag.get("score",0.0),"lead_lag_reliability":lead_lag.get("reliability",0.0),
                        "lead_lag_label":lead_lag.get("label","未知")})
            # Lead-Lag加入同一个“环境综合系数”，不再与Regime/多周期连续相乘。
            adaptive_ctx["lead_lag"]=lead_lag
            adaptive_ctx["size_multiplier"]=_unified_adaptive_size_multiplier(
                adaptive_ctx.get("regime"), adaptive_ctx.get("multi_timeframe"), lead_lag,
                regime_enabled=bool(adaptive_ctx.get("regime") or {}),
                mtf_enabled=bool(adaptive_ctx.get("multi_timeframe") or {}),
                lead_lag_enabled=True)
            adaptive_ctx["size_sources"]={
                "regime":float((adaptive_ctx.get("regime") or {}).get("size_multiplier",1.0) or 1.0),
                "multi_timeframe":float((adaptive_ctx.get("multi_timeframe") or {}).get("size_multiplier",1.0) or 1.0),
                "lead_lag":float(lead_lag.get("size_multiplier",1.0) or 1.0)}
            ctx["adaptive_size_multiplier"]=adaptive_ctx["size_multiplier"]
            ctx["adaptive_size_sources"]=adaptive_ctx["size_sources"]
            _activity(f"{symbol}：Lead-Lag={lead_lag.get('label','未知')}；可靠度={float(lead_lag.get('reliability',0))*100:.1f}%；环境综合仓位系数={adaptive_ctx['size_multiplier']:.2f}（不再连续相乘）")
        except Exception as exc:
            lead_lag={"enabled":True,"error":str(exc),"direction":"NEUTRAL","label":"获取失败，降级为原信号","score":0.0,"reliability":0.0,"size_multiplier":1.0,"relationships":[]}
            adaptive_ctx["lead_lag"]=lead_lag
            ctx.update({"lead_lag_enabled":True,"lead_lag_error":str(exc)})
            _activity(f"{symbol}：Lead-Lag分析失败，降级使用原信号链：{exc}","warning")
    else:
        ctx.update({"lead_lag_enabled":False,"lead_lag_direction":"DISABLED","lead_lag_score":0.0,"lead_lag_reliability":0.0,"lead_lag_label":"领先关系已关闭"})
        adaptive_ctx["lead_lag"]=lead_lag

    committee=strategy_committee(fd.iloc[-1].to_dict(),ctx,p, float(meta.get("val_metrics",{}).get("sharpe",0) or 0))
    # Opportunity-preserving signal tiers: strong signals use the model entry threshold;
    # moderate signals may enter with reduced risk; weak signals remain flat.
    stress_now=float(ctx.get("stress",0) or 0)
    normal_entry=float(meta["entry"])
    trial_entry=max(0.52, min(normal_entry-0.08, normal_entry))
    model_conf=float(max(p[0],p[2]))
    margin=float(abs(p[2]-p[0]))
    candidate_entry=trial_entry if model_conf>=trial_entry else normal_entry
    model_sig="LONG" if p[2]>=candidate_entry and p[2]>p[0] else ("SHORT" if p[0]>=candidate_entry and p[0]>p[2] else "FLAT")
    committee_sig=str(committee.get("signal") or "FLAT")
    agreement=float(committee.get("agreement",0) or 0)
    committee_score=float(committee.get("committee_score",0) or 0)
    # Committee is confirmation, not a second entry gate: aligned -> normal/trial;
    # neutral/light disagreement -> trial; strong opposite evidence -> veto.
    opposite=(committee_sig in ("LONG","SHORT") and committee_sig != model_sig)
    sig=model_sig
    signal_tier="NORMAL" if model_sig!="FLAT" and model_conf>=normal_entry else ("TRIAL" if model_sig!="FLAT" else "NONE")
    reason="MODEL+COMMITTEE" if committee_sig==model_sig else ("MODEL_COMMITTEE_NEUTRAL" if committee_sig=="FLAT" else "MODEL_STRONGER_THAN_COMMITTEE")
    if opposite and agreement>=0.55 and abs(committee_score)>=0.30:
        sig="FLAT"; signal_tier="NONE"; reason="COMMITTEE_STRONG_OPPOSITE"
    # Trial requires only a modest directional edge; disagreement reduces size downstream.
    if signal_tier=="TRIAL" and margin<0.04:
        sig="FLAT"; signal_tier="NONE"; reason="TRIAL_DIRECTION_TOO_WEAK"
    if ctx.get("no_trade") and sig!="FLAT": sig="FLAT"; signal_tier="NONE"; reason="NO_TRADE:"+",".join(ctx.get("reasons",[]))
    confidence=risk_adjusted_confidence(model_conf, float(committee["committee_score"]), float(committee["agreement"]), stress_now)
    # 统一机会仓位：在信号方向已经确定后，把模型强度/委员会/环境一次性合并。
    # 只调仓，不改方向；硬风险仍由 pretrade/production gate 负责。
    adaptive_ctx["size_multiplier"]=_unified_adaptive_size_multiplier(
        adaptive_ctx.get("regime"), adaptive_ctx.get("multi_timeframe"), adaptive_ctx.get("lead_lag"),
        confidence=confidence, directional_margin=margin, signal_tier=signal_tier, committee=committee,
        regime_enabled=bool(adaptive_ctx.get("regime") or {}),
        mtf_enabled=bool(adaptive_ctx.get("multi_timeframe") or {}),
        lead_lag_enabled=bool(adaptive_ctx.get("lead_lag") or {}))
    adaptive_ctx["sizing_label"]=("强机会·适度加仓" if adaptive_ctx["size_multiplier"]>1.03 else ("风险/环境偏弱·降仓" if adaptive_ctx["size_multiplier"]<0.97 else "中性仓位"))
    ctx["adaptive_size_multiplier"]=adaptive_ctx["size_multiplier"]
    ctx["adaptive_sizing_label"]=adaptive_ctx["sizing_label"]
    _activity(f"{symbol}：统一机会仓位={adaptive_ctx['size_multiplier']:.2f}（{adaptive_ctx['sizing_label']}），只调仓不改方向")

    # Confidence is already the model confidence; do not re-apply a second
    # confidence gate here. Execution sizing applies its own hard safety floor.
    alpha_info=meta_explanation(fd.iloc[-1].to_dict(),base_p[0],meta_p)
    dyn=dynamic_tp_sl(float(meta["tp"]),float(meta["sl"]),ctx,side=("long" if sig=="LONG" else "short"))
    out={"signal":sig,"reason":reason,"short_prob":float(p[0]),"flat_prob":float(p[1]),"long_prob":float(p[2]),
            "base_short_prob":float(base_p[0,0]),"base_flat_prob":float(base_p[0,1]),"base_long_prob":float(base_p[0,2]),
            "confidence":confidence,"raw_confidence":model_conf,"entry_threshold":normal_entry,
            "signal_tier":signal_tier,"trial_entry_threshold":trial_entry,"directional_margin":margin,
            "base_tp":float(meta["tp"]),"base_sl":float(meta["sl"]),"tp":float(dyn["tp"]),"sl":float(dyn["sl"]),
            "dynamic_tp_sl":dyn,"horizon":meta["horizon"],"strategy_mode":strategy_mode,"strategy_label":profile["label"],
            "metrics":meta["metrics"],"val_metrics":meta["val_metrics"],"grade":meta["grade"],"model_ready":True,
            "version":VERSION,"market_context":ctx,"strategy_committee":committee,"alpha_core":alpha_info,
            "adaptive_context":adaptive_ctx,"timeframe":tf}
    try: ledger_record("SIGNAL",symbol,out)
    except Exception: pass
    return out


def live_check(symbols=None):
    if not okx_client.is_connected: raise RuntimeError("请先连接 OKX")
    if config.okx.is_demo: raise RuntimeError("当前 OKX_DEMO_MODE=1，不是生产环境")
    if not alpha_live.allowed: raise RuntimeError("ALPHA_LIVE_ALLOWED 未开启")
    if config.trading.trading_type != "swap": raise RuntimeError("ALPHA-X ULTRA 实盘仅允许 SWAP")
    syms=symbols or config.trading.get_active_symbols(); out=[]
    for s in syms:
        cs=config.trading.get_ccxt_symbol(s); m=alpha_live.market(cs)
        out.append({"symbol":s,"ccxt_symbol":cs,"contractSize":m.get("contractSize"),"posMode":alpha_live.pos_mode()})
    return {"ok":True,"mode":"production","balance":alpha_live.account(),"symbols":out}


def _on_ws_event(event: Dict[str, Any]):
    """Consume OKX private WS events as a low-latency execution journal. REST remains recovery truth."""
    try:
        channel=str((event.get("arg") or {}).get("channel") or "")
        for row in event.get("data") or []:
            tag=str(row.get("tag") or "")
            clid=str(row.get("clOrdId") or "")
            oid=str(row.get("ordId") or "")
            if channel not in ("orders","orders-algo","positions"): continue
            if channel.startswith("orders") and not (tag=="ALPHAX" or clid.startswith("AX")):
                continue
            state=str(row.get("state") or row.get("pos") or "")
            payload={k:row.get(k) for k in ("ordId","clOrdId","state","accFillSz","fillSz","fillPx","avgPx","tradeId","fee","fillPnl","attachAlgoClOrdId","algoId","posSide") if k in row}
            if oid:
                if state=="filled": ops_record(oid,"FILLED",payload)
                elif state in ("canceled","mmp_canceled"): ops_record(oid,"ERROR",payload)
                elif state in ("live","partially_filled"): ops_record(oid,"SUBMITTED",payload)
            ledger_record("WS_EXECUTION", row.get("instId") or "OKX", {"channel":channel, **payload})
            if state=="filled" and clid.startswith("AX"):
                with LOCK:
                    for sym, pos in STATE.get("positions",{}).items():
                        if pos.get("client_order_id")==clid:
                            pos["last_ws_fill"]={"fillPx":row.get("fillPx"),"fillSz":row.get("fillSz"),"tradeId":row.get("tradeId"),"at":time.time()}
                            try:
                                fp=float(row.get("fillPx")); pos.setdefault("price_path",[]).append(fp); pos["price_path"]=pos["price_path"][-500:]
                            except Exception: pass
                            if row.get("avgPx"): pos["entry"]=float(row.get("avgPx"))
                            break
        with LOCK: STATE["ws"]=ALPHA_WS.status() if ALPHA_WS else STATE.get("ws",{})
    except Exception as exc:
        logger.warning(f"[ALPHA-X] WS event handling failed: {exc}")


EQUITY_ERROR_PREFIX="无法读取实时权益"
EQUITY_STALE_OK=120.0          # 读不到时，最近一次成功读数在这个秒数内仍可继续使用
_ACCOUNT_CACHE={"ts":0.0,"total":0.0,"free":0.0,"fails":0}
_ACCOUNT_LOCK=threading.Lock()


def _live_account_snapshot(max_age: float = 2.0, retries: int = 3):
    """读取 OKX 账户权益/可用：失败自动重试；max_age 秒内的成功读数直接复用（多处同时读取时不重复请求）。
    max_age=0 表示必须重新读取（下单前用）。成功时清掉界面上旧的“无法读取实时权益”提示。"""
    with _ACCOUNT_LOCK:
        if max_age>0 and time.time()-_ACCOUNT_CACHE["ts"]<=max_age:
            return _ACCOUNT_CACHE["total"],_ACCOUNT_CACHE["free"]
    last=None
    for k in range(max(1,retries)):
        try:
            a=alpha_live.account(); total=float(a.get("total") or 0); free=float(a.get("free") or 0)
            if total<=0 and free<=0: raise RuntimeError("OKX 返回的权益为 0，按读取失败处理")
            with _ACCOUNT_LOCK: _ACCOUNT_CACHE.update(ts=time.time(),total=total,free=free,fails=0)
            with LOCK:
                if str(STATE.get("live_error") or "").startswith(EQUITY_ERROR_PREFIX): STATE["live_error"]=""
            return total,free
        except Exception as exc:
            last=exc
            if k<retries-1: time.sleep((0.4,1.0,2.0)[min(k,2)])
    with _ACCOUNT_LOCK: _ACCOUNT_CACHE["fails"]+=1
    raise RuntimeError(f"重试{max(1,retries)}次仍失败：{last}")


def _last_good_account(max_age: float = EQUITY_STALE_OK):
    """最近一次成功读数（秒数内有效），没有则返回 None。"""
    with _ACCOUNT_LOCK:
        if _ACCOUNT_CACHE["ts"] and time.time()-_ACCOUNT_CACHE["ts"]<=max_age:
            return _ACCOUNT_CACHE["total"],_ACCOUNT_CACHE["free"],time.time()-_ACCOUNT_CACHE["ts"]
    return None


def _risk_block(cfg):
    with LOCK:
        today=time.strftime("%Y-%m-%d")
        if STATE.get("day")!=today:
            STATE["day"]=today; STATE["day_start_equity"]=STATE.get("equity",STATE.get("balance",10000)); STATE["consecutive_losses"]=0
        start=float(STATE.get("day_start_equity") or 0); eq=float(STATE.get("equity") or 0)
        if start>0 and (start-eq)/start>=cfg["max_daily_loss_pct"]: return "达到单日最大亏损保护"
        if int(STATE.get("consecutive_losses",0))>=cfg["max_consecutive_losses"]: return "连续亏损保护"
    return ""


def _record_live_consecutive_result(attr: dict, reason: str = "") -> None:
    """Update live consecutive-loss protection exactly once after a confirmed close."""
    try:
        pnl=float((attr or {}).get("net_pnl"))
    except Exception:
        return
    if not math.isfinite(pnl):
        return
    with LOCK:
        if pnl < 0:
            STATE["consecutive_losses"] = int(STATE.get("consecutive_losses",0)) + 1
        elif pnl > 0:
            STATE["consecutive_losses"] = 0
        # A zero-PnL close neither increases nor resets the loss streak.
        STATE.setdefault("live_close_stats",[]).append({"time":time.time(),"pnl":pnl,"reason":reason,"consecutive_losses":STATE["consecutive_losses"]})
        STATE["live_close_stats"] = STATE["live_close_stats"][-500:]
    _persist()


def _v6_entry_guard(symbol, pred, price, now):
    """V6按触发K去重并复核追价；成功前不把请求误当成交。"""
    fs=pred.get("fast_strategy") or {}
    if fs.get("engine_version")!="v6": return True
    key=str(fs.get("signal_id") or "")
    if not key or (STATE.get("v6_attempted") or {}).get(symbol)==key:
        _activity(f"V6 {symbol}：本根触发已尝试下单，等待新K线机会")
        return False
    end=float(fs.get("bar_ts") or 0)+300000
    if not end<=now*1000<=end+315000:
        _activity(f"V6 {symbol}：触发已过期或尚未收盘，本轮不下单")
        return False
    d=1 if pred.get("signal")=="LONG" else -1
    ref=float(fs.get("reference_price") or 0); stop=float(fs.get("sl_price") or 0); target=float(fs.get("tp_price") or 0)
    risk=d*(ref-stop)
    if price<=0 or risk<=0 or abs(price-ref)>float(fs.get('max_chase_r',fast_v6.PARAMS['max_chase_r']))*risk:
        _activity(f"V6 {symbol}：下单前价格偏离触发位，停止追价")
        return False
    sl=d*(price-stop)/price; tp=d*(target-price)/price
    cost=float(fs.get("estimated_round_cost") or .0022)
    if sl<=0 or tp<float(fs.get('min_target_cost',fast_v6.PARAMS['min_target_cost']))*cost or (tp-cost)/(sl+cost)<float(fs.get('min_net_rr',fast_v6.PARAMS['min_net_rr'])):
        _activity(f"V6 {symbol}：报价变化后空间不足，放弃本次机会")
        return False
    pred.update(sl=sl,tp=tp,base_sl=sl,base_tp=tp,dynamic_tp_sl={"tp":tp,"sl":sl,"reason":"V6结构目标与失效位，禁止旧模式重算"})
    return True


def _v6_claim_signal(symbol,pred):
    fs=pred.get("fast_strategy") or {}
    if fs.get("engine_version")=="v6":
        with LOCK: STATE.setdefault("v6_attempted",{})[symbol]=fs.get("signal_id")
        _persist()  # 即使请求结果未知也不在同根K重复发单；撤单/失败等待下一根。

def _v7_entry_guard(symbol, pred, price, now):
    """V7按触发K去重并复核追价与空间；口径与V6一致，信号来自指标。"""
    fs=pred.get("fast_strategy") or {}
    if fs.get("engine_version")!="v7": return True
    if pred.get("signal") not in ("LONG","SHORT"): return True  # FLAT 无需去重/追价复核
    key=str(fs.get("signal_id") or "")
    if not key or (STATE.get("v7_attempted") or {}).get(symbol)==key:
        _activity(f"V7 {symbol}：本根触发已尝试下单，等待新K线机会")
        return False
    # 触发K的收盘时间按开仓主级别计算；硬编码5m会让15m/1h信号永远被判为“过期/未收盘”。
    bar_ms=fast_v7.tf_ms(fs.get("v7_base_tf") or "5m")
    end=float(fs.get("bar_ts") or 0)+bar_ms
    if not end<=now*1000<=end+bar_ms+15000:
        _activity(f"V7 {symbol}：触发已过期或尚未收盘，本轮不下单")
        return False
    d=1 if pred.get("signal")=="LONG" else -1
    if fs.get("v7_scheme3"):
        # 方案三：信号K收盘后、下一根收盘前按市价进场（上面已检查）；回测里晚一整根进场结果几乎不变。
        # 止盈/止损按下单前最新价重算，比例固定。
        if price<=0:
            _activity(f"V7 {symbol}：方案三报价无效，本轮不进场")
            return False
        import alpha_v7_scheme3 as S3
        tp_px,sl_px=S3.targets(price,d); tp=d*(tp_px-price)/price; sl=d*(price-sl_px)/price
        fs.update(tp_price=float(tp_px),sl_price=float(sl_px),reference_price=float(price))
        pred.update(sl=sl,tp=tp,base_sl=sl,base_tp=tp,dynamic_tp_sl={"tp":tp,"sl":sl,"reason":"V7 方案三：净1%止盈、25%止损"})
        return True
    ref=float(fs.get("reference_price") or 0); stop=float(fs.get("sl_price") or 0); target=float(fs.get("tp_price") or 0)
    risk=d*(ref-stop)
    if price<=0 or risk<=0 or abs(price-ref)>float(fs.get('max_chase_r',fast_v7.PARAMS['max_chase_r']))*risk:
        _activity(f"V7 {symbol}：下单前价格偏离触发位，停止追价")
        return False
    sl=d*(price-stop)/price; tp=d*(target-price)/price
    cost=float(fs.get("estimated_round_cost") or .0022)
    if sl<=0 or tp<float(fs.get('min_target_cost',fast_v7.PARAMS['min_target_cost']))*cost or (tp-cost)/(sl+cost)<float(fs.get('min_net_rr',fast_v7.PARAMS['min_net_rr'])):
        _activity(f"V7 {symbol}：报价变化后空间不足，放弃本次机会")
        return False
    pred.update(sl=sl,tp=tp,base_sl=sl,base_tp=tp,dynamic_tp_sl={"tp":tp,"sl":sl,"reason":"V7指标目标与失效位，禁止旧模式重算"})
    return True


def _v7_claim_signal(symbol,pred):
    fs=pred.get("fast_strategy") or {}
    if fs.get("engine_version")=="v7":
        with LOCK: STATE.setdefault("v7_attempted",{})[symbol]=fs.get("signal_id")
        _persist()  # 同根K不重复发单；撤单/失败等待下一根。


def _fast_position_meta(pred):
    """按信号引擎版本选择持仓元数据（V6/V7 各自契约）。"""
    ev=str(((pred.get("fast_strategy") or {}).get("engine_version") or ""))
    if ev=="v7": return fast_v7.position_meta(pred)
    return fast_v6.position_meta(pred)


def _position_max_seconds(pred):
    """持仓顶层到期秒数：V7 用 decide 给出的级别化 max_seconds（=max_hold_bars×主级别每根毫秒/1000），
    15m/1h 随级别自动变长；非 V7 维持原有 horizon×profile 因子口径。"""
    fs=pred.get("fast_strategy") or {}
    if fs.get("engine_version")=="v7" and fs.get("max_seconds"):
        return int(fs["max_seconds"])
    return int(pred["horizon"]*(_strategy_profile(pred.get("strategy_mode","SHORT"))["max_hold_factor"]))


def _live_manage_v6(symbol,p,pred,cfg):
    """调用者已确认真实仓位；止损改单/平仓都以交易所确认推进状态。"""
    pending=bool(p.get("v6_recovery_pending"))
    if pending:
        plan={"stop":None};reason="V6异常恢复保护未验证，退出并核对成交"
    else:
        tk=okx_client.get_ticker(symbol) or {}; px=float(tk.get("last") or 0)
        if px<=0: return
        frames={}
        if p.get("v6_engine")=="V6_BREAKOUT" or p.get("v62"):
            frames=(_fast_mode._fetch_symbol(symbol) or {}).get("frames") or {}
        plan=fast_v6.exit_plan(p,px,time.time(),frames,trailing=False)
        reason=plan.get("close")
        if p.get('v7_pine_error') and time.time()-float(p.get('v7_pine_error_logged_at') or 0)>60:
            _activity(f"V7 {symbol} Pine退出指令未执行：{p['v7_pine_error']}；保留交易所保护", "warning")
            p['v7_pine_error_logged_at']=time.time()
        if p.get('recovered') and (not p.get('tp_attach_clordid') or not p.get('sl_attach_clordid')):
            reason='V6异常恢复缺少保护标识，退出并核对成交'
    if reason:
        r=_managed_close(symbol,p["side"])
        if r.get("status")=="no_position": return
        flat=alpha_live.wait_position_closed(symbol,p["side"],timeout=10)
        if not flat.get("closed"):
            with LOCK: STATE["live_error"]=f"{symbol} V6平仓尚未确认，继续管理"
            return
        fill_px=float(r.get("average") or 0); qty=float(r.get("filled") or 0)
        if fill_px<=0 or qty<=0:
            _activity(f"V6 {symbol}：仓位归零但成交明细未齐，等待原有成交对账", "warning")
            return
        attr=trade_attribution(symbol,float(p["entry"]),fill_px,p["side"],qty,exit_fee=float(r.get("fee") or 0),reason=reason)
        row={"time":time.time(),"symbol":symbol,"side":p["side"],"action":"CLOSE","reason":reason,
             "order_id":r.get("order_id"),"filled":qty,"average":fill_px,"flat_confirmed":True,"attribution":attr}
        with LOCK:
            STATE.setdefault("attribution",[]).append({**attr,"symbol":symbol,"side":p["side"],"reason":reason,"time":row["time"]})
            STATE["attribution"]=STATE["attribution"][-500:]
            STATE["live_trades"].append(row); STATE["history"]=STATE["live_trades"][-300:]
            STATE["positions"].pop(symbol,None)
        ledger_record("LIVE_CLOSE",symbol,row)
        if p.get("order_id"): ops_record(p["order_id"],"CLOSED",row)
        _record_live_consecutive_result(attr,reason); _record_strategy_health_close(attr,p,pred)
        _fast_detail(symbol,"V6平仓确认",f"{reason}；实际成交={fill_px:.8g}；净盈亏={float(attr.get('net_pnl') or 0):+.4f} USDT")
        _persist(); return
    # 移动止损与分批止盈均由OKX原生算法单托管，本地不再轮询阈值。
    candidate=plan.get("stop"); side=p["side"]; current=float(p.get("sl") or 0)
    better=candidate and ((side=="long" and candidate>current) or (side=="short" and candidate<current))
    if better and p.get("sl_attach_clordid") and time.time()-float(p.get("v6_last_amend") or 0)>=20:
        r=alpha_live.amend_sl_only(symbol,side,float(candidate),p["sl_attach_clordid"],wait_timeout=4.0)
        if r.get("verified"):
            with LOCK: p["sl"]=float(candidate); p["trail_sl"]=float(candidate); p["v6_last_amend"]=time.time()
            _fast_detail(symbol,"V6移动保护",f"止损确认更新至{candidate:.8g}；初始目标不延长")
            _persist()
        else: _activity(f"V6 {symbol}：止损更新未确认，保留已有保护", "warning")

# ---------------------------------------------------------------- V7 方案二：加仓 / 一卖减半 / 卖点清仓
def _scheme2_action(pred, p):
    """取本根 5 分钟K线的方案二持仓动作；同一根K线的动作只执行一次（执行前先记下，失败不重发）。"""
    s2=pred.get("scheme2") or {}
    act=s2.get("action"); T=int(s2.get("T") or 0)
    if not act or not T or int(p.get("s2_done_T") or 0)>=T: return None,T
    return dict(act),T


def _scheme2_leg_notional(equity,free,sl_pct,cfg):
    """方案二每一批的名义金额：权益 × 单笔风险 × 1/3 ÷ 止损距离；不超过最大名义比例和可用保证金×杠杆×0.8。"""
    from alpha_v7_scheme2 import LEG_RISK
    notional=float(equity)*float(cfg["risk_pct"])*LEG_RISK/max(float(sl_pct),0.002)
    return max(0.0,min(notional,float(equity)*float(cfg.get("max_notional_pct",1.0)),float(free)*max(float(cfg.get("leverage",1)),1)*0.8))


def _scheme2_protect(symbol,p,stop):
    """把保护换成交易所整仓止损单（closeFraction=1，加仓/减仓后不用改数量）；已是整仓单则只在止损价变化时改价。"""
    side=p["side"]
    if p.get("s2_pos_algo"):
        if abs(float(stop)-float(p.get("sl") or 0))>abs(float(stop))*1e-9:
            r=alpha_live.amend_sl_only(symbol,side,float(stop),p["s2_pos_algo"],wait_timeout=4.0)
            if not r.get("verified"): raise RuntimeError("整仓止损改价未确认："+str(r.get("error") or ""))
            with LOCK: p["sl"]=float(stop); p["s2_stop"]=float(stop)
        return
    r=alpha_live.place_position_protection(symbol,side,float(stop),float(p.get("tp") or 0))
    old=[p.get("tp_attach_clordid"),p.get("sl_attach_clordid")]
    with LOCK:
        p["s2_pos_algo"]=r["client_id"]; p["sl"]=float(r["sl"]); p["tp"]=float(r["tp"]); p["s2_stop"]=float(r["sl"])
        p["s2_old_attach"]=[x for x in old if x]
    _persist()
    # 整仓单已验证生效后才撤原来按首批数量挂的 TP/SL；撤不掉也无害（同价位、只减仓）。
    for cid in [x for x in old if x]:
        try: alpha_live.cancel_algo(symbol,algo_cl_ord_id=cid)
        except Exception as exc: _activity(f"实盘 {symbol}：方案二原首批保护单撤销未确认（整仓保护已生效）：{exc}","warning")
    _activity(f"实盘 {symbol}：方案二已换成整仓保护单：止损={r['sl']:.8g}（一买低点），兜底止盈={r['tp']:.8g}")


def _live_manage_scheme2(symbol,p,pred,cfg):
    """返回需要清仓的原因（交给通用平仓流程），或 None。"""
    act,T=_scheme2_action(pred,p)
    if not act: return None
    with LOCK: p["s2_done_T"]=T
    _persist()
    typ=act.get("type"); side=p["side"]; d=1 if side=="long" else -1
    if typ=="close":
        return act.get("reason") or "方案二卖点清仓"
    if typ=="mark_sold1":
        with LOCK: p["s2_sold1"]=float(act["price"])
        _activity(f"实盘 {symbol}：{act.get('reason')}（不减仓，之后不再加仓）"); _persist(); return None
    if typ=="reduce_half":
        try:
            _scheme2_protect(symbol,p,float(p.get("s2_stop") or p.get("sl")))
            real=alpha_live.positions(); cs=config.trading.get_ccxt_symbol(symbol)
            try: mode=alpha_live.pos_mode()
            except Exception: mode="long_short_mode"
            rows=[x for x in real if x.get("symbol")==cs and float(x.get("contracts") or 0)>0 and (mode!="long_short_mode" or x.get("side")==side)]
            contracts=sum(float(x.get("contracts") or 0) for x in rows)
            if contracts<=0: return None
            r=alpha_live.reduce_only_close_qty(symbol,side,contracts*0.5)
        except Exception as exc:
            _activity(f"实盘 {symbol}：方案二一卖减半未执行：{exc}；保留原仓位和保护","warning"); return None
        filled=float(r.get("filled") or 0); avg=float(r.get("average") or 0)
        if filled<=0 or avg<=0:
            _activity(f"实盘 {symbol}：方案二一卖减半未确认成交，保留原仓位","warning"); return None
        try: attr=trade_attribution(symbol,float(p["entry"]),avg,side,filled,exit_fee=float(r.get("fee") or 0),reason="方案二一卖减半")
        except Exception: attr={}
        with LOCK:
            before=float(p.get("filled") or contracts) or contracts
            p["filled"]=max(0.0,before-filled); p["notional"]=float(p.get("notional") or 0)*(p["filled"]/before if before else 0)
            p["s2_sold1"]=float(act.get("sold1") or avg)
            STATE.setdefault("attribution",[]).append({**attr,"symbol":symbol,"side":side,"reason":"方案二一卖减半","time":time.time(),"partial":True})
            STATE["attribution"]=STATE["attribution"][-500:]
            STATE["live_trades"].append({"time":time.time(),"symbol":symbol,"side":side,"action":"PARTIAL_CLOSE","reason":act.get("reason"),"filled":filled,"average":avg})
            STATE["history"]=STATE["live_trades"][-300:]
        ledger_record("LIVE_PARTIAL_CLOSE",symbol,{"reason":act.get("reason"),"filled":filled,"average":avg})
        _activity(f"实盘 {symbol}：{act.get('reason')}；成交 {filled:.8g} 张 @ {avg:.8g}"); _persist(); return None
    if typ=="add":
        stage=str(act.get("stage")); stop=float(act["stop"]); ref=float(act.get("ref") or 0)
        why=""
        if ops_breaker_status().get("open"): why="熔断器已开启"
        elif _risk_block(cfg): why="风控保护中："+_risk_block(cfg)
        elif p.get("s2_sold1") is not None: why="已卖过一卖，不再加仓"
        if why:
            _activity(f"实盘 {symbol}：方案二{('二买' if stage=='2' else '类二买')}加仓跳过：{why}","warning"); return None
        try:
            px=float((okx_client.get_ticker(symbol) or {}).get("last") or 0)
            risk=d*(ref-stop); sl_pct=d*(px-stop)/px if px>0 else 0
            if px<=0 or sl_pct<=0 or risk<=0: raise RuntimeError("当前价已越过一买低点")
            if abs(px-ref)>float(fast_v7.PARAMS["max_chase_r"])*risk: raise RuntimeError("当前价偏离收盘触发位，放弃追价")
            equity,free=_live_account_snapshot(max_age=0)
            if equity<=0 or free<=0: raise RuntimeError("权益/可用余额不足")
            notional=_scheme2_leg_notional(equity,free,sl_pct,cfg)
            _scheme2_protect(symbol,p,stop)             # 先挂好整仓保护，再加仓
            r=alpha_live.add_to_position(symbol,side,notional,int(cfg["leverage"]))
        except Exception as exc:
            _activity(f"实盘 {symbol}：方案二加仓未执行：{exc}","warning"); return None
        filled=float(r.get("filled") or 0); avg=float(r.get("average") or 0)
        if filled<=0 or avg<=0:
            _activity(f"实盘 {symbol}：方案二加仓未成交（{r.get('status')}），保持原仓位","warning"); return None
        with LOCK:
            old=float(p.get("filled") or 0)
            p["entry"]=(float(p["entry"])*old+avg*filled)/(old+filled) if old>0 else avg
            p["filled"]=old+filled; p["notional"]=float(p.get("notional") or 0)+float(r.get("notional_usdt") or 0)
            p["s2_stages"]=str(p.get("s2_stages") or "")+stage
            STATE["live_trades"].append({"time":time.time(),"symbol":symbol,"side":side,"action":"ADD","reason":act.get("reason"),"order_id":r.get("order_id"),"filled":filled,"average":avg,"sl":stop})
            STATE["history"]=STATE["live_trades"][-300:]
        ledger_record("LIVE_ADD",symbol,r)
        _activity(f"实盘 {symbol}：{act.get('reason')}；成交 {filled:.8g} 张 @ {avg:.8g}；持仓均价 {p['entry']:.8g}"); _persist()
    return None


def _paper_manage_scheme2(symbol,p,pred,cfg,px,now):
    """方案二本地模拟：一买低点止损、卖点清仓、一卖减半、类二买加仓。"""
    d=1 if p["side"]=="long" else -1; reason=""
    if d*(px-float(p["sl"]))<=0: reason="方案二止损（一买低点）"
    act,T=_scheme2_action(pred,p)
    if not reason and act:
        p["s2_done_T"]=T; typ=act.get("type")
        if typ=="close": reason=act.get("reason") or "方案二卖点清仓"
        elif typ=="mark_sold1": p["s2_sold1"]=float(act["price"])
        elif typ=="reduce_half":
            closed=float(p["notional"])*0.5
            pnl=closed*(d*(px/float(p["entry"])-1)-2*float(cfg["fee_pct"])-2*float(cfg["slippage_pct"]))
            with LOCK:
                STATE["balance"]+=pnl; p["notional"]-=closed; p["s2_sold1"]=float(act.get("sold1") or px)
                STATE["paper_trades"].append(dict(symbol=symbol,side=p["side"],action="CLOSE",entry=p["entry"],exit=px,average=px,pnl=pnl,reason=act.get("reason"),time=now,partial=True))
                STATE["history"]=STATE["paper_trades"][-300:]
            _activity(f"本地模拟 {symbol}：{act.get('reason')}，本次净盈亏={pnl:+.2f} USDT")
        elif typ=="add" and p.get("s2_sold1") is None:
            stop=float(act["stop"]); sl_pct=d*(px-stop)/px
            if sl_pct>0:
                add=_scheme2_leg_notional(STATE["balance"],STATE["balance"],sl_pct,cfg)
                with LOCK:
                    p["entry"]=(float(p["entry"])*float(p["notional"])+px*add)/(float(p["notional"])+add)
                    p["notional"]=float(p["notional"])+add; p["sl"]=stop
                    p["s2_stages"]=str(p.get("s2_stages") or "")+str(act.get("stage"))
                _activity(f"本地模拟 {symbol}：{act.get('reason')}，加仓名义 {add:.2f} USDT")
    if reason:
        pnl=float(p["notional"])*(d*(px/float(p["entry"])-1)-2*float(cfg["fee_pct"])-2*float(cfg["slippage_pct"]))
        with LOCK:
            STATE["balance"]+=pnl
            STATE["paper_trades"].append(dict(symbol=symbol,side=p["side"],action="CLOSE",entry=p["entry"],exit=px,average=px,pnl=pnl,reason=reason,time=now))
            STATE["history"]=STATE["paper_trades"][-300:]
            STATE["positions"].pop(symbol,None)
            STATE["consecutive_losses"]=0 if pnl>0 else STATE["consecutive_losses"]+1
        _activity(f"本地模拟 V7 {symbol}：{reason}，本次净盈亏={pnl:+.2f} USDT")
    _persist()


def _paper_manage_scheme3(symbol,p,pred,cfg,px,now):
    """方案三本地模拟：止盈/止损按价格触发，反向信号按收盘平仓。"""
    d=1 if p["side"]=="long" else -1; reason=""
    if d*(px-float(p["sl"]))<=0: reason="方案三止损25%"
    elif d*(px-float(p["tp"]))>=0: reason="方案三止盈（净1%）"
    else:
        try:
            from alpha_v7_feed import frame as _v7frame
            reason=_scheme3_exit_reason(p,{"1h":_v7frame(okx_client._exchange,config.trading.get_ccxt_symbol(symbol),"1h",count=1000)}) or ""
        except Exception: reason=""
    if reason:
        pnl=float(p["notional"])*(d*(px/float(p["entry"])-1)-2*float(cfg["fee_pct"])-2*float(cfg["slippage_pct"]))
        with LOCK:
            STATE["balance"]+=pnl
            STATE["paper_trades"].append(dict(symbol=symbol,side=p["side"],action="CLOSE",entry=p["entry"],exit=px,average=px,pnl=pnl,reason=reason,time=now))
            STATE["history"]=STATE["paper_trades"][-300:]
            STATE["positions"].pop(symbol,None)
            STATE["consecutive_losses"]=0 if pnl>0 else STATE["consecutive_losses"]+1
        _activity(f"本地模拟 V7 {symbol}：{reason}，本次净盈亏={pnl:+.2f} USDT")
    _persist()


def _scheme3_exit_reason(p,frames):
    import alpha_v7_scheme3 as S3
    f=(frames or {}).get("1h") or {}
    if not len(f.get("ts",[])): return None
    side=1 if p.get("side")=="long" else -1
    entry_bar=int(p.get("s3_bar_ts") or p.get("v7_bar_ts") or 0)+S3.H   # 进场那根 = 信号K的下一根
    return "方案三反向信号平仓" if S3.exit_due(f,side,entry_bar,int(time.time()*1000)) else None


def _live_manage_v7(symbol,p,pred,cfg):
    """V7实盘持仓管理：口径与_live_manage_v6一致；止损改单/平仓都以交易所确认推进。"""
    pending=bool(p.get("v6_recovery_pending")) or p.get("exit_mode")=="RECOVERY"
    if pending:
        plan={"stop":None};reason="V7异常恢复保护未验证，退出并核对成交"
    else:
        tk=okx_client.get_ticker(symbol) or {}; px=float(tk.get("last") or 0)
        if px<=0: return
        frames={}
        if p.get("v7_scheme3"):
            try:
                from alpha_v7_feed import frame as _v7frame
                frames={"1h":_v7frame(okx_client._exchange,config.trading.get_ccxt_symbol(symbol),"1h",count=1000)}
            except Exception as exc:
                with LOCK:STATE['live_error']='方案三1小时K线暂不可用，保留交易所止盈止损：'+str(exc)
        elif p.get("v7_engine") in ("donchian","pine_import","chan_quant") or (p.get("v7_exit_config") or {}).get("close_confirm"):
            try:
                from alpha_v7_feed import bundle
                bt=p.get('v7_base_tf','5m')
                frames=bundle(okx_client._exchange,config.trading.get_ccxt_symbol(symbol),multi=False,base_tf=bt)
            except Exception as exc:
                with LOCK:STATE['live_error']='V7收盘退出数据暂不可用，保留交易所保护：'+str(exc)
        _v7_reconcile_partial(symbol,p)
        if p.get("v7_scheme3"):
            # 方案三：止盈/止损由交易所保护单执行；本地只看反向信号（下一根开盘市价平），不到期、不移动止损。
            plan={"stop":None}; reason=_scheme3_exit_reason(p,frames)
        elif p.get("v7_scheme2"):
            # 方案二：不走通用退出计划（不到期、不移动止损），只按缠论卖点/加仓动作处理。
            plan={"stop":None}; reason=_live_manage_scheme2(symbol,p,pred,cfg)
        else:
            plan=fast_v7.exit_plan(p,px,time.time(),frames,trailing=False)
            reason=plan.get("close")
        if p.get('v7_pine_error') and time.time()-float(p.get('v7_pine_error_logged_at') or 0)>60:
            _activity(f"V7 {symbol} Pine退出指令未执行：{p['v7_pine_error']}；保留交易所保护", "warning")
            p['v7_pine_error_logged_at']=time.time()
        if p.get('recovered') and (not p.get('tp_attach_clordid') or not p.get('sl_attach_clordid')):
            reason='V7异常恢复缺少保护标识，退出并核对成交'
    if reason:
        r=_managed_close(symbol,p["side"])
        if r.get("status")=="no_position": return
        flat=alpha_live.wait_position_closed(symbol,p["side"],timeout=10)
        if not flat.get("closed"):
            with LOCK: STATE["live_error"]=f"{symbol} V7平仓尚未确认，继续管理"
            return
        fill=_exchange_close_fill(symbol,p)
        fill_px=float(fill.get("price") or 0); qty=float(fill.get("qty") or 0)
        if fill_px<=0 or qty<=0:
            _activity(f"V7 {symbol}：仓位归零但成交明细未齐，等待原有成交对账", "warning")
            return
        attr=trade_attribution(symbol,float(p["entry"]),fill_px,p["side"],qty,exit_fee=float(fill.get("fee") or 0),reason=reason)
        row={"time":time.time(),"symbol":symbol,"side":p["side"],"action":"CLOSE","reason":reason,
             "order_id":r.get("order_id"),"filled":qty,"average":fill_px,"flat_confirmed":True,"attribution":attr}
        with LOCK:
            STATE.setdefault("attribution",[]).append({**attr,"symbol":symbol,"side":p["side"],"reason":reason,"time":row["time"]})
            STATE["attribution"]=STATE["attribution"][-500:]
            STATE["live_trades"].append(row); STATE["history"]=STATE["live_trades"][-300:]
            STATE["positions"].pop(symbol,None)
        ledger_record("LIVE_CLOSE",symbol,row)
        if p.get("order_id"): ops_record(p["order_id"],"CLOSED",row)
        _record_live_consecutive_result(attr,reason); _record_strategy_health_close(attr,p,pred)
        _fast_detail(symbol,"V7平仓确认",f"{reason}；实际成交={fill_px:.8g}；净盈亏={float(attr.get('net_pnl') or 0):+.4f} USDT")
        _persist(); return
    # 移动止损与分批止盈由OKX原生算法单托管，本地只在exit_plan给出候选。
    candidate=plan.get("stop"); side=p["side"]; current=float(p.get("sl") or 0)
    better=candidate and ((side=="long" and candidate>current) or (side=="short" and candidate<current))
    if better and p.get("sl_attach_clordid") and time.time()-float(p.get("v6_last_amend") or 0)>=20:
        r=alpha_live.amend_sl_only(symbol,side,float(candidate),p["sl_attach_clordid"],wait_timeout=4.0)
        if r.get("verified"):
            with LOCK: p["sl"]=float(candidate); p["trail_sl"]=float(candidate); p["v6_last_amend"]=time.time()
            _fast_detail(symbol,"V7移动保护",f"止损确认更新至{candidate:.8g}；初始目标不延长")
            _persist()
        else: _activity(f"V7 {symbol}：止损更新未确认，保留已有保护", "warning")


def _paper_manage_v6(symbol,p,pred,cfg,px,now):
    """本地报价模拟，不等同OKX官方模拟盘；部分退出按剩余名义记账。"""
    d=1 if p['side']=='long' else -1; entry=float(p['entry']); reason=''
    frames={}
    if p.get('v6_engine')=='V6_BREAKOUT' or p.get('v62'):
        try: frames=(_fast_mode._fetch_symbol(symbol) or {}).get('frames') or {}
        except Exception: pass
    plan=fast_v6.exit_plan(p,px,now,frames,trailing=gate_enabled('trend_trail'))
    if d*(px-float(p['sl']))<=0: reason='V6结构止损'
    elif d*(px-float(p['tp']))>=0: reason='V6目标止盈'
    elif plan.get('close'): reason=plan['close']
    fraction=1.0; stage=None
    if not reason and gate_enabled('partial_tp'):
        steps=p.setdefault('v6_paper_steps',[])
        for name,progress in [('TP1',.5),('TP2',.8)]:
            if name in steps: continue
            pct=fast_v6.partial_target_pct(p,progress)
            if pct<float(p['base_tp_pct']) and d*(px/entry-1)>=pct:
                base=p.setdefault('v6_paper_initial_notional',float(p['notional']))
                fraction=min(1.0,base*.3/float(p['notional'])); reason='V6分批'+name; stage=name
            break
    if reason:
        closed=float(p['notional'])*fraction
        pnl=closed*(d*(px/entry-1)-2*float(cfg['fee_pct'])-2*float(cfg['slippage_pct']))
        with LOCK:
            STATE['balance']+=pnl
            p['v6_realized']=float(p.get('v6_realized') or 0)+pnl
            STATE['paper_trades'].append(dict(symbol=symbol,side=p['side'],action='CLOSE',entry=entry,exit=px,average=px,pnl=pnl,reason=reason,time=now,partial=bool(stage)))
            STATE['history']=STATE['paper_trades'][-300:]
            if stage:
                p['notional']-=closed; p['v6_paper_steps'].append(stage)
                buffer=fast_v6.partial_floor_pct(p,stage)
                floor=entry*(1+d*buffer)
                p['sl']=max(float(p['sl']),floor) if d==1 else min(float(p['sl']),floor)
            else:
                STATE['positions'].pop(symbol,None)
                STATE['consecutive_losses']=0 if p['v6_realized']>0 else STATE['consecutive_losses']+1
        _activity(f"本地模拟 V6 {symbol}：{reason}，本次净盈亏={pnl:+.2f} USDT")
    if symbol in STATE['positions'] and plan.get('stop') is not None:
        with LOCK: p['sl']=max(float(p['sl']),plan['stop']) if d==1 else min(float(p['sl']),plan['stop'])
    unreal=0.0
    for sym,pos in list(STATE['positions'].items()):
        quote=px if sym==symbol else float((okx_client.get_ticker(sym) or {}).get('last') or pos['entry'])
        sd=1 if pos['side']=='long' else -1
        unreal+=float(pos['notional'])*(sd*(quote/float(pos['entry'])-1)-2*float(cfg['fee_pct'])-2*float(cfg['slippage_pct']))
    with LOCK: STATE['equity']=STATE['balance']+unreal
    _persist()

def _paper_manage_v7(symbol,p,pred,cfg,px,now):
    """V7本地报价模拟，口径同_paper_manage_v6；部分退出按剩余名义记账。"""
    if p.get('v7_scheme2'):
        _paper_manage_scheme2(symbol,p,pred,cfg,px,now); return
    if p.get('v7_scheme3'):
        _paper_manage_scheme3(symbol,p,pred,cfg,px,now); return
    d=1 if p['side']=='long' else -1; entry=float(p['entry']); reason=''
    frames={}
    if p.get('v7_engine') in ('donchian','pine_import','chan_quant') or (p.get('v7_exit_config') or {}).get('close_confirm'):
        try:
            from alpha_v7_feed import bundle
            bt=p.get('v7_base_tf','5m')
            frames=bundle(okx_client._exchange,config.trading.get_ccxt_symbol(symbol),multi=False,base_tf=bt)
        except Exception: pass
    trailing=gate_enabled('trend_trail')
    if trailing and (p.get('v7_exit_config') or {}).get('runner'):
        p['tp']=fast_v7.runner_target(p);p['v7_runner_active']=True
    elif p.get('v7_runner_active'):
        p['tp']=p['original_tp_price'];p['v7_runner_active']=False
    plan=fast_v7.exit_plan(p,px,now,frames,trailing=False)
    # Emulate OKX native callback with the same entry-locked activation/distance.
    if trailing:
        active,callback=fast_v7.native_trail_config(p)
        if d*(px-active)>=0:p['v7_paper_trail_active']=True
        if p.get('v7_paper_trail_active'):
            anchor=max(px,float(p.get('trail_highest_price') or px)) if d==1 else min(px,float(p.get('trail_lowest_price') or px))
            p['trail_highest_price' if d==1 else 'trail_lowest_price']=anchor
            native_stop=anchor*(1-d*callback)
            old=float(p.get('v7_paper_native_stop') or native_stop)
            p['v7_paper_native_stop']=max(old,native_stop) if d==1 else min(old,native_stop)
            if d*(px-p['v7_paper_native_stop'])<=0:reason='V7原生追踪模拟退出'
    else:
        p.pop('v7_paper_trail_active',None);p.pop('v7_paper_native_stop',None)
    if d*(px-float(p['sl']))<=0: reason='V7结构止损'
    elif not reason and d*(px-float(p['tp']))>=0: reason='V7目标止盈'
    elif plan.get('close'): reason=plan['close']
    fraction=1.0; stage=None
    if not reason and gate_enabled('partial_tp'):
        steps=p.setdefault('v7_paper_steps',[])
        for name,progress in [('TP1',.5),('TP2',.8)]:
            if name in steps: continue
            pct=fast_v7.partial_target_pct(p,progress)
            if pct<float(p['base_tp_pct']) and d*(px/entry-1)>=pct:
                base=p.setdefault('v7_paper_initial_notional',float(p['notional']))
                fraction=min(1.0,base*.3/float(p['notional'])); reason='V7分批'+name; stage=name
            break
    if reason:
        closed=float(p['notional'])*fraction
        pnl=closed*(d*(px/entry-1)-2*float(cfg['fee_pct'])-2*float(cfg['slippage_pct']))
        with LOCK:
            STATE['balance']+=pnl
            p['v7_realized']=float(p.get('v7_realized') or 0)+pnl
            STATE['paper_trades'].append(dict(symbol=symbol,side=p['side'],action='CLOSE',entry=entry,exit=px,average=px,pnl=pnl,reason=reason,time=now,partial=bool(stage)))
            STATE['history']=STATE['paper_trades'][-300:]
            if stage:
                p['notional']-=closed; p['v7_paper_steps'].append(stage)
                buffer=fast_v7.partial_floor_pct(p,stage)
                floor=entry*(1+d*buffer)
                p['sl']=max(float(p['sl']),floor) if d==1 else min(float(p['sl']),floor)
            else:
                STATE['positions'].pop(symbol,None)
                STATE['consecutive_losses']=0 if p['v7_realized']>0 else STATE['consecutive_losses']+1
        _activity(f"本地模拟 V7 {symbol}：{reason}，本次净盈亏={pnl:+.2f} USDT")
    if symbol in STATE['positions'] and plan.get('stop') is not None:
        with LOCK: p['sl']=max(float(p['sl']),plan['stop']) if d==1 else min(float(p['sl']),plan['stop'])
    unreal=0.0
    for sym,pos in list(STATE['positions'].items()):
        quote=px if sym==symbol else float((okx_client.get_ticker(sym) or {}).get('last') or pos['entry'])
        sd=1 if pos['side']=='long' else -1
        unreal+=float(pos['notional'])*(sd*(quote/float(pos['entry'])-1)-2*float(cfg['fee_pct'])-2*float(cfg['slippage_pct']))
    with LOCK: STATE['equity']=STATE['balance']+unreal
    _persist()


def _paper_step(symbol,pred,cfg):
    cfg=_scheme3_cfg(cfg)
    if not pred.get("model_ready") and (STATE["positions"].get(symbol) or {}).get("fast_version") not in ("v6","v7"): return
    ticker=okx_client.get_ticker(symbol) or {}
    px=float(ticker.get("last") or 0)
    if px<=0: return
    now=time.time()
    p0=STATE["positions"].get(symbol)
    if p0 and p0.get("fast_version")=="v7":
        _paper_manage_v7(symbol,p0,pred,cfg,px,now)
        return
    if p0 and p0.get("fast_version")=="v6":
        _paper_manage_v6(symbol,p0,pred,cfg,px,now)
        return
    if not p0 and (pred.get("fast_strategy") or {}).get("engine_version")=="v7":
        if not _v7_entry_guard(symbol,pred,px,now): return
    elif not p0 and not _v6_entry_guard(symbol,pred,px,now): return
    with LOCK:
        p=STATE["positions"].get(symbol)
        if p:
            ret=(px/p["entry"]-1)*(1 if p["side"]=="long" else -1); reason=""
            if (p["side"]=="long" and px<=p["sl"]) or (p["side"]=="short" and px>=p["sl"]): reason="SL"
            elif (p["side"]=="long" and px>=p["tp"]) or (p["side"]=="short" and px<=p["tp"]): reason="TP"
            elif now-p["opened_at"]>=p["max_seconds"]: reason="TIME"
            if reason:
                pnl=p["notional"]*(ret-cfg["fee_pct"]*2-cfg["slippage_pct"]*2); STATE["balance"]+=pnl
                STATE["paper_trades"].append({"symbol":symbol,"side":p["side"],"action":"CLOSE","entry":p["entry"],"exit":px,"average":px,"pnl":pnl,"reason":reason,"time":now})
                STATE["history"]=STATE["paper_trades"][-300:]; STATE["positions"].pop(symbol,None)
                STATE["consecutive_losses"]=0 if pnl>0 else STATE["consecutive_losses"]+1
                _activity(f"模拟盘 {symbol}：平仓，原因={reason}，本次盈亏={pnl:+.2f} USDT")
        block=_risk_block(cfg)
        if block:
            _activity(f"模拟盘 {symbol}：当前禁止开仓，原因={block}", "warning")
        if symbol not in STATE["positions"] and not block and len(STATE["positions"])<cfg["max_positions"] and not _same_side_blocked(symbol,pred,cfg):
            recent=[t for t in reversed(STATE["paper_trades"]) if t.get("symbol")==symbol]
            if not recent or now-recent[0]["time"]>=cfg["cooldown_minutes"]*60:
                side="long" if pred["signal"]=="LONG" else ("short" if pred["signal"]=="SHORT" else None)
                if side:
                    entry_check=(pred.get("entry_price_confirmation") or {}) if (pred.get("fast_strategy") or {}).get("engine_version") in ("v6","v7") else _entry_price_confirmation(symbol,side,str(pred.get("timeframe") or cfg.get("timeframe") or "15m"))
                    if entry_check.get("decision") == "WAIT":
                        _activity(f"模拟盘 {symbol}：入场价格确认 → 等待；评分={float(entry_check.get('score',0))*100:.1f}%；原因={entry_check.get('reason','位置明显不适合')}")
                        return
                    if entry_check.get("decision") == "ENTER":
                        _activity(f"模拟盘 {symbol}：入场价格确认 → 通过；评分={float(entry_check.get('score',0))*100:.1f}%；当前价={float(entry_check.get('entry_price',0) or 0):.8g}")
                    else:
                        _activity(f"模拟盘 {symbol}：入场价格确认 → 关闭，恢复原有下单流程")
                    dyn=pred.get("dynamic_tp_sl") or dynamic_tp_sl(float(pred.get("base_tp",pred["tp"])),float(pred.get("base_sl",pred["sl"])),pred.get("market_context") or {},side=side)
                    adaptive_mult=float((pred.get("adaptive_context") or {}).get("size_multiplier",1.0) or 1.0)
                    risk_notional=STATE["balance"]*cfg["risk_pct"]*max(float(pred.get("confidence",0)),0.0)/max(dyn["sl"],0.002)
                    risk_notional*=max(0.55,min(1.20,adaptive_mult))
                    fast_entry_mult=float(pred.get("fast_entry_size_multiplier",1.0) or 1.0) if _is_fast(pred) else 1.0
                    fast_entry_mult=max(0.25,min(1.0,fast_entry_mult))
                    risk_notional*=fast_entry_mult
                    if (pred.get("fast_strategy") or {}).get("v7_scheme2"):   # 方案二每批：单笔风险×1/3÷止损距离
                        risk_notional=_scheme2_leg_notional(STATE["balance"],STATE["balance"],float(dyn["sl"]),cfg)
                    if (pred.get("fast_strategy") or {}).get("v7_scheme3"):
                        risk_notional=_scheme3_notional(STATE["balance"],STATE["balance"],cfg)
                    if _is_fast(pred) and fast_entry_mult < 0.999:
                        _activity(f"模拟盘 {symbol}：FAST入场强度={pred.get('signal_tier','一般')}；该级别降仓系数={fast_entry_mult:.2f}，只降低仓位")
                    notional=max(10,min(STATE["balance"]*cfg["max_notional_pct"],risk_notional))
                    # 入场价格层只负责“进/等”，很强、强、一般均使用正常仓位；仓位仍由原有风险系统决定。
                    candles=okx_client.get_ohlcv(symbol=symbol,timeframe=cfg.get("timeframe","15m"),limit=300)
                    pipeline=pretrade_pipeline(symbol=symbol,side=side,candles=candles,ticker=ticker,equity=float(STATE["equity"]),free=float(STATE["balance"]),
                                               requested_notional=notional,stop_pct=float(dyn["sl"]),confidence=float(pred.get("confidence",0)),
                                               stress=float((pred.get("market_context") or {}).get("stress",0)),cfg={**cfg,"gate_switches":dict(STATE.get("gate_switches") or DEFAULT_GATE_SWITCHES)},live=False,
                                               health_ok=True,reconciliation_ok=True,model_ok=bool(pred.get("model_ready")))
                    try: ledger_record("PAPER_PRETRADE_PIPELINE",symbol,pipeline)
                    except Exception: pass
                    if not pipeline.get("ready"):
                        _activity(f"模拟盘 {symbol}：风控/执行检查未通过，禁止开仓", "warning")
                        return
                    notional=float(pipeline["planned_notional"])
                    if notional<=0: return
                    if not STATE["running"]: return
                    ep=px if (pred.get("fast_strategy") or {}).get("engine_version") in ("v6","v7") else px*(1+cfg["slippage_pct"] if side=="long" else 1-cfg["slippage_pct"])
                    STATE["positions"][symbol]={"side":side,"entry":ep,"tp":ep*(1+dyn["tp"] if side=="long" else 1-dyn["tp"]),"sl":ep*(1-dyn["sl"] if side=="long" else 1+dyn["sl"]),"notional":notional,"opened_at":now,"max_seconds":_position_max_seconds(pred),"strategy_mode":pred.get("strategy_mode","SHORT"),"fast_engine":str((pred.get("fast_strategy") or {}).get("v4_engine") or ""),**_fast_position_meta(pred),"strategy_label":pred.get("strategy_label","短线"),"pipeline":pipeline,"base_tp_pct":float(pred.get("base_tp",pred["tp"])),"base_sl_pct":float(pred.get("base_sl",pred["sl"])),"dynamic_tp_pct":dyn["tp"],"dynamic_sl_pct":dyn["sl"],"dynamic_reason":dyn["reason"]}
                    _fs_claim=pred.get("fast_strategy") or {}
                    if _fs_claim.get("engine_version")=="v7": _v7_claim_signal(symbol,pred)
                    else: _v6_claim_signal(symbol,pred)
                    _activity(f"模拟盘 {symbol}：开仓 {side}，价格={ep:.6g}，名义金额={notional:.2f} USDT，动态TP={dyn['tp']*100:.2f}% 动态SL={dyn['sl']*100:.2f}%（{dyn['reason']}）")
                    STATE["paper_trades"].append({"symbol":symbol,"side":side,"action":"OPEN","entry":ep,"average":ep,"filled":notional/max(ep,1e-12),"pnl":0.0,"reason":str(pred.get("reason") or "")[:120],"time":now})
                    STATE["history"]=STATE["paper_trades"][-300:]
    # 多币种必须逐仓取价，否则一个币的价格会错误影响所有持仓。
    unreal=0.0
    with LOCK: positions=dict(STATE["positions"])
    for s,p in positions.items():
        try: q=float((okx_client.get_ticker(s) or {}).get("last") or p["entry"])
        except Exception: q=p["entry"]
        unreal+=p["notional"]*((q/p["entry"]-1)*(1 if p["side"]=="long" else -1)-cfg["fee_pct"])
    with LOCK:
        STATE["equity"]=STATE["balance"]+unreal; STATE["risk_block"]=_risk_block(cfg); STATE["last_cycle"]=time.time()
    _persist()



def _black_window_signal_header(symbol, pred):
    """Write the signal/confirmation part of the live decision chain to the black window."""
    model_sig = str(pred.get("signal") or "FLAT").upper()
    conf = float(pred.get("confidence", 0) or 0) * 100.0
    committee = pred.get("strategy_committee") or {}
    committee_sig = str(committee.get("signal") or "FLAT").upper()
    agreement = float(committee.get("agreement", 0) or 0) * 100.0
    score = float(committee.get("committee_score", 0) or 0)
    if model_sig == "FLAT":
        model_status = "FLAT"
    else:
        model_status = "PASS"
    # COMMITTEE is confirmation information; a strong opposite committee is a veto,
    # while neutral/disagreement is explicitly shown rather than hidden.
    veto = bool(model_sig in ("LONG","SHORT") and committee_sig in ("LONG","SHORT")
                and committee_sig != model_sig and agreement >= 55.0 and abs(score) >= 0.30)
    if veto:
        committee_status = "BLOCK"
    elif committee_sig == model_sig and model_sig != "FLAT":
        committee_status = "PASS"
    elif committee_sig == "FLAT":
        committee_status = "NEUTRAL"
    else:
        committee_status = "WEAK/CONFLICT"
    _activity(f"实盘 {symbol}：{'快速策略' if str(pred.get('strategy_mode') or '').upper()=='FAST' else 'MODEL'} → {model_sig} {conf:.1f}% → {model_status}")
    adaptive=pred.get("adaptive_context") or {}
    if adaptive.get("enabled"):
        rg=adaptive.get("regime") or {}; mtf=adaptive.get("multi_timeframe") or {}
        ll=adaptive.get("lead_lag") or {}
        _activity(f"实盘 {symbol}：市场状态    → {rg.get('label','未知')}；多周期共振 → {mtf.get('label','未知')}；Lead-Lag → {ll.get('label','未知')}；仓位系数 → {float(adaptive.get('size_multiplier',1.0)):.2f}")
    else:
        _activity(f"实盘 {symbol}：市场状态/多周期 → 已关闭，使用原信号路径")
    _activity(f"实盘 {symbol}：COMMITTEE   → {committee_sig}（一致度 {agreement:.1f}%）→ {committee_status}"
              + ("，强反向确认" if veto else ""))

def _fast_detail(symbol, stage, message, level="info"):
    """FAST专用黑窗口细节；不改变任何交易判断。"""
    if level == "warning":
        _activity(f"实盘 {symbol}：快速实盘｜{stage}｜{message}", "warning")
    elif level == "error":
        _activity(f"实盘 {symbol}：快速实盘｜{stage}｜{message}", "error")
    else:
        _activity(f"实盘 {symbol}：快速实盘｜{stage}｜{message}")

def _is_fast(pred):
    return str((pred or {}).get("strategy_mode") or "").upper() == "FAST"

def _is_v7(pred):
    return ((pred or {}).get("fast_strategy") or {}).get("engine_version") == "v7"

def _scheme3_on():
    try: return bool(fast_v7.get_runtime_params().get("chan_scheme3"))
    except Exception: return False

def _scheme3_cfg(cfg):
    """方案三打开时：最多 10 仓、同方向不限、平仓后不冷却（与回测一致）；其余设置不变。"""
    if not _scheme3_on(): return cfg
    import alpha_v7_scheme3 as S3
    return {**cfg,"max_positions":S3.MAX_POSITIONS,"max_same_side":0,"cooldown_minutes":0}

def _scheme3_notional(equity,free,cfg):
    """方案三每笔名义金额 = 账户权益 × 10%（不超过 可用 × 杠杆 × 80%）。"""
    import alpha_v7_scheme3 as S3
    return max(0.0,min(float(equity)*S3.EQUITY_FRAC,float(free)*float(cfg.get("leverage",1))*0.80))

def _v7_priority(pred):
    """空位不够时的开仓顺序：往返成本占止损距离的比例越小越优先（止损越宽、手续费占比越低）。"""
    fs=(pred or {}).get("fast_strategy") or {}
    return float(fs.get("estimated_round_cost") or 0.0022)/max(float(pred.get("sl") or 0),1e-6)

def _v7_entry_plan(preds, held, max_positions):
    """本轮处理顺序：已持仓的币先管理；V7 新开仓信号按成本占比从低到高；其余照原顺序。
    返回 (顺序, 需要按满额单笔风险下单的币, 空位不够时的提示)。"""
    new=[s for s,p in preds.items() if s not in held and p.get("signal") in ("LONG","SHORT") and p.get("model_ready") and _is_v7(p)]
    new.sort(key=lambda s:_v7_priority(preds[s]))
    free=max(0,int(max_positions)-len(held))
    msg=f"V7 本轮 {len(new)} 个开仓信号、空位 {free} 个；按成本占比优先：{'、'.join(new[:free]) or '无'}" if len(new)>free else ""
    order=[s for s in preds if s in held]+new+[s for s in preds if s not in held and s not in new]
    return order,new,msg

def _same_side_blocked(symbol, pred, cfg):
    """同方向持仓数上限（0=不限制）。返回原因文字或空字符串。"""
    cap=int(cfg.get("max_same_side") or 0)
    if cap<=0 or pred.get("signal") not in ("LONG","SHORT"): return ""
    side="long" if pred["signal"]=="LONG" else "short"
    with LOCK: n=sum(1 for p in (STATE.get("positions") or {}).values() if p.get("side")==side)
    return f"同方向（{'多' if side=='long' else '空'}）持仓已达上限 {n}/{cap}" if n>=cap else ""

def _black_window_pipeline_chain(symbol, pred, pipeline):
    """Write every pre-trade gate and the final decision to the black window."""
    def st(v):
        return "PASS" if bool(v) else "BLOCK"

    q = pipeline.get("quality") or {}
    liq = pipeline.get("liquidity") or {}
    cap = pipeline.get("capacity") or {}
    impact = pipeline.get("impact") or {}
    risk = pipeline.get("risk") or {}
    route = pipeline.get("route") or {}
    gate = pipeline.get("gate") or {}
    checks = gate.get("checks") or {}

    market_ok = q.get("ok", True)
    liquidity_ok = bool(liq.get("ok", True))
    capacity_ok = bool(cap.get("ok", cap.get("within_capacity", True)))
    impact_ok = bool(impact.get("impact_ok", True))
    # impact estimate is informational unless the production gate/route actually
    # rejected it; surface the measured cost so the operator can see why.
    impact_bps = float(impact.get("impact_bps", impact.get("expected_cost_bps", 0)) or 0)
    if "impact_ok" in impact:
        impact_ok = bool(impact.get("impact_ok"))
    elif "ok" in impact:
        impact_ok = bool(impact.get("ok"))
    route_ok = bool(route.get("ok", True))
    risk_ok = bool(risk.get("ok", True))
    planned = float(pipeline.get("planned_notional", 0) or 0)
    rs = pipeline.get("risk_sizing") or {}
    cvar_mode = str(rs.get("cvar_mode") or "正常")
    cvar = float(rs.get("cvar", 0) or 0)
    cvar_mult = float(rs.get("multiplier", 1) or 1)

    _activity(f"实盘 {symbol}：MARKET      → {st(market_ok)}")
    _activity(f"实盘 {symbol}：LIQUIDITY   → {st(liquidity_ok)}")
    _activity(f"实盘 {symbol}：CAPACITY    → {st(capacity_ok)}")
    _activity(f"实盘 {symbol}：IMPACT COST → {st(impact_ok)}（{impact_bps:.2f} bps）")
    if risk_ok and cvar_mult < 1.0:
        _activity(f"实盘 {symbol}：RISK        → 降仓（CVaR {cvar*100:.2f}%；风险系数 {cvar_mult:.2f}）")
    elif risk_ok:
        _activity(f"实盘 {symbol}：RISK        → PASS（CVaR {cvar*100:.2f}%；{cvar_mode}）")
    else:
        _activity(f"实盘 {symbol}：RISK        → BLOCK（CVaR {cvar*100:.2f}%；{cvar_mode}）")
    _activity(f"实盘 {symbol}：RECONCILE   → {st((pipeline.get('reconciliation') or {}).get('ok', True))}")
    _activity(f"实盘 {symbol}：RECOVERY    → {st((pipeline.get('recovery') or {}).get('ok', True))}")
    _activity(f"实盘 {symbol}：CLOCK       → {st((pipeline.get('clock') or {}).get('ok', True))}")
    _activity(f"实盘 {symbol}：AUDIT       → {st((pipeline.get('audit') or {}).get('ok', True))}")
    _activity(f"实盘 {symbol}：EXECUTION   → {st(route_ok and planned > 0)}")

    pre_checks = pipeline.get("pre_checks") or {}
    failed = [str(k) for k,v in checks.items() if not v]
    failed += [str(k) for k,v in pre_checks.items() if not v and str(k) not in failed]
    if not pipeline.get("ready"):
        reason = pipeline.get("reason", "INTEGRATED_GATE_BLOCK")
        final_reason = "；".join(failed) if failed else reason
        if not failed and planned <= 0:
            final_reason = "计划下单金额为0"
        _activity(f"实盘 {symbol}：FINAL       → NO ORDER")
        _activity(f"实盘 {symbol}：原因        → {final_reason}", "warning")
    elif planned <= 0:
        _activity(f"实盘 {symbol}：FINAL       → NO ORDER")
        _activity(f"实盘 {symbol}：原因        → 执行金额为0", "warning")
    else:
        _activity(f"实盘 {symbol}：FINAL       → READY TO ORDER")


def _reconciliation_check(real_positions):
    """Verify every locally owned live position still exists on OKX with the same side.
    Unknown/manual exchange positions are never adopted or modified here.
    """
    with LOCK:
        local=dict(STATE.get("positions") or {})
    real=list(real_positions or [])
    problems=[]
    for symbol,p in local.items():
        cs=config.trading.get_ccxt_symbol(symbol)
        matches=[x for x in real if x.get("symbol")==cs and float(x.get("contracts") or 0)>0]
        side=str(p.get("side") or "")
        if not matches:
            problems.append(f"本地持仓{symbol}在交易所不存在")
        elif side:
            # net_mode下单向持仓side可能为None，跳过方向检查；long_short_mode才检查方向
            try: mode_check=alpha_live.pos_mode()
            except Exception: mode_check="long_short_mode"
            if mode_check=="long_short_mode" and not any(str(x.get("side") or "")==side for x in matches):
                problems.append(f"{symbol}持仓方向不一致")
    return {"ok":not problems,"problems":problems,"local_count":len(local),"real_count":len(real)}

def _audit_check():
    """Validate local audit/state stores before allowing new risk."""
    try:
        rows=ledger_recent(1)
        orders=ops_open_orders()
        if not isinstance(rows,list) or not isinstance(orders,dict):
            return {"ok":False,"reason":"审计存储结构异常"}
        for oid,rec in orders.items():
            if not isinstance(rec,dict) or not isinstance(rec.get("events",[]),list):
                return {"ok":False,"reason":f"订单审计记录损坏:{oid}"}
        return {"ok":True,"recent_ledger":len(rows),"tracked_orders":len(orders)}
    except Exception as exc:
        return {"ok":False,"reason":f"审计存储读取失败:{exc}"}

def _clock_check(cfg):
    try:
        return alpha_live.clock_status(int(cfg.get("clock_max_skew_ms",3000)))
    except Exception as exc:
        return {"ok":False,"error":str(exc)}

def _strategy_health_advice(symbol: str, pred: dict) -> dict:
    """交易后复盘只做软性仓位调整，不改变方向、不绕过硬风控。"""
    try:
        if not isinstance(pred, dict) or pred.get("signal") not in ("LONG", "SHORT"):
            return {"size_multiplier": 1.0, "health": "观察中", "action": "正常参与"}
        ctx=pred.get("market_context") or {}
        return strategy_health_advise(symbol, str(pred.get("strategy_mode") or STATE.get("strategy_mode") or "SHORT"),
                                      "long" if pred.get("signal")=="LONG" else "short", str(ctx.get("regime") or "UNKNOWN"))
    except Exception as exc:
        logger.debug(f"策略复盘建议读取失败 {symbol}: {exc}")
        return {"size_multiplier": 1.0, "health": "不可用", "action": "不调整原策略"}


def _record_strategy_health_close(attr: dict, position: dict | None = None, prediction: dict | None = None) -> dict:
    """仅在已确认平仓后记录一次；模块异常绝不影响正常平仓链路。"""
    try:
        result=strategy_health_record(attr or {}, position or {}, prediction or {})
        review=result.get("trade") or {}
        if review:
            pattern=review.get("failure_pattern") or "无"
            action=result.get("action") or "正常参与"
            _activity(f"交易复盘 {review.get('symbol','')}：{review.get('strategy_mode','')} {review.get('side','')} 本次盈亏={float(review.get('net_pnl') or 0):+.2f}U；结果={review.get('health','观察中')}；失败模式={pattern}；下一次={action}")
        return result
    except Exception as exc:
        logger.warning(f"[ALPHA-X] 交易复盘记录失败，不影响平仓结果: {exc}")
        return {"recorded": False, "reason": str(exc)}


def _fmt_factor(k, v) -> str:
    """入场因子可能是数值(按百分比显示)、文字标签，也可能是 dict/list（小币种偶发）；全部安全格式化，绝不抛错。"""
    try:
        if isinstance(v, dict):
            for kk in ("value", "val", "score", "price", "pct", "ratio"):
                if kk in v:
                    try:
                        return f"{k}={float(v[kk]) * 100:.0f}%"
                    except (TypeError, ValueError):
                        pass
            return f"{k}={v.get('label') or v.get('name') or '—'}"
        if isinstance(v, (list, tuple)):
            return f"{k}={'/'.join(str(x) for x in v[:3])}"
        return f"{k}={float(v) * 100:.0f}%"
    except (TypeError, ValueError):
        return f"{k}={v}"


def _entry_price_confirmation(symbol: str, side: str, timeframe: str = "15m") -> dict:
    """Final price-location check after direction is fixed; never changes direction or risk rules."""
    if not gate_enabled("entry_price"):
        return {"enabled": False, "decision": "BYPASS", "score": 1.0, "reason": "入场价格确认已关闭，直接进入原有下单流程"}
    try:
        candles=okx_client.get_ohlcv(symbol=symbol,timeframe=timeframe,limit=300)
        candles=_drop_incomplete(_df(candles),timeframe)
        ticker=okx_client.get_ticker(symbol) or {}
        px=float(ticker.get("last") or 0)
        result=evaluate_entry_location(candles,side,px)
        score=float(result.get("score",0.0) or 0.0)
        # 与 FAST 内置入场价格使用同一套标准：很强/强/一般均可正常仓位进入，只有明显不适合才等待。
        if result.get("decision") == "ENTER":
            result["entry_size_multiplier"] = 1.00
        else:
            result["entry_size_multiplier"] = 0.0
        result["timeframe"]=timeframe
        result["symbol"]=symbol
        ledger_record("ENTRY_PRICE_CONFIRMATION",symbol,result)
        factors=result.get("factors") or {}
        top="；".join(_fmt_factor(k,v) for k,v in factors.items())
        _activity(f"{symbol}：入场位置分析｜方向={('做多' if side=='long' else '做空')}｜评分={float(result.get('score',0))*100:.1f}%｜{top}")
        return result
    except Exception as exc:
        # Optional feature failure is fail-safe: wait for the next cycle instead of entering.
        _activity(f"{symbol}：入场价格确认模块异常，暂缓本轮开仓：{exc}","warning")
        return {"enabled": True, "decision": "WAIT", "score": 0.0, "reason": f"入场位置分析异常，等待重试：{exc}"}


def _arm_native_exits_after_open(symbol: str, cfg: dict):
    with LOCK:
        p=dict(STATE.get("positions",{}).get(symbol) or {})
    if not p.get("live"): return
    if p.get("v7_scheme2"):
        _activity(f"实盘 {symbol}：方案二持仓不挂移动止损/分批止盈（按缠论卖点离场，一买低点止损）")
        return
    if p.get("v7_scheme3"):
        _activity(f"实盘 {symbol}：方案三持仓不挂移动止损/分批止盈（净1%止盈、25%止损、反向信号平仓）")
        return
    errors=[]
    if gate_enabled("trend_trail"):
        try: _arm_native_trail(symbol,p,cfg)
        except Exception as exc: errors.append(f"移动止损: {exc}")
    if gate_enabled("partial_tp"):
        try:
            with LOCK: p=dict(STATE.get("positions",{}).get(symbol) or {})
            _arm_native_partial(symbol,p,cfg)
        except Exception as exc: errors.append(f"分批止盈: {exc}")
    if errors:
        reason="；".join(errors)
        ops_open_breaker(f"NATIVE_EXIT_ARM_FAILED:{symbol}")
        _activity(f"实盘 {symbol}：原生退出托管未全部接通，已打开保护熔断；原固定保护仍在: {reason}","error")


def _live_step(symbol,pred,cfg,allocation_multiplier=1.0):
    """Integrated production path: signal -> portfolio/risk -> capacity/impact -> route -> OKX."""
    cfg=_scheme3_cfg(cfg)
    if (pred.get("fast_strategy") or {}).get("v62") and not gate_enabled("v63_live"):
        _activity(f"V6.3 {symbol}：研究引擎默认仅支持本地模拟；如需真实开仓，请在风控开关中显式打开“V6.3真实开仓许可”（未通过压力盈利验证，风险自担）", "warning")
        _activity(f"实盘 {symbol}：FINAL       → NO ORDER")
        _activity(f"实盘 {symbol}：原因        → V63_LIVE 许可未开启", "warning")
        return
    if not _resume_close_recovery():
        _activity(f"实盘 {symbol}：存在未完成平仓/残仓恢复任务，本轮禁止新开仓", "warning")
        _activity(f"实盘 {symbol}：FINAL       → NO ORDER")
        _activity(f"实盘 {symbol}：原因        → CLOSE RECOVERY 未完成", "warning")
        return
    with LOCK:
        protection_block = (STATE.get("protection_blocks") or {}).get(symbol)
        flat_cleanup_pending = symbol in (STATE.get('flat_cleanup_pending') or {})
        flat_fill_pending = symbol in (STATE.get('flat_fill_pending') or {})
    if protection_block or flat_cleanup_pending or flat_fill_pending:
        _activity(f"实盘 {symbol}：平仓残留单或成交对账未完成，禁止重新开仓；原因={(protection_block or {}).get('reason','等待残留单清理及成交明细')} ", "warning")
        _activity(f"实盘 {symbol}：FINAL       → NO ORDER")
        _activity(f"实盘 {symbol}：原因        → PROTECTION FAILURE QUARANTINE", "warning")
        return
    if gate_enabled("model_ready") and not pred.get("model_ready"):
        _activity(f"实盘 {symbol}：模型不可用，跳过本轮", "warning")
        _activity(f"实盘 {symbol}：FINAL       → NO ORDER")
        _activity(f"实盘 {symbol}：原因        → {'快速策略' if str(pred.get('strategy_mode') or '').upper()=='FAST' else 'MODEL'} 未准备好", "warning")
        return
    if pred.get("signal")=="FLAT":
        if (pred.get('fast_strategy') or {}).get('engine_version')=='v7':
            _activity(f"实盘 {symbol}：V7 {(pred.get('fast_strategy') or {}).get('v7_base_tf') or str(pred.get('timeframe') or '5m').split(' ')[0]}收盘策略等待；{pred.get('reason','')}；本轮无订单")
            return
        if _is_fast(pred):
            fs=pred.get("fast_strategy") or {}; ctx=pred.get("adaptive_context",{}).get("multi_timeframe",{}) or {}
            _fast_detail(symbol,"方向确认",f"大方向={ctx.get('direction_source','未确认')}；4H={fs.get('4h_label','未知')}；1H={fs.get('1h_label','未知')}；方向={ctx.get('direction',0)}")
            _fast_detail(symbol,"15m结构",f"{fs.get('15m_label','未知')}；结构评分={float(fs.get('structure_score',0))*100:.1f}%")
            _fast_detail(symbol,"5m触发",f"触发评分={float(fs.get('trigger_score',0))*100:.1f}%；证据={','.join(fs.get('trigger_evidence') or []) or '无'}")
            _fast_detail(symbol,"最终判断",f"FLAT；原因={pred.get('reason','未形成可执行条件')}","warning")
        _activity(f"实盘 {symbol}：当前无交易信号，继续观察")
        _activity(f"实盘 {symbol}：FINAL       → NO ORDER")
        _activity(f"实盘 {symbol}：原因        → {pred.get('reason','快速策略未形成可执行条件')}", "warning")
        return
    if ops_breaker_status().get("open"):
        _activity(f"实盘 {symbol}：熔断器已开启，禁止新开仓", "warning")
        _activity(f"实盘 {symbol}：FINAL       → NO ORDER")
        _activity(f"实盘 {symbol}：原因        → 熔断器", "warning")
        return
    global INTEGRATION_FENCE
    if gate_enabled("ha_fencing") and INTEGRATION_FENCE and not authorize_execution(INTEGRATION_FENCE):
        with LOCK: STATE["risk_block"]="执行主节点 fencing 无效，禁止新开仓"
        _activity(f"实盘 {symbol}：执行授权检查未通过，原因=执行主节点 fencing 无效", "warning")
        _activity(f"实盘 {symbol}：FINAL       → NO ORDER")
        _activity(f"实盘 {symbol}：原因        → HA FENCING", "warning")
        return
    side="long" if pred["signal"]=="LONG" else "short"; now=time.time()
    if _is_fast(pred):
        _fast_detail(symbol,"快速策略",f"FAST日内裸K启动；不读取训练模型；本轮只用真实OKX价格数据")
        fd=pred.get("fast_data") or {}
        _fast_detail(symbol,"实时数据",f"5m触发/15m结构/1H+4H方向；已完成K线={fd.get('completed_bars',{}) if isinstance(fd,dict) else '已检查'}；未收盘K线不参与")
        _fast_detail(symbol,"方向确认",f"方向={pred.get('signal')}；强度={(pred.get('fast_strategy') or {}).get('tier','未知')}；置信度={float(pred.get('confidence',0))*100:.1f}%；不读取训练模型")
        _fast_detail(symbol,"入场位置",f"开始检查：当前价格 + HH/HL/LH/LL + N字 + FVG + BOS/CHOCH + 流动性扫盘 + 等高等低流动性池 + Order Block + 位移 + 拒绝K + 压缩扩张 + 溢价/折价 + 回踩/突破")
    if _is_fast(pred):
        entry_check=pred.get("entry_price_confirmation") or {"decision":"WAIT","score":0.0,"reason":"FAST入场分析结果缺失"}
    else:
        entry_check=_entry_price_confirmation(symbol,side,str(cfg.get("timeframe") or "15m"))
    if entry_check.get("decision") == "WAIT":
        if not _is_fast(pred):
            score=float(entry_check.get("score",0.0) or 0.0)
            # 普通训练模型：很强、强、一般都正常仓位进入；只有明显差的<35%才等待。
            if score >= 0.35:
                entry_check["decision"]="ENTER"
                entry_check["entry_size_multiplier"]=1.00
                entry_check["entry_quality"]="强" if score >= 0.62 else "一般"
                entry_check["reason"]="方向已确认，入场位置达到可参与标准，正常仓位进入"
        if entry_check.get("decision") == "WAIT":
            if _is_fast(pred):
                _fast_detail(symbol,"入场位置",f"WAIT；综合评分={float(entry_check.get('score',0))*100:.1f}%；原因={entry_check.get('reason','位置一般')}","warning")
            _activity(f"实盘 {symbol}：入场价格确认 → 等待；评分={float(entry_check.get('score',0))*100:.1f}%；原因={entry_check.get('reason','位置一般')}")
            _activity(f"实盘 {symbol}：FINAL       → WAIT ENTRY PRICE")
            return
    if entry_check.get("decision") == "ENTER":
        if _is_fast(pred):
            factors=entry_check.get("factors") or {}
            _fast_detail(symbol,"入场位置",f"ENTER；评分={float(entry_check.get('score',0))*100:.1f}%；" + "；".join(_fmt_factor(k,v) for k,v in factors.items()))
        _activity(f"实盘 {symbol}：入场价格确认 → 通过；评分={float(entry_check.get('score',0))*100:.1f}%；当前价={float(entry_check.get('entry_price',0) or 0):.8g}")
    else:
        _activity(f"实盘 {symbol}：入场价格确认 → 关闭，恢复原有下单流程")
    health_advice=_strategy_health_advice(symbol,pred)
    review_mult=max(0.55,min(1.10,float(health_advice.get("size_multiplier",1.0) or 1.0)))
    if abs(review_mult-1.0)>0.001:
        _activity(f"实盘 {symbol}：策略复盘发现相同条件历史表现{health_advice.get('health','观察中')}，下一笔软性仓位系数={review_mult:.2f}；动作={health_advice.get('action','正常参与')}")
    with LOCK:
        if gate_enabled("duplicate_position") and symbol in STATE["positions"]:
            _activity(f"实盘 {symbol}：信号={pred.get('signal')} 但未开仓，原因=本系统已有该币持仓", "warning")
            _activity(f"实盘 {symbol}：FINAL       → NO ORDER")
            _activity(f"实盘 {symbol}：原因        → DUPLICATE POSITION", "warning")
            return
        _ss=_same_side_blocked(symbol,pred,cfg) if symbol not in STATE["positions"] else ""
        if _ss:
            _activity(f"实盘 {symbol}：信号={pred.get('signal')} 但未开仓，原因={_ss}", "warning")
            return
        if gate_enabled("max_positions") and len(STATE["positions"])>=cfg["max_positions"]:
            _activity(f"实盘 {symbol}：信号={pred.get('signal')} 但未开仓，原因=达到最大持仓数 {len(STATE['positions'])}/{cfg['max_positions']}", "warning")
            _activity(f"实盘 {symbol}：FINAL       → NO ORDER")
            _activity(f"实盘 {symbol}：原因        → MAX POSITIONS", "warning")
            return
        # Daily-loss / consecutive-loss protection is a hard capital-safety floor;
        # the UI risk_budget switch only controls the sizing budget, not these protections.
        rb_reason=_risk_block(cfg)
        if rb_reason:
            _activity(f"实盘 {symbol}：风控保护中，禁止开仓：{rb_reason}", "warning")
            _activity(f"实盘 {symbol}：FINAL       → NO ORDER")
            _activity(f"实盘 {symbol}：原因        → DAILY/CONSECUTIVE LOSS PROTECTION", "warning")
            return
        recent=[t for t in reversed(STATE["live_trades"]) if t.get("symbol")==symbol and t.get("action") in ("OPEN_FAILED","CLOSE")]
        if gate_enabled("cooldown") and recent and now-recent[0].get("time",0)<cfg["cooldown_minutes"]*60:
            remain=max(0.0,cfg["cooldown_minutes"]*60-(now-recent[0].get("time",0)))
            _activity(f"实盘 {symbol}：信号={pred.get('signal')} 但未开仓，原因=冷却中，剩余约{remain:.0f}秒", "warning")
            _activity(f"实盘 {symbol}：FINAL       → NO ORDER")
            _activity(f"实盘 {symbol}：原因        → COOLDOWN", "warning")
            return
        # maker 模式下，上一笔 post-only 未成交（撤单不追）后的短冷却，避免同一信号每分钟反复挂单
        if _fast_maker_enabled(pred):
            _mk_until=float((STATE.get("maker_skip_until") or {}).get(symbol) or 0)
            if now<_mk_until:
                _activity(f"实盘 {symbol}：信号={pred.get('signal')} 但上次 post-only 未成交，短冷却剩余约{int(_mk_until-now)}秒，跳过")
                return
    try:
        real=alpha_live.positions(); cs=config.trading.get_ccxt_symbol(symbol)
        if gate_enabled("duplicate_position") and any(p.get("symbol")==cs and float(p.get("contracts") or 0)>0 for p in real):
            with LOCK: STATE["risk_block"]="检测到该合约已有持仓，ALPHA-X 跳过开仓"
            _activity(f"实盘 {symbol}：发现交易所已有该币持仓，本系统不接管，跳过开仓", "warning")
            _activity(f"实盘 {symbol}：FINAL       → NO ORDER")
            _activity(f"实盘 {symbol}：原因        → EXCHANGE DUPLICATE POSITION", "warning")
            return
        try:
            equity,free=_live_account_snapshot(max_age=0)        # 下单前必须重新读取，保证可用余额是最新的
        except Exception as exc:
            # 只跳过这一个币的这次开仓，不中断整轮扫描（其余币的持仓管理照常进行）
            _activity(f"实盘 {symbol}：下单前读取实时权益失败，本轮跳过该币开仓：{exc}", "warning")
            return
        if equity<=0 or free<=0:
            _activity(f"实盘 {symbol}：OKX 权益/可用余额不足（权益={equity:.2f}，可用={free:.2f}），本轮不开仓", "warning")
            return
        recon=_reconciliation_check(real)
        audit=_audit_check()
        clock=_clock_check(cfg) if gate_enabled("clock") else {"ok":True,"disabled":True}
        h=health()
        rh=recovery_health()
        recovery_ok=bool(rh.get("healthy")) and bool(recon.get("ok"))
        if gate_enabled("reconciliation") and not recon.get("ok"):
            _activity(f"实盘 {symbol}：对账检查未通过：{';'.join(recon.get('problems',[]))}", "warning")
            _activity(f"实盘 {symbol}：FINAL       → NO ORDER")
            _activity(f"实盘 {symbol}：原因        → RECONCILIATION", "warning")
            return
        if gate_enabled("clock") and not clock.get("ok"):
            _activity(f"实盘 {symbol}：时钟检查未通过：{clock.get('error') or ('偏差='+str(clock.get('skew_ms'))+'ms')}", "warning")
            _activity(f"实盘 {symbol}：FINAL       → NO ORDER")
            _activity(f"实盘 {symbol}：原因        → CLOCK", "warning")
            return
        if gate_enabled("audit") and not audit.get("ok"):
            _activity(f"实盘 {symbol}：审计检查未通过：{audit.get('reason','未知')}", "warning")
            _activity(f"实盘 {symbol}：FINAL       → NO ORDER")
            _activity(f"实盘 {symbol}：原因        → AUDIT", "warning")
            return
        if (pred.get("fast_strategy") or {}).get("engine_version")=="v7" and pred.get("signal") in ("LONG","SHORT"):
            _v7_quote=okx_client.get_ticker(symbol) or {}
            if not _v7_entry_guard(symbol,pred,float(_v7_quote.get("last") or 0),time.time()): return
        if (pred.get("fast_strategy") or {}).get("engine_version")=="v6":
            _v6_quote=okx_client.get_ticker(symbol) or {}
            if not _v6_entry_guard(symbol,pred,float(_v6_quote.get("last") or 0),time.time()): return
        rb=risk_budget(equity,cfg["risk_pct"],float(pred["sl"]),float(pred.get("confidence",0)),float((pred.get("market_context") or {}).get("stress",0)),float(max(0.0,min(1.20,allocation_multiplier))),cfg["max_notional_pct"],cfg["leverage"],free)
        requested=float(rb["notional_usdt"]) if gate_enabled("risk_budget") else max(0.0, min(equity*float(cfg.get("max_notional_pct",1.0)), free*float(cfg.get("leverage",1))))
        if _is_fast(pred):
            fast_mult=float(pred.get("fast_entry_size_multiplier",1.0) or 1.0)
            _fast_detail(symbol,"仓位逻辑",f"FAST策略层系数={fast_mult:.2f}；组合层不再重复使用信心/压力；最终由风险预算统一计算")
        if pred.get("signal_tier")=="TRIAL": requested*=0.35
        # 交易复盘只影响本次风险预算的软系数；硬风控、方向和执行规则保持原样。
        requested*=review_mult
        # 方案二分批建仓：每一批 = 单笔风险 × 1/3 ÷ 止损距离（与加仓同一算法），不受“风险预算”开关与信心系数影响。
        _fs2=pred.get("fast_strategy") or {}
        if _fs2.get("v7_scheme2"):
            requested=_scheme2_leg_notional(equity,free,float(pred["sl"]),cfg)
        elif _fs2.get("v7_scheme3"):
            # 方案三：每笔固定 = 权益 10%，不受风险预算/信心/复盘系数影响（与回测一致）。
            requested=_scheme3_notional(equity,free,cfg)
        else:
            requested*=float(_fs2.get("v7_risk_scale") or 1.0)
        if requested<=0:
            _activity(f"实盘 {symbol}：风险预算计算后下单金额为0，跳过")
            _activity(f"实盘 {symbol}：FINAL       → NO ORDER")
            _activity(f"实盘 {symbol}：原因        → RISK BUDGET 计算结果为0", "warning")
            return
        # Pull a bounded completed-bar window for data quality, impact and capacity gates.
        candles=okx_client.get_ohlcv(symbol=symbol,timeframe=cfg.get("timeframe","15m"),limit=300)
        ticker=okx_client.get_ticker(symbol) or {}
        pipeline=pretrade_pipeline(symbol=symbol,side=side,candles=candles,ticker=ticker,equity=equity,free=free,
                                   requested_notional=requested,stop_pct=float(pred["sl"]),confidence=float(pred.get("confidence",0)),
                                   stress=float((pred.get("market_context") or {}).get("stress",0)),cfg={**cfg,"signal_tier":pred.get("signal_tier","NORMAL"),"gate_switches":dict(STATE.get("gate_switches") or DEFAULT_GATE_SWITCHES),"clock_ok":bool(clock.get("ok")),"audit_ok":bool(audit.get("ok")),"ha_ok":bool(INTEGRATION_FENCE and authorize_execution(INTEGRATION_FENCE))},live=True,
                                   health_ok=recovery_ok,reconciliation_ok=bool(recon.get("ok")),model_ok=bool(pred.get("model_ready")),
                                   )
        pipeline["clock"] = clock
        pipeline["audit"] = audit
        pipeline["reconciliation"] = recon
        pipeline["recovery"] = {"ok":recovery_ok,"health":rh,"system_health":h}
        ledger_record("PRETRADE_PIPELINE",symbol,pipeline)
        if _is_fast(pred):
            _fast_detail(symbol,"下单前检查",f"行情质量={'通过' if bool((pipeline.get('quality') or {}).get('ok',True)) else '不通过'}；流动性={'通过' if bool((pipeline.get('liquidity') or {}).get('ok',True)) else '不通过'}；容量={'通过' if bool((pipeline.get('capacity') or {}).get('ok',True)) else '不通过'}；冲击成本={'通过' if bool((pipeline.get('impact') or {}).get('impact_ok',True)) else '不通过'}")
            _fast_detail(symbol,"下单前检查",f"对账={'通过' if bool(recon.get('ok')) else '不通过'}；审计={'通过' if bool(audit.get('ok')) else '不通过'}；时钟={'通过' if bool(clock.get('ok')) else '不通过'}；恢复={'通过' if recovery_ok else '不通过'}")
            _fast_detail(symbol,"仓位计算",f"计划金额={float(pipeline.get('planned_notional') or 0):.2f} USDT；杠杆={cfg.get('leverage')}x；风险预算={'启用' if gate_enabled('risk_budget') else '关闭'}")
        _black_window_pipeline_chain(symbol, pred, pipeline)
        if not pipeline.get("ready"):
            reason=pipeline.get("reason","INTEGRATED_GATE_BLOCK")
            gate=pipeline.get("gate") or {}
            checks=gate.get("checks") or {}
            failed=[k for k,v in checks.items() if not v]
            details=[]
            if failed: details.append("gate失败="+",".join(failed))
            q=pipeline.get("quality") or {}
            if q and not q.get("ok",True): details.append("行情质量="+str(q.get("reason") or q.get("errors") or "未通过"))
            if pipeline.get("planned_notional") is not None: details.append(f"计划金额={float(pipeline.get('planned_notional') or 0):.2f}U")
            rs=pipeline.get("risk_sizing") or {}
            if rs.get("cvar_mode") and rs.get("multiplier") is not None and float(rs.get("multiplier") or 1) < 1.0:
                details.append(f"CVaR={float(rs.get('cvar',0) or 0)*100:.2f}%→风险系数{float(rs.get('multiplier',1) or 1):.2f}")
            detail="；".join(details) or reason
            with LOCK: STATE["risk_block"]=detail
            _activity(f"实盘 {symbol}：信号={pred.get('signal')} 置信度={float(pred.get('confidence',0))*100:.1f}% → 未开仓，原因={detail}", "warning")
            return
        notional=float(pipeline["planned_notional"])
        if not _is_fast(pred):
            # 入场价格确认不改变仓位：很强/强/一般均按原有风险系统计算的正常仓位进入。
            pass
        if _is_fast(pred):
            # FAST裸K的很强/强/一般均按正常仓位进入；最终金额仍只允许由原有安全风控链调整。
            fast_mult=1.00
        if notional<=0:
            _activity(f"实盘 {symbol}：执行检查通过但最终下单金额=0，未开仓；请检查组合权重/容量/执行金额", "warning")
            return
        # 最小下单数量检查：防止金额太小导致下单失败（如AERO最小1张，3.91U不够买1张）
        try:
            cs_min=config.trading.get_ccxt_symbol(symbol)
            mkt_min=alpha_live.market(cs_min)
            ct_min=alpha_live._safe_num(mkt_min.get("contractSize"),0.0) or alpha_live._safe_num((mkt_min.get("info") or {}).get("ctVal"),0.0) or 1.0
            min_amt_min=alpha_live._safe_num((mkt_min.get("limits",{}).get("amount",{}) or {}).get("min"),0.0)
            last_px=float((ticker or {}).get("last") or 0)
            if last_px>0 and min_amt_min>0:
                min_notional=min_amt_min*ct_min*last_px*1.05  # 加5%余量防止价格波动
                if notional<min_notional:
                    _activity(f"实盘 {symbol}：计划金额{notional:.2f}U小于最小下单金额{min_notional:.2f}U（最小{min_amt_min}张），未开仓；请调大单笔风险或换高价币种", "warning")
                    with LOCK: STATE["risk_block"]=f"下单金额太小，最小需要{min_notional:.2f}U（当前{notional:.2f}U）"
                    return
        except Exception as min_exc:
            logger.warning(f"[ALPHA-X] 最小下单数量检查异常，跳过: {min_exc}")
        _pre_v6_stop=float(pred.get("sl") or 0)
        if (pred.get("fast_strategy") or {}).get("engine_version")=="v7" and pred.get("signal") in ("LONG","SHORT"):
            if not _v7_entry_guard(symbol,pred,float((ticker or {}).get("last") or 0),time.time()): return
            notional*=min(1.0,_pre_v6_stop/max(float(pred["sl"]),1e-12))
            _v7_claim_signal(symbol,pred)
        else:
            if not _v6_entry_guard(symbol,pred,float((ticker or {}).get("last") or 0),time.time()): return
            if (pred.get("fast_strategy") or {}).get("engine_version")=="v6":
                notional*=min(1.0,_pre_v6_stop/max(float(pred["sl"]),1e-12))
            _v6_claim_signal(symbol,pred)
        if not STATE["running"]: return
        intent_key=f"{symbol}|{side}|{int(now//60)}"
        clid=ops_client_id(symbol,side,intent_key=intent_key)
        intent_id="intent:"+clid
        from alpha_ops import upsert_intent
        upsert_intent(intent_id,{"symbol":symbol,"side":side,"notional":notional,"client_order_id":clid,"signal_ts":now,"tp":float(pred["tp"]),"sl":float(pred["sl"]),"pipeline":pipeline})
        ops_record(intent_id,"NEW",{"symbol":symbol,"side":side,"notional":notional,"client_order_id":clid,"pipeline_ready":True})
        _s3=bool((pred.get("fast_strategy") or {}).get("v7_scheme3"))
        use_maker=_fast_maker_enabled(pred) or _s3        # 方案三固定：先挂限价(maker)省手续费，没成交再市价补上
        _v6_protection={}
        _fs=pred.get('fast_strategy') or {}
        if _fs.get('v7_scheme3'):
            _v6_protection={}   # 方案三：止盈/止损按下单瞬间报价的固定比例（净1%/25%）挂单，不做盈亏比检查
        elif _fs.get('engine_version')=='v7':
            _v6_protection={'protection':dict(tp=_fs['tp_price'],sl=_fs['sl_price'],reference=_fs['reference_price'],cost=_fs['estimated_round_cost'],min_net_rr=_fs.get('min_net_rr',1.1),max_chase_r=_fs.get('max_chase_r',.5),min_target_cost=_fs.get('min_target_cost',2.5))}
        elif _fs.get('engine_version')=='v6':
            _v6_protection={'protection':dict(tp=_fs['tp_price'],sl=_fs['sl_price'],reference=_fs['reference_price'],cost=_fs['estimated_round_cost'],min_net_rr=_fs.get('min_net_rr',1.1),max_chase_r=_fs.get('max_chase_r',.25),min_target_cost=_fs.get('min_target_cost',2.5))}
        if _is_fast(pred):
            _fast_detail(symbol,"准备下单",f"所有下单前检查通过；方向={'做多' if side=='long' else '做空'}；计划金额={notional:.2f} USDT；准备向OKX发送{'post-only限价单(maker)' if use_maker else '市价单(taker)'}")
        _activity(f"实盘 {symbol}：开仓链路通过 → 信号={pred.get('signal')} 置信度={float(pred.get('confidence',0))*100:.1f}% → 持仓/冷却/余额/执行检查均通过 → 准备开仓 {side}，计划金额={notional:.2f} USDT")
        try:
            if use_maker:
                _mp=_maker_params(); _mode=_fast_entry_mode()
                _ref=float((ticker or {}).get("last") or 0)
                with LOCK:
                    ms=STATE.setdefault("maker_stats",{"signals":0,"filled":0,"no_fill":0,"partial":0,"market_fallback":0,"by_status":{}})
                    ms["signals"]=int(ms.get("signals",0))+1
                r=alpha_live.open_maker(symbol,side,notional,float(pred["tp"]),float(pred["sl"]),int(cfg["leverage"]),
                                        client_order_id=clid,ttl=float(_mp["ttl"]),max_reprice=int(_mp["reprice"]),
                                        improve_bps=float(_mp["improve_bps"]),chase_bps=float(_mp["chase_bps"]),
                                        fallback_to_market=(_mode=="maker_market" or _s3),ref_price=_ref,
                                        **({"fallback_on_runaway":True} if _s3 else {}),**_v6_protection)
                if float(r.get("filled") or 0)<=0:
                    mstatus=str(r.get("maker_status") or "no_fill")
                    with LOCK:
                        ms=STATE.setdefault("maker_stats",{"signals":0,"filled":0,"no_fill":0,"partial":0,"market_fallback":0,"by_status":{}})
                        ms["no_fill"]=int(ms.get("no_fill",0))+1
                        bs=ms.setdefault("by_status",{}); bs[mstatus]=int(bs.get(mstatus,0))+1
                        STATE.setdefault("maker_skip_until",{})[symbol]=now+int(_mp["skip_seconds"])
                    try: ops_record(clid,"MAKER_NO_FILL",{"symbol":symbol,"side":side,"status":mstatus})
                    except Exception: pass
                    _activity(f"实盘 {symbol}：post-only 限价单未成交（{mstatus}），已撤单、不追价、不付 taker，{int(_mp['skip_seconds'])}秒内不再挂同币；当前无持仓、无风险","info")
                    if _is_fast(pred): _fast_detail(symbol,"挂单结果",f"未成交（{mstatus}），已撤单；不追价、不付taker，等待下一次极值信号")
                    _persist()
                    return
                with LOCK:
                    ms=STATE.setdefault("maker_stats",{"signals":0,"filled":0,"no_fill":0,"partial":0,"market_fallback":0,"by_status":{}})
                    if r.get("maker"):
                        ms["filled"]=int(ms.get("filled",0))+1
                        if r.get("partial_entry"): ms["partial"]=int(ms.get("partial",0))+1
                    elif _mode=="maker_market":
                        ms["market_fallback"]=int(ms.get("market_fallback",0))+1
                if _is_fast(pred):
                    _fast_detail(symbol,"挂单结果",f"maker 已成交 {float(r.get('filled') or 0):.8g} 张 @ {float(r.get('average') or 0):.8g}（部分成交={bool(r.get('partial_entry'))}）")
            else:
                r=alpha_live.open(symbol,side,notional,float(pred["tp"]),float(pred["sl"]),int(cfg["leverage"]),client_order_id=clid,**_v6_protection)
        except Exception as submit_exc:
            err_text=str(submit_exc)
            protection_failure=("TP/SL" in err_text or "保护" in err_text or "强制平仓未完成" in err_text)
            existing=None
            if protection_failure:
                # Never treat a filled entry order as a successful retry when the executor
                # has already rejected/closed it because native protection was not verified.
                _block_protection_failure(symbol, side, err_text)
                try:
                    real_now=alpha_live.positions()
                    cs_now=config.trading.get_ccxt_symbol(symbol)
                    # net_mode下只按symbol过滤，long_short_mode才匹配side
                    try: mode_now=alpha_live.pos_mode()
                    except Exception: mode_now="long_short_mode"
                    if mode_now=="long_short_mode":
                        still_open=any(p.get("symbol")==cs_now and p.get("side")==side and float(p.get("contracts") or 0)>0 for p in real_now)
                    else:
                        still_open=any(p.get("symbol")==cs_now and float(p.get("contracts") or 0)>0 for p in real_now)
                    if still_open:
                        _managed_close(symbol,side)
                except Exception as close_exc:
                    ops_record(intent_id,"ERROR",{"error":err_text,"cleanup_error":str(close_exc)})
                    raise RuntimeError(f"TP/SL保护失败且残仓恢复未完成: {close_exc}") from submit_exc
                ops_record(intent_id,"ERROR",{"error":err_text,"protection_quarantine":True})
                raise
            try: existing=alpha_live.order_by_client_id(symbol,clid)
            except Exception: pass
            if existing and (float(existing.get("accFillSz") or 0)>0 or str(existing.get("state"))=="filled"):
                r=alpha_live.recover_filled_entry(symbol,side,clid)
                ledger_record("LIVE_ORDER_RECOVERED",symbol,r)
            else:
                ops_record(intent_id,"ERROR",{"error":err_text,"existing":existing})
                raise
        ops_record(r["order_id"],"SUBMITTED",{"symbol":symbol,"side":side,"notional":notional,"client_order_id":clid})
        if _is_fast(pred):
            _fast_detail(symbol,"OKX下单",f"订单已提交；订单号={r.get('order_id') or '未知'}；客户端订单号={clid}")
            _fast_detail(symbol,"成交确认",f"实际成交数量={float(r.get('filled') or 0):.8g}；实际成交均价={float(r.get('average') or 0):.8g}")
        ops_record(r["order_id"],"FILLED",{"filled":r.get("filled"),"average":r.get("average"),"client_order_id":clid})
        expected=[r.get("tp_attach_clordid"),r.get("sl_attach_clordid")]
        protection=alpha_live.protection_status(symbol,side,expected_ids=expected,wait_timeout=5.0)
        if not protection.get("verified"):
            ops_record(r["order_id"],"ERROR",{"reason":"PROTECTION_UNVERIFIED","detail":protection})
            try:
                flat_result=_managed_close(symbol,side)
                if not flat_result.get("flat_confirmed"):
                    raise RuntimeError("保护性平仓未确认仓位归零")
            except Exception as close_exc:
                ops_open_breaker(f"PROTECTION_CLOSE_FAILED:{symbol}")
                raise RuntimeError(f"OKX 原生 TP/SL 未验证，保护性平仓失败或未确认归零: {close_exc}")
            raise RuntimeError(f"OKX 原生 TP/SL 未验证，已保护性平仓并确认仓位归零: {protection.get('error') or protection.get('count',0)}")
        ops_record(r["order_id"],"PROTECTED",protection)
        if _is_fast(pred):
            _fast_detail(symbol,"TP/SL保护",f"OKX原生保护已验证；TP={float(r.get('tp') or 0):.8g}；SL={float(r.get('sl') or 0):.8g}；保护数量={int(protection.get('count',0))}")
            _fast_detail(symbol,"进入持仓管理",f"持仓已建立；实际数量={float(r.get('filled') or 0):.8g}；实际均价={float(r.get('average') or 0):.8g}")
        _activity(f"实盘 {symbol}：订单成交，TP/SL保护已验证，订单={r.get('order_id')}")
        r["protection_verified"]=True; r["protection_count"]=int(protection.get("count",0)); r["pretrade_pipeline"]=pipeline
        with LOCK:
            STATE["positions"][symbol]={"side":side,"entry":r["average"],"order_id":r["order_id"],"client_order_id":r.get("client_order_id"),"tp":r["tp"],"sl":r["sl"],"tp_attach_clordid":r.get("tp_attach_clordid"),"sl_attach_clordid":r.get("sl_attach_clordid"),"base_tp_pct":float(pred.get("base_tp",pred["tp"])),"base_sl_pct":float(pred.get("base_sl",pred["sl"])),"dynamic_tp_pct":float(pred["tp"]),"dynamic_sl_pct":float(pred["sl"]),"dynamic_reason":str((pred.get("dynamic_tp_sl") or {}).get("reason", "")),"exit_mode":"NORMAL","native_trail_id":"","native_trail_armed":False,"native_partial_armed":False,"partial_tp_order_ids":[],"trail_active":False,"trail_anchor_price":r["average"],"trail_highest_price":r["average"],"trail_lowest_price":r["average"],"trail_sl":r["sl"],"last_trail_update":0.0,"original_tp_price":r["tp"],"original_sl_price":r["sl"],"transition_started_at":0.0,"transition_error":"","last_dynamic_update":time.time(),"opened_at":now,"notional":r["notional_usdt"],"filled":float(r.get("filled") or 0),"partial_tp_base_qty":0.0,"partial_tp_steps":[],"partial_tp_filled_by_step":{},"partial_tp_last_fill":{},"partial_tp_sl_stage":"NONE","live":True,"max_seconds":_position_max_seconds(pred),"strategy_mode":pred.get("strategy_mode","SHORT"),"fast_engine":str((pred.get("fast_strategy") or {}).get("v4_engine") or ""),**_fast_position_meta(pred),"strategy_label":pred.get("strategy_label","短线"),"pipeline":pipeline}
            _fv=STATE['positions'][symbol].get('fast_version')
            if _fv in ('v6','v7'):
                _p=STATE['positions'][symbol]; _d=1 if side=='long' else -1; _ep=float(r['average'])
                _p.update(base_tp_pct=_d*(float(r['tp'])-_ep)/_ep,base_sl_pct=_d*(_ep-float(r['sl']))/_ep)
                if _fv=='v6': _p['v6_trigger_price_type']='last' 
            STATE["live_trades"].append({"time":now,"symbol":symbol,"side":side,"action":"OPEN","order_id":r["order_id"],"average":r["average"],"filled":r["filled"],"tp":r["tp"],"sl":r["sl"],"notional":r["notional_usdt"],"pipeline":pipeline})
            STATE["history"]=STATE["live_trades"][-300:]; STATE["live_error"]=""
        try: ledger_record("LIVE_OPEN",symbol,r)
        except Exception: pass
        _persist()
        _arm_native_exits_after_open(symbol,cfg)
    except Exception as exc:
        # Never replace structure prices with percentages or adopt unrelated protection.
        recovered_position=False; confirmed_flat=False
        recovery_clid=getattr(exc,"client_order_id",None) or locals().get("clid")
        try:
            real=alpha_live.positions();cs=config.trading.get_ccxt_symbol(symbol)
            rp=next((x for x in real if x.get("symbol")==cs and x.get("side")==side and float(x.get("contracts") or 0)>0),None)
            confirmed_flat=rp is None
            if rp and recovery_clid:
                try:
                    rec=alpha_live.recover_filled_entry(symbol,side,recovery_clid)
                    pending=False
                except Exception as recovery_error:
                    # Actual position exists but protection/parent state is uncertain.
                    # Keep it registered for protective close; never mark it protected.
                    rec=dict(average=float(rp.get("entryPrice") or 0),filled=float(rp["contracts"]),notional_usdt=float(rp.get("entryPrice") or 0)*float(rp["contracts"])*float(rp.get("contract_size") or 0),tp=0.,sl=0.,order_id=recovery_clid)
                    pending=True
                    ops_open_breaker(f"ENTRY_RECOVERY_UNVERIFIED:{symbol}")
                entry=float(rec["average"]);d=1 if side=="long" else -1
                recovered={**_fast_position_meta(pred),"side":side,"entry":entry,"order_id":rec.get("order_id"),"client_order_id":recovery_clid,"tp":rec["tp"],"sl":rec["sl"],"tp_attach_clordid":rec.get("tp_attach_clordid"),"sl_attach_clordid":rec.get("sl_attach_clordid"),"base_tp_pct":d*(rec["tp"]-entry)/entry if entry and not pending else 0.,"base_sl_pct":d*(entry-rec["sl"])/entry if entry and not pending else 0.,"original_tp_price":rec["tp"],"original_sl_price":rec["sl"],"trail_sl":rec["sl"],"notional":rec["notional_usdt"],"filled":rec["filled"],"opened_at":now,"live":True,"recovered":True,"v6_recovery_pending":pending,"exit_mode":"NORMAL","native_trail_id":"","native_trail_armed":False,"native_partial_armed":False,"partial_tp_order_ids":[],"strategy_mode":pred.get("strategy_mode","FAST"),"strategy_label":pred.get("strategy_label","异常恢复"),"pipeline":pipeline}
                with LOCK:
                    STATE["positions"][symbol]=recovered
                    STATE["live_trades"].append({"time":now,"symbol":symbol,"side":side,"action":"OPEN_QUARANTINED" if pending else "OPEN_RECOVERED","average":entry,"filled":rec["filled"],"tp":rec["tp"],"sl":rec["sl"],"error":str(exc)[:200]})
                    STATE["history"]=STATE["live_trades"][-300:]
                    STATE["live_error"]=f"{symbol}: "+("保护未验证，登记待退出仓位" if pending else "实际成交与原生保护已恢复")
                recovered_position=True
                _activity(STATE["live_error"],"warning")
            elif rp:
                ops_open_breaker(f"ENTRY_STATE_UNKNOWN:{symbol}")
        except Exception as recover_exc:
            ops_open_breaker(f"ENTRY_QUERY_FAILED:{symbol}")
            logger.exception(f"[ALPHA-X LIVE] {symbol} 开仓异常恢复失败: {recover_exc}")
        if not recovered_position:
            message="已确认当前无仓位，原订单仍需对账" if confirmed_flat else "持仓状态未确认，已停止新开仓并等待对账"
            # An empty position snapshot does not prove an ambiguous pending order was canceled.
            if recovery_clid: ops_open_breaker(f"ENTRY_ORDER_RECONCILE:{symbol}")
            with LOCK:
                STATE["live_error"]=f"{symbol}: {message}: {exc}"
                STATE["last_open_error"]={"symbol":symbol,"error":str(exc)[:300],"trace_tail":traceback.format_exc()[-700:],"ts":time.time()}
                STATE["last_error_ts"]=time.time()
                STATE["live_trades"].append({"time":now,"symbol":symbol,"side":side,"action":"OPEN_FAILED" if confirmed_flat else "OPEN_UNKNOWN","error":str(exc)[:200]})
                STATE["history"]=STATE["live_trades"][-300:]
            _activity(STATE["live_error"],"error")
        _persist()


def _recover_live_state(cfg):
    """After restart, recover only ALPHA-X tagged positions; never adopt manual positions."""
    try:
        real=alpha_live.positions()
        with LOCK: saved=dict(STATE["positions"])
        known_ccxt={config.trading.get_ccxt_symbol(s):s for s in saved}
        for rp in real:
            cs=rp.get("symbol");
            if cs in known_ccxt: continue
            # Unknown positions remain untouched by design.
            continue
        return True
    except Exception as exc:
        with LOCK: STATE["live_error"]=f"实盘恢复检查失败: {exc}"
        return False

def _v7_reconcile_partial(symbol,p):
    if not p.get('native_partial_armed'): return
    specs=p.get('partial_tp_native_specs') or []
    ids=p.get('partial_tp_order_ids') or []
    for spec,cid in zip(specs,ids):spec.setdefault('client_id',cid)
    pending=[x for x in specs if not x.get('done') and x.get('client_id')]
    rows=alpha_live._protection_rows(symbol,p['side'],[x['client_id'] for x in pending])
    by={alpha_live._algo_client_id(x):x for x in rows}
    for spec in pending:
        row=by.get(spec['client_id'])
        if not row: continue  # unknown state is not a fill
        state=str(row.get('state') or '')
        if state=='effective':
            # Effective algo means triggered, NOT necessarily filled. Confirm child order.
            children=row.get('ordIdList') or ([row['ordId']] if row.get('ordId') else [])
            if isinstance(children,str):children=[children]
            filled=0.
            for oid in children:
                detail=alpha_live.wait_order_terminal(symbol,str(oid),timeout=1.0)
                if str(detail.get('status'))=='closed':filled+=float(detail.get('filled') or 0)
            if filled+1e-10>=float(spec['qty']):
                with LOCK:
                    spec['done']=True
                    steps=p.setdefault('partial_tp_steps',[])
                    if spec['name'] not in steps:steps.append(spec['name'])
                _persist()
        elif state in ('canceled','order_failed'):
            # Do not silently resubmit: order failure needs an observed remaining quantity.
            _activity(f"V7 {symbol}：{spec['name']} 条件单失效，保留固定SL，等待保护恢复",'warning')

def _reconcile_native_partial(symbol: str, p: dict, current_contracts: float):
    if p.get('fast_version')=='v7':
        _v7_reconcile_partial(symbol,p)
        expected=[x['client_id'] for x in p.get('partial_tp_native_specs',[]) if not x.get('done') and x.get('client_id')]
        expected.append(p.get('sl_attach_clordid'))
        if not alpha_live.protection_status_set(symbol,p['side'],expected,wait_timeout=3.0).get('verified'):
            raise RuntimeError('V7分批保护状态不完整；不得按列表位置重新发单')
        return
    side=str(p.get("side") or "")
    ids=list(p.get("partial_tp_order_ids") or [])
    specs={x.get("name"):x for x in (p.get("partial_tp_native_specs") or [])}
    # specs do not carry IDs; map in original TP1/TP2/FINAL order.
    spec_ids=[("TP1",ids[0] if len(ids)>0 else ""),("TP2",ids[1] if len(ids)>1 else ""),("FINAL",ids[2] if len(ids)>2 else "")]
    sl_id=str(p.get("sl_attach_clordid") or "")
    expected=[x for _,x in spec_ids if x]+([sl_id] if sl_id else [])
    rows=alpha_live._protection_rows(symbol,side,expected)
    active={alpha_live._algo_client_id(x) for x in rows if alpha_live._algo_is_active(x)}
    missing=[(name,cid) for name,cid in spec_ids if cid and cid not in active]
    base=float(p.get("partial_tp_base_qty") or p.get("filled") or current_contracts)
    reduction=max(0.0,base-float(current_contracts))
    cs=config.trading.get_ccxt_symbol(symbol); step=alpha_live._amount_step(cs)
    missing_qty=sum(float((specs.get(name) or {}).get("qty") or 0) for name,_ in missing)
    restored=[]
    if missing:
        if reduction<=step:
            for name,cid in missing:
                spec=specs.get(name) or {}
                r=alpha_live.place_native_tp(symbol,side,float(spec.get("qty") or 0),float(spec.get("price") or 0))
                restored.append(r.get("client_id"))
        elif abs(missing_qty-reduction)>step:
            raise RuntimeError("原生分批止盈订单缺失数量与实际减仓不一致")
    tp_ids=[]
    for name,cid in spec_ids:
        use_id=(restored.pop(0) if name in [m[0] for m in missing] and restored else cid)
        if use_id and (use_id in active or use_id in restored):
            tp_ids.append(use_id)
    verify_ids=tp_ids+([sl_id] if sl_id else [])
    status=alpha_live.protection_status_set(symbol,side,verify_ids,wait_timeout=3.0)
    if not status.get("verified"):
        raise RuntimeError("原生分批止盈恢复后保护单未全部验证")
    with LOCK:
        livep=STATE["positions"].get(symbol)
        if livep:
            livep["partial_tp_order_ids"]=tp_ids
            livep["tp_attach_clordid"]=tp_ids[-1] if tp_ids else livep.get("tp_attach_clordid")
    _persist()


def _reconcile_live(cfg):
    """Crash recovery: reconcile local ALPHA-X intents with real positions and pending orders."""
    try:
        real=alpha_live.positions(); by={p.get("symbol"):p for p in real if float(p.get("contracts") or 0)>0}
        with LOCK: saved=dict(STATE["positions"])
        for s,p in saved.items():
            cs=config.trading.get_ccxt_symbol(s); rp=by.get(cs)
            if not rp:
                _activity(f"实盘 {s}：交易所已无本系统登记仓位，正在完成成交归因")
                # 交给连续确认与成交对账流程，重启不能直接丢掉仓位记录。
                continue
            if rp.get("side") and rp.get("side")!=p.get("side"):
                _cleanup_flat_position_algos(s,p)
                with LOCK: STATE["positions"].pop(s,None)
                continue
            try:
                side_r=str(p.get("side") or "")
                mode_exit=str(p.get("exit_mode") or "NORMAL")
                if mode_exit in ("TRANSITION","RECOVERY"):
                    # Crash-safe rule: never resume halfway through a handoff. Restore NORMAL TP+SL first.
                    rb=alpha_live.restore_tp_sl(s,side_r,float(p.get("original_tp_price") or p.get("tp") or 0),float(p.get("original_sl_price") or p.get("sl") or 0),p.get("tp_attach_clordid"),p.get("sl_attach_clordid"))
                    if rb.get("verified"):
                        # 撤掉可能叠加的移动单，固定SL已由restore确认兜底。
                        if p.get("native_trail_armed") and p.get("native_trail_id"):
                            try: _cancel_active_algo(s,side_r,str(p.get("native_trail_id")))
                            except Exception: pass
                        with LOCK:
                            lp=STATE["positions"].get(s)
                            if lp:
                                lp["tp_attach_clordid"]=rb.get("tp_attach_clordid") or lp.get("tp_attach_clordid")
                                lp["sl_attach_clordid"]=rb.get("sl_attach_clordid") or lp.get("sl_attach_clordid")
                                lp["tp"]=float(p.get("original_tp_price") or p.get("tp") or 0)
                                lp["sl"]=float(p.get("original_sl_price") or p.get("sl") or 0)
                                lp["native_trail_id"]=""; lp["native_trail_armed"]=False
                                lp["trail_active"]=False; lp["exit_mode"]="NORMAL"; lp["transition_error"]=""
                        _activity(f"实盘 {s}：检测到切换中断，已恢复原TP+SL并验证，回到NORMAL", "warning")
                    else:
                        raise RuntimeError(rb.get("error") or "切换中断后的原保护恢复失败")
                else:
                    # NORMAL / TREND_TRAIL 同构对账：
                    # (1) TP：分批armed则恢复分批(内部已含SL验证)，否则验证单一TP+固定SL这一对。
                    if p.get("s2_pos_algo"):
                        # 方案二整仓保护单：一张单同时含止损和兜底止盈
                        if not alpha_live.protection_status_set(s,side_r,[p["s2_pos_algo"]],wait_timeout=5.0).get("verified"):
                            raise RuntimeError("方案二整仓保护单未验证")
                    elif p.get("native_partial_armed"):
                        _reconcile_native_partial(s,p,float(rp.get("contracts") or 0))
                    else:
                        prot=alpha_live.protection_status(s,side_r,expected_ids=[p.get("tp_attach_clordid"),p.get("sl_attach_clordid")],wait_timeout=5.0)
                        if not prot.get("verified"):
                            raise RuntimeError("TP+固定SL未验证")
                    # (2) 叠加移动单：armed则检查是否仍active；若已失效(被撤/触发异常)，固定SL
                    #     已在上面验证兜底，只需清移动标记并回到NORMAL，绝不因此裸奔或误熔断。
                    if p.get("native_trail_armed"):
                        tid=str(p.get("native_trail_id") or ""); trail_active=False
                        if tid:
                            trows=alpha_live._protection_rows(s,side_r,[tid])
                            trail_active=any(alpha_live._algo_client_id(x)==tid and alpha_live._algo_is_active(x) for x in trows)
                        if not trail_active:
                            with LOCK:
                                lp=STATE["positions"].get(s)
                                if lp:
                                    lp["native_trail_armed"]=False; lp["native_trail_id"]=""; lp["trail_active"]=False
                                    if lp.get("exit_mode")=="TREND_TRAIL": lp["exit_mode"]="NORMAL"
                            _activity(f"实盘 {s}：重启发现移动单已失效，固定SL兜底，已清除移动标记", "warning")
            except Exception as exc:
                ops_open_breaker(f"RECOVERY_PROTECTION_CHECK_FAILED:{s}")
                with LOCK: STATE["live_error"]=f"{s} 恢复保护检查失败: {exc}"
        # Never adopt unknown positions. Orphaned ALPHAX entry orders are safe to cancel.
        try:
            pending=alpha_live.pending_orders()
            local_clids={str(p.get("client_order_id")) for p in saved.values() if p.get("client_order_id")}
            for o in pending:
                clid=str(o.get("clOrdId") or "")
                if str(o.get("tag") or "") == "ALPHAX" and clid.startswith("AX") and clid not in local_clids:
                    inst=o.get("instId") or ""; sym=inst.replace("-SWAP","").replace("-","/")+":USDT" if inst.endswith("-SWAP") else None
                    if sym:
                        try: alpha_live.cancel(sym,o.get("ordId")); ledger_record("ORPHAN_ORDER_CANCELED",sym,{"ordId":o.get("ordId"),"clOrdId":clid})
                        except Exception as exc: ledger_record("ORPHAN_ORDER_CANCEL_FAILED",sym,{"ordId":o.get("ordId"),"error":str(exc)})
        except Exception as exc:
            logger.warning(f"[ALPHA-X] pending order recovery failed: {exc}")
        _persist(); return True
    except Exception as exc:
        with LOCK: STATE["live_error"]=f"重启持仓同步失败: {exc}"
        return False


def _trail_direction_score(p: dict, pred: dict, last: float) -> dict:
    """Opportunity-preserving trend confirmation used only near the original TP."""
    side=str(p.get("side") or "")
    entry=float(p.get("entry") or 0); tp=float(p.get("tp") or 0)
    if entry<=0 or tp<=0 or last<=0 or side not in ("long","short"):
        return {"aligned":False,"score":0.0,"progress":0.0,"reason":"BAD_PRICE_STATE"}
    total=abs(tp-entry); favorable=(last-entry) if side=="long" else (entry-last)
    progress=max(0.0,min(1.25,favorable/(total+1e-12)))
    trend=float(pred.get("trend_score") or (pred.get("market_context") or {}).get("trend_score") or 0.0)
    strength=float(pred.get("trend_strength") or (pred.get("market_context") or {}).get("trend_strength") or 0.0)
    volreg=float(pred.get("vol_regime") or (pred.get("market_context") or {}).get("vol_regime") or 1.0)
    regime=str(pred.get("regime") or (pred.get("market_context") or {}).get("regime") or "NORMAL").upper()
    aligned=(trend>=0 if side=="long" else trend<=0)
    score=0.0
    score += 0.45 if aligned else 0.0
    score += 0.30 if abs(trend)>=0.012 else (0.15 if abs(trend)>=0.008 else 0.0)
    score += 0.20 if strength>=1.15 else (0.10 if strength>=0.85 else 0.0)
    score += 0.15 if regime in ("TREND","EXTREME") else 0.0
    score += 0.10 if volreg>=1.15 else 0.0
    score=min(1.0,score)
    return {"aligned":aligned,"score":score,"progress":progress,"trend":trend,"strength":strength,"vol_regime":volreg,"regime":regime}


def _adaptive_atr_pct(pred: dict) -> dict:
    """Return normalized ATRs and a bounded volatility-expansion factor.

    atr5/14/50 are already percentages of price. Never divide them by the
    current price again. The short ATR catches fresh bursts while ATR50
    provides a slower volatility baseline.
    """
    mc=pred.get("market_context") or {}
    atr5=max(0.0,float(pred.get("atr5") or mc.get("atr5") or 0.0))
    atr14=max(0.0,float(pred.get("atr14") or mc.get("atr14") or 0.0))
    atr50=max(0.0,float(pred.get("atr50") or mc.get("atr50") or 0.0))
    if atr14<=0: atr14=atr5
    if atr50<=0: atr50=atr14
    expansion=max(1.0,min(2.5,atr14/(atr50+1e-12)))
    burst=max(1.0,min(1.8,atr5/(atr14+1e-12)))
    effective=max(atr14,0.65*atr14+0.35*atr5)
    return {"atr5":atr5,"atr14":atr14,"atr50":atr50,"expansion":expansion,"burst":burst,"effective":effective}


def _trail_sl_price(p: dict, pred: dict, last: float) -> float:
    """Adaptive ATR trailing SL; recent bursts widen the trail without loosening risk."""
    side=str(p.get("side") or "")
    a=_adaptive_atr_pct(pred)
    callback=float(DEFAULT.get("trail_callback_atr",2.2))
    # Volatility expansion widens the trail; the short ATR reacts faster to a burst.
    dist=a["effective"]*callback*(1.0+0.35*(a["expansion"]-1.0)+0.20*(a["burst"]-1.0))
    dist=max(float(DEFAULT.get("trail_min_distance_pct",0.006)),dist)
    dist=min(float(DEFAULT.get("trail_max_distance_pct",0.035)),dist)
    if side=="long": return last*(1-dist)
    return last*(1+dist)


def _dynamic_trail_tp_price(p: dict, pred: dict, last: float, score: float = 0.8) -> float:
    """Stable, monotonic dynamic TP for TREND_TRAIL.

    The TP is anchored to the best favorable price seen since handoff, not
    recalculated from the current price alone.  It can only extend in the
    favorable direction.  When trend quality deteriorates, TP freezes instead
    of collapsing toward price; the trailing SL remains the active profit-lock.
    """
    side=str(p.get("side") or "")
    a=_adaptive_atr_pct(pred)
    strength=max(0.0,min(1.0,float(score)))
    original=float(p.get("original_tp_price") or p.get("tp") or last)
    current_tp=float(p.get("tp") or original)
    if side=="long":
        best=float(p.get("trail_highest_price") or last)
        anchor=max(best,last)
    else:
        best=float(p.get("trail_lowest_price") or last)
        anchor=min(best,last)

    # Volatility-aware target distance.  Expansion is bounded so a bad tick
    # cannot create an absurd TP.
    dist=a["effective"]*max(1.8,2.8*max(strength,0.45))*(1.0+0.25*(a["expansion"]-1.0))
    dist=max(float(DEFAULT.get("trail_tp_min_progress_pct",0.003)),dist)
    max_ext=float(DEFAULT.get("trail_tp_max_extension_pct",0.12))

    if side=="long":
        candidate=max(original, anchor*(1.0+dist))
        hard_cap=max(original, float(p.get("entry") or original)*(1.0+max_ext))
        candidate=min(candidate,hard_cap)
        # Monotonic: never move an established TP backwards.
        return max(current_tp, candidate)
    else:
        candidate=min(original, anchor*(1.0-dist))
        hard_floor=min(original, float(p.get("entry") or original)*(1.0-max_ext))
        candidate=max(candidate,hard_floor)
        return min(current_tp, candidate)

def _enter_trend_trail(symbol: str, p: dict, pred: dict, cfg: dict, last: float) -> bool:
    """Transactional NORMAL -> TREND_TRAIL handoff. Upgrades the existing TP in place and adds trailing behavior to the same SL; rollback restores the original TP+SL."""
    if not gate_enabled("trend_trail"): return False
    if str(p.get("exit_mode") or "NORMAL") not in ("NORMAL","TP_PREPARE","FAST_OPPORTUNITY"): return False
    tp_id=p.get("tp_attach_clordid"); sl_id=p.get("sl_attach_clordid")
    if not tp_id or not sl_id: return False
    with LOCK:
        p["exit_mode"]="TRANSITION"; p["transition_started_at"]=time.time(); p["transition_error"]=""
    _persist()
    try:
        real=alpha_live.positions(); cs=config.trading.get_ccxt_symbol(symbol)
        try: mode=alpha_live.pos_mode()
        except Exception: mode="long_short_mode"
        alive=any(x.get("symbol")==cs and (mode!="long_short_mode" or x.get("side")==p.get("side")) and float(x.get("contracts") or 0)>0 for x in real)
        if not alive: raise RuntimeError("切换前交易所仓位已不存在")

        # Keep protection continuous: do NOT cancel the TP. Upgrade the same TP first.
        info=_trail_direction_score(p,pred,last)
        dyn_tp=_dynamic_trail_tp_price(p,pred,last,info.get("score",0.8))
        dyn_sl=_trail_sl_price(p,pred,last)
        cur_sl=float(p.get("sl") or 0)
        if p.get("side")=="long":
            dyn_sl=max(dyn_sl,cur_sl); dyn_sl=min(dyn_sl,last*(1-0.001))
        else:
            dyn_sl=min(dyn_sl,cur_sl); dyn_sl=max(dyn_sl,last*(1+0.001))

        tp_amend=alpha_live.amend_tp_only(symbol,p["side"],dyn_tp,tp_id=tp_id,wait_timeout=5.0)
        if not tp_amend.get("verified"): raise RuntimeError(tp_amend.get("error") or "动态TP建立未验证")
        sl_amend=alpha_live.amend_sl_only(symbol,p["side"],dyn_sl,sl_id=sl_id,wait_timeout=5.0)
        if not sl_amend.get("verified"): raise RuntimeError(sl_amend.get("error") or "Trailing SL建立未验证")

        verify=alpha_live.protection_status(symbol,p["side"],expected_ids=[str(tp_id),str(sl_id)],wait_timeout=3.0)
        if not verify.get("verified"): raise RuntimeError("动态TP+Trailing SL最终验证失败")
        with LOCK:
            p["exit_mode"]="TREND_TRAIL"; p["trail_active"]=True
            p["trail_anchor_price"]=last
            p["trail_highest_price"]=last if p["side"]=="long" else p.get("trail_highest_price",last)
            p["trail_lowest_price"]=last if p["side"]=="short" else p.get("trail_lowest_price",last)
            p["trail_sl"]=dyn_sl; p["sl"]=dyn_sl; p["tp"]=dyn_tp
            p["dynamic_tp_pct"]=max(0.006,min(0.08,abs(dyn_tp/float(p.get("entry") or last)-1)))
            p["tp_disabled_at"]=0.0; p["tp_frozen"]=False; p["tp_freeze_reason"]=""; p["last_trail_update"]=time.time(); p["transition_error"]=""
        ledger_record("EXIT_MODE_SWITCHED",symbol,{"from":"NORMAL","to":"TREND_TRAIL","tp_id":tp_id,"sl_id":sl_id,"dynamic_tp":dyn_tp,"trail_sl":dyn_sl})
        _activity(f"实盘 {symbol}：EXIT_CONTROLLER 已完成 NORMAL → TREND_TRAIL，动态TP+Trailing SL均已验证")
        _persist(); return True
    except Exception as exc:
        rollback_ok=False; rollback_error=""
        try:
            orig_tp=float(p.get("original_tp_price") or p.get("tp") or 0)
            orig_sl=float(p.get("original_sl_price") or p.get("sl") or 0)
            protected_sl=float(p.get("sl") or orig_sl)
            if p.get("partial_tp_sl_stage") and p.get("partial_tp_sl_stage")!="NONE":
                if p.get("side")=="long": protected_sl=max(orig_sl,protected_sl)
                else: protected_sl=min(orig_sl,protected_sl)
            rb=alpha_live.restore_tp_sl(symbol,p["side"],orig_tp,protected_sl,tp_id,sl_id)
            rollback_ok=bool(rb.get("verified")); rollback_error=str(rb.get("error") or "")
            if rollback_ok:
                with LOCK:
                    p["tp_attach_clordid"]=rb.get("tp_attach_clordid") or tp_id; p["sl_attach_clordid"]=rb.get("sl_attach_clordid") or sl_id
                    p["tp"]=orig_tp; p["sl"]=orig_sl; p["trail_sl"]=orig_sl
        except Exception as rb_exc:
            rollback_error=str(rb_exc)
        with LOCK:
            p["exit_mode"]="NORMAL" if rollback_ok else "RECOVERY"; p["trail_active"]=False
            p["transition_error"]=f"切换失败: {exc}; 回滚={'成功' if rollback_ok else '失败'} {rollback_error}"
        ledger_record("EXIT_MODE_SWITCH_FAILED",symbol,{"error":str(exc),"rollback_ok":rollback_ok,"rollback_error":rollback_error})
        if rollback_ok: _activity(f"实盘 {symbol}：TREND_TRAIL切换失败，原TP+SL已回滚并验证，恢复NORMAL","warning")
        else:
            _activity(f"实盘 {symbol}：TREND_TRAIL切换及原保护回滚均未完成，进入RECOVERY：{rollback_error}","error")
            ops_open_breaker(f"EXIT_HANDOFF_RECOVERY_REQUIRED:{symbol}")
        _persist(); return False


def _live_trend_trail(symbol: str, p: dict, pred: dict, cfg: dict) -> None:
    """Single-owner controller: dynamically expands TP while ratcheting the SL only in the favorable direction."""
    if str(p.get("exit_mode") or "NORMAL")!="TREND_TRAIL": return
    ticker=okx_client.get_ticker(symbol) or {}; last=float(ticker.get("last") or 0)
    if last<=0: return
    now=time.time(); interval=float(cfg.get("trail_amend_seconds",20))
    if now-float(p.get("last_trail_update",0) or 0)<interval: return
    info=_trail_direction_score(p,pred,last)
    with LOCK:
        if p.get("side")=="long":
            p["trail_highest_price"]=max(float(p.get("trail_highest_price") or last),last)
        else:
            p["trail_lowest_price"]=min(float(p.get("trail_lowest_price") or last),last)
    score=max(0.0,float(info.get("score",0.0)))
    freeze_score=float(DEFAULT.get("trail_tp_freeze_score",0.55))
    rearm_score=float(DEFAULT.get("trail_tp_rearm_score",0.72))
    # TP is stateful: weak trend freezes the target rather than pulling it back.
    # A materially stronger trend can re-arm outward extension.
    if score < freeze_score:
        with LOCK:
            p["tp_frozen"]=True; p["tp_freeze_reason"]="TREND_WEAKENING"
    elif score >= rearm_score:
        with LOCK:
            p["tp_frozen"]=False; p["tp_freeze_reason"]=""
    current_tp=float(p.get("tp") or p.get("original_tp_price") or last)
    tp=_dynamic_trail_tp_price(p,pred,last,score) if not bool(p.get("tp_frozen")) else current_tp
    sl=_trail_sl_price(p,pred,last)
    current_sl=float(p.get("trail_sl") or p.get("sl") or 0)
    if p.get("side")=="long": sl=max(sl,current_sl); sl=min(sl,last*(1-0.001))
    else: sl=min(sl,current_sl); sl=max(sl,last*(1+0.001))
    if p.get("side")=="long" and tp<=last: return
    if p.get("side")=="short" and tp>=last: return
    try:
        tp_r={"verified":True,"skipped":True}
        old_tp=float(p.get("tp") or tp)
        tp_step=float(DEFAULT.get("trail_tp_min_step_pct",0.002))
        tp_needs_update=(abs(tp-old_tp)/max(abs(old_tp),1e-12) >= tp_step)
        if not bool(p.get("tp_frozen")) and tp_needs_update:
            tp_r=alpha_live.amend_tp_only(symbol,p["side"],tp,tp_id=p.get("tp_attach_clordid"),wait_timeout=3.0)
        sl_r=alpha_live.amend_sl_only(symbol,p["side"],sl,sl_id=p.get("sl_attach_clordid"),wait_timeout=3.0)
        if not tp_r.get("verified") or not sl_r.get("verified"): raise RuntimeError("动态TP或Trailing SL验证失败")
        with LOCK:
            if tp_needs_update and not p.get("tp_frozen"): p["tp"]=tp
            p["trail_sl"]=sl; p["sl"]=sl; p["last_trail_update"]=now
        ledger_record("TRAIL_PROTECTION_AMENDED",symbol,{"dynamic_tp":tp,"trail_sl":sl,"score":info.get("score")})
    except Exception as exc:
        _activity(f"实盘 {symbol}：TREND_TRAIL保护更新失败，保持已验证保护并进入恢复检查：{exc}","error")
        with LOCK: p["transition_error"]=f"Trailing动态保护更新失败: {exc}"


def _partial_tp_price(entry: float, side: str, target_pct: float) -> float:
    return entry * (1.0 + target_pct if side == "long" else 1.0 - target_pct)

def _live_partial_take_profit(symbol: str, p: dict, cfg: dict) -> None:
    """Optional staged TP. OFF means this function is a complete no-op.
    The exchange remains the source of truth: every reduction is reduce-only,
    quantity is re-read from OKX, and local state advances only after the fill
    is terminal and the remaining exchange position is confirmed.
    """
    # FAST v4 双引擎默认启用分批止盈（区间/趋势 sleeve 的高胜率画像依赖它，且仅收紧保护、
    # 全程 reduce-only 并经交易所逐笔校验）；其它模式仍只受全局 partial_tp 闸门控制，默认关闭。
    _is_fast_position = str(p.get("strategy_mode") or "").upper() == "FAST"
    if not (gate_enabled("partial_tp") or (_is_fast_position and p.get("fast_version") != "v6")):
        return
    try:
        if str(p.get("exit_mode") or "NORMAL") in ("TRANSITION", "RECOVERY"):
            return
        side=str(p.get("side") or "")
        entry=float(p.get("entry") or 0)
        if side not in ("long", "short") or entry <= 0:
            return
        real=alpha_live.positions(); cs=config.trading.get_ccxt_symbol(symbol)
        try: mode=alpha_live.pos_mode()
        except Exception: mode="long_short_mode"
        if mode=="long_short_mode":
            rp=next((x for x in real if x.get("symbol")==cs and x.get("side")==side and float(x.get("contracts") or 0)>0),None)
        else:
            rp=next((x for x in real if x.get("symbol")==cs and float(x.get("contracts") or 0)>0),None)
        if not rp:
            return
        remaining=float(rp.get("contracts") or 0)
        if remaining<=0:
            return
        # The first observation after enabling the feature establishes the real
        # exchange quantity as the baseline. This avoids inventing size locally.
        with LOCK:
            if not p.get("partial_tp_base_qty"):
                p["partial_tp_base_qty"]=remaining
            base_qty=float(p.get("partial_tp_base_qty") or remaining)
            steps=list(p.get("partial_tp_steps") or [])
            filled_by_step={str(k): float(v or 0) for k,v in (p.get("partial_tp_filled_by_step") or {}).items()}
        if base_qty<=0:
            return
        tp_base=float(p.get("base_tp_pct") or p.get("dynamic_tp_pct") or 0.02)
        # Two staged reductions: 30% at 50% of the original TP distance,
        # another 30% at 80%; the final 40% remains under the original/trailing exit.
        levels=[("TP1",0.50,0.30), ("TP2",0.80,0.30)]
        ticker=okx_client.get_ticker(symbol) or {}; last=float(ticker.get("last") or 0)
        if last<=0 or tp_base<=0:
            return
        for name,ratio,portion in levels:
            if name in steps:
                continue
            target_pct=max(0.002, min(tp_base*ratio, 0.25))
            if p.get('fast_version')=='v6': target_pct=fast_v6.partial_target_pct(p,ratio)
            # Never place a staged TP beyond the original/native TP target.
            if target_pct >= tp_base:
                continue
            target=_partial_tp_price(entry,side,target_pct)
            reached=(last>=target if side=="long" else last<=target)
            if not reached:
                break
            desired=base_qty*portion
            already_filled=max(0.0, filled_by_step.get(name,0.0))
            qty=min(remaining, max(0.0, desired-already_filled))
            if qty<=0:
                if already_filled >= desired*0.999:
                    with LOCK:
                        steps=list(p.get("partial_tp_steps") or [])
                        if name not in steps: steps.append(name)
                        p["partial_tp_steps"]=steps
                continue
            _activity(f"实盘 {symbol}：{name} 达标，准备按交易所真实剩余仓位执行分批止盈 {qty:.8g} 张", "info")
            r=alpha_live.reduce_only_close_qty(symbol,side,qty)
            if not r.get("flat_confirmed") and float(r.get("filled") or 0)<=0:
                _activity(f"实盘 {symbol}：{name} 未确认真实成交，保持原保护，不推进分批止盈状态", "warning")
                return
            actual_filled=float(r.get("filled") or 0)
            if actual_filled<=0:
                return
            # Record each partial slice as a real attributed exit using the actual
            # exchange fill, so the final attribution can explain every profit-taking step.
            try:
                pattr=trade_attribution(symbol,entry,float(r.get("average") or 0),side,actual_filled,exit_fee=float(r.get("fee") or 0),reason=name)
                with LOCK:
                    STATE.setdefault("attribution",[]).append({**pattr,"symbol":symbol,"side":side,"reason":name,"time":time.time(),"partial":True})
                    STATE["attribution"]=STATE["attribution"][-500:]
            except Exception:
                pattr={}
            with LOCK:
                steps=list(p.get("partial_tp_steps") or [])
                filled_by_step={str(k): float(v or 0) for k,v in (p.get("partial_tp_filled_by_step") or {}).items()}
                filled_by_step[name]=filled_by_step.get(name,0.0)+actual_filled
                desired=base_qty*portion
                # A partial/canceled fill is not considered complete; the next cycle
                # retries only the remaining target quantity, never the whole 30%.
                if filled_by_step[name] >= desired*0.999:
                    if name not in steps: steps.append(name)
                p["partial_tp_steps"]=steps
                p["partial_tp_filled_by_step"]=filled_by_step
                p["partial_tp_last_fill"]={"step":name,"filled":actual_filled,"cumulative_filled":filled_by_step[name],"requested":qty,"target_qty":desired,"average":float(r.get("average") or 0),"fee":float(r.get("fee") or 0),"time":time.time()}
            ledger_record("LIVE_PARTIAL_TP",symbol,{"step":name,"requested":qty,"filled":actual_filled,"average":r.get("average"),"fee":r.get("fee"),"target_price":target,"target_pct":target_pct,"remaining":r.get("remaining_contracts"),"attribution":pattr})
            _activity(f"实盘 {symbol}：{name} 已按真实成交确认，成交 {actual_filled:.8g} 张，成交均价 {float(r.get('average') or 0):.8g}")
            # TP1: protect the remaining 70% around breakeven. TP2: lift the
            # floor to TP1's price; existing trend-trailing remains the owner
            # of further trailing when its normal conditions are met.
            if name=="TP1":
                be=entry*(1.0005 if side=="long" else 0.9995)
                if p.get('fast_version')=='v6': be=_partial_tp_price(entry,side,fast_v6.partial_floor_pct(p,name))
                current_sl=float(p.get("sl") or 0)
                # Partial TP may only tighten protection, never widen an already-better SL.
                if side=="long" and current_sl>0: be=max(be,current_sl)
                if side=="short" and current_sl>0: be=min(be,current_sl)
                if p.get("sl_attach_clordid"):
                    rb=alpha_live.amend_sl_only(symbol,side,be,p.get("sl_attach_clordid"),wait_timeout=4.0)
                    if rb.get("verified"):
                        with LOCK: p["sl"]=be; p["partial_tp_sl_stage"]="BREAKEVEN"
                    else:
                        _activity(f"实盘 {symbol}：TP1后保本SL修改未验证，保留交易所现有SL", "warning")
            else:
                floor=_partial_tp_price(entry,side,max(0.002,min(tp_base*0.50,0.25)))
                if p.get('fast_version')=='v6': floor=_partial_tp_price(entry,side,fast_v6.partial_floor_pct(p,name))
                current_sl=float(p.get("sl") or 0)
                if side=="long" and current_sl>0: floor=max(floor,current_sl)
                if side=="short" and current_sl>0: floor=min(floor,current_sl)
                if p.get("sl_attach_clordid"):
                    rb=alpha_live.amend_sl_only(symbol,side,floor,p.get("sl_attach_clordid"),wait_timeout=4.0)
                    if rb.get("verified"):
                        with LOCK: p["sl"]=floor; p["partial_tp_sl_stage"]="TP1_PROTECTED"
                    else:
                        _activity(f"实盘 {symbol}：TP2后保护SL修改未验证，保留当前交易所SL", "warning")
            _persist()
            return
    except Exception as exc:
        _activity(f"实盘 {symbol}：分批止盈模块异常，未推进状态，原有TP/SL继续工作：{exc}", "warning")

def _live_exit_controller(symbol: str, p: dict, pred: dict, cfg: dict) -> None:
    """Central exit ownership: NORMAL uses old TP+SL; TREND_TRAIL exclusively owns the SL."""
    # v4 区间均值回归单：只认交易所 TP/SL + 分批止盈 + 4小时到期，不做趋势跟踪延展或冻结目标。
    if str(p.get("fast_engine") or "") == "RANGE_REVERSION":
        return
    if gate_enabled("trend_trail"):
        # 趋势跟踪由OKX原生 move_order_stop 托管，本地不再切换/轮询。
        return
    mode=str(p.get("exit_mode") or "NORMAL")
    if mode=="TREND_TRAIL":
        _live_trend_trail(symbol,p,pred,cfg); return
    if mode in ("TRANSITION","RECOVERY"):
        return
    ticker=okx_client.get_ticker(symbol) or {}; last=float(ticker.get("last") or 0)
    if last<=0: return
    info=_trail_direction_score(p,pred,last)
    if info["progress"]>=float(cfg.get("trail_prepare_progress",0.72)) and info["score"]>=0.60:
        with LOCK: p["exit_mode"]="TP_PREPARE"
    if info["progress"]>=float(cfg.get("trail_switch_progress",0.90)) and info["score"]>=0.78:
        _enter_trend_trail(symbol,p,pred,cfg,last)
    elif info["progress"]>=float(cfg.get("trail_prepare_progress",0.72)):
        with LOCK: p["exit_mode"]="TP_PREPARE" if info["score"]>=0.60 else "NORMAL"


def _live_dynamic_protection(symbol: str, p: dict, pred: dict, cfg: dict) -> None:
    """盘中动态调整 TP/SL；绝不扩大初始美元风险。"""
    try:
        if str(p.get("exit_mode") or "NORMAL") != "NORMAL":
            return
        dyn=pred.get("dynamic_tp_sl") or {}
        if not dyn: return
        now=time.time()
        if now-float(p.get("last_dynamic_update",0) or 0)<120: return
        entry=float(p.get("entry") or 0); side=str(p.get("side") or "")
        if entry<=0 or side not in ("long","short"): return
        base_sl=float(p.get("base_sl_pct") or p.get("dynamic_sl_pct") or dyn.get("sl") or 0.01)
        current_sl=float(p.get("dynamic_sl_pct") or base_sl)
        candidate_sl=float(dyn.get("sl") or current_sl)
        # Never widen live risk. Only tighten the stop after entry.
        safe_sl=min(candidate_sl,base_sl,current_sl)
        candidate_tp=float(dyn.get("tp") or p.get("dynamic_tp_pct") or p.get("base_tp_pct") or 0.02)
        current_tp=float(p.get("dynamic_tp_pct") or candidate_tp)
        # In a strengthening trend allow TP to expand; in deteriorating conditions
        # the dynamic policy may shorten TP. Both are bounded and rate-limited.
        new_tp=max(0.006,min(0.08,candidate_tp))
        if side=="long":
            new_sl_px=entry*(1-safe_sl); new_tp_px=entry*(1+new_tp)
        else:
            new_sl_px=entry*(1+safe_sl); new_tp_px=entry*(1-new_tp)
        old_sl_px=float(p.get("sl") or (entry*(1-current_sl if side=="long" else 1+current_sl)))
        old_tp_px=float(p.get("tp") or (entry*(1+current_tp if side=="long" else 1-current_tp)))
        # Ignore immaterial changes to avoid amendment churn.
        if abs(new_sl_px-old_sl_px)/entry<0.001 and abs(new_tp_px-old_tp_px)/entry<0.001: return
        # If an amendment would place a target on the wrong side of the market, defer it.
        ticker=okx_client.get_ticker(symbol) or {}; last=float(ticker.get("last") or 0)
        if last>0 and ((side=="long" and (new_sl_px>=last or new_tp_px<=last)) or (side=="short" and (new_sl_px<=last or new_tp_px>=last))):
            return
        result=alpha_live.amend_protection(symbol,side,new_tp_px,new_sl_px,expected_ids=[p.get("tp_attach_clordid"),p.get("sl_attach_clordid")])
        if not result.get("verified"):
            _activity(f"实盘 {symbol}：动态TP/SL修改未通过验证，保持原保护，不放大风险", "warning")
            return
        with LOCK:
            p["tp"]=new_tp_px; p["sl"]=new_sl_px; p["dynamic_tp_pct"]=new_tp; p["dynamic_sl_pct"]=safe_sl; p["dynamic_reason"]=str(dyn.get("reason", "")); p["last_dynamic_update"]=now
        ledger_record("DYNAMIC_PROTECTION_AMENDED",symbol,{"tp":new_tp_px,"sl":new_sl_px,"tp_pct":new_tp,"sl_pct":safe_sl,"reason":dyn.get("reason"),"regime":dyn.get("regime")})
        _activity(f"实盘 {symbol}：动态保护已调整 TP={new_tp_px:.6g} SL={new_sl_px:.6g}（{dyn.get('reason','')}）")
    except Exception as exc:
        _activity(f"实盘 {symbol}：动态保护检查异常，保留原TP/SL：{exc}", "warning")


def _live_manage(symbol,pred,cfg):
    with LOCK:p=STATE["positions"].get(symbol)
    if not p:return
    # 仓位级周期锁定：老仓位始终使用开仓时的模型/TP-SL周期，不随全局按钮切换。
    try:
        locked_mode=_normalize_strategy_mode(p.get("strategy_mode","SHORT"))
        if locked_mode != _normalize_strategy_mode(pred.get("strategy_mode","SHORT")):
            locked_pred=predict(symbol, strategy_mode=locked_mode)
            if locked_pred.get("model_ready"):
                pred=locked_pred
    except Exception as exc:
        _activity(f"实盘 {symbol}：仓位周期锁定读取失败，继续使用已锁定的TP/SL；原因={exc}","warning")
    try:
        # Exchange position is checked before any protection mutation. A stale local position
        # must never trigger a TP/SL amend after the position has already disappeared.
        real=alpha_live.positions(); cs=config.trading.get_ccxt_symbol(symbol)
        # net_mode下单向持仓side可能为None，只按symbol过滤；long_short_mode才匹配side
        try: mode=alpha_live.pos_mode()
        except Exception: mode="long_short_mode"
        if mode=="long_short_mode":
            rp=next((x for x in real if x.get("symbol")==cs and x.get("side")==p["side"] and float(x.get("contracts") or 0)>0),None)
        else:
            rp=next((x for x in real if x.get("symbol")==cs and float(x.get("contracts") or 0)>0),None)
        if not rp:
            # 连续3轮查不到才删除，防止网络瞬时问题误删持仓（比双重确认更安全）
            with LOCK:
                missing_counts=STATE.setdefault("position_missing_counts",{})
                missing_counts[symbol]=int(missing_counts.get(symbol,0))+1
                miss_count=missing_counts[symbol]
            if miss_count<3:
                _activity(f"实盘 {symbol}：第{miss_count}次未查询到交易所仓位，暂不删除持仓（连续3次才确认消失）", "warning")
                _persist()
                return
            # 连续3次都查不到，确认仓位消失
            with LOCK:
                STATE.setdefault("position_missing_counts",{}).pop(symbol,None)
            # 仓位已平：撤销该仓位所有残留算法单(移动止损/分批止盈/固定TP/SL)，交易所端兜底，
            # 防止"仓位没了、移动单还挂着"，在同币下次开仓后于非预期价位误平新仓。
            _flat_cleanup_or_block(symbol,p)
            fill=_exchange_close_fill(symbol,p)
            exit_px=float(fill.get("price") or 0)
            qty=float(fill.get("qty") or 0)
            if exit_px <= 0 or qty <= 0:
                with LOCK:
                    STATE.setdefault('flat_fill_pending',{})[symbol]=dict(p)
                    STATE['positions'].pop(symbol,None)
                    STATE["live_error"]=f"{symbol} 交易所仓位已归零，页面已移除持仓；成交明细等待对账"
                _activity(STATE["live_error"], "warning")
                _persist()
                return
            try:
                attr=trade_attribution(symbol,p.get("entry",exit_px),exit_px,p["side"],qty,exit_fee=float(fill.get("fee") or 0),reason="EXCHANGE_POSITION_GONE")
            except Exception as exc:
                attr={"net_pnl":0.0,"error":str(exc)}
            close_row={"time":time.time(),"symbol":symbol,"side":p["side"],"action":"CLOSE","reason":"EXCHANGE_POSITION_GONE","order_id":fill.get("order_id") or p.get("order_id"),"filled":qty,"average":exit_px,"flat_confirmed":True,"attribution":attr,"exchange_trade_id":fill.get("trade_id") or ""}
            with LOCK:
                # 平仓归因同时写入本地归因列表和实盘交易记录，停止实盘后仍可恢复。
                STATE.setdefault("attribution",[]).append({**attr,"symbol":symbol,"side":p["side"],"reason":"EXCHANGE_POSITION_GONE","time":close_row["time"]})
                STATE["attribution"]=STATE["attribution"][-500:]
                STATE["live_trades"].append(close_row); STATE["positions"].pop(symbol,None)
                STATE["history"]=STATE["live_trades"][-300:]
            try: ledger_record("LIVE_CLOSE_RECONCILED",symbol,attr)
            except Exception: pass
            _record_live_consecutive_result(attr, "EXCHANGE_POSITION_GONE")
            _record_strategy_health_close(attr, p, pred)
            if _is_fast(pred):
                _fast_detail(symbol,"平仓确认",f"OKX真实仓位已确认归零；平仓原因=交易所仓位消失；本次净盈亏={float(attr.get('net_pnl') or 0):+.4f} USDT；交易归因已记录")
            _activity(f"实盘 {symbol}：连续3次未查询到仓位，确认已在交易所消失，完成平仓归因")
            _persist(); return
        else:
            # 查到仓位，清零连续缺失计数
            with LOCK:
                STATE.setdefault("position_missing_counts",{}).pop(symbol,None)
        if p.get("fast_version") == "v7":
            _live_manage_v7(symbol,p,pred,cfg)
            return
        if p.get("fast_version") == "v6":
            _live_manage_v6(symbol,p,pred,cfg)
            return
        # ===== FAST持仓增强：(5)最小持仓 (4)保本移动 (8)横盘时间止损。交易所TP/SL条件单始终有效，不受影响。 =====
        _fast_min_hold=False
        # v4 区间均值回归单：失效位是宽结构止损，不能被横盘/动量反转信号提前砍仓，只认 TP/结构SL/时间。
        _fast_is_mr = bool(_is_fast(pred) and str(p.get("fast_engine") or "") == "RANGE_REVERSION")
        if _is_fast(pred):
            try:
                _tk=okx_client.get_ticker(symbol) or {}; _last=float(_tk.get("last") or 0)
                _now=time.time()
                entry_px=float(p.get("entry") or 0)
                base_sl_pct=float(p.get("base_sl_pct") or p.get("dynamic_sl_pct") or 0.01)
                hold_sec=_now-float(p.get("opened_at") or _now)
                side_p=str(p.get("side") or "")
                FAST_MIN_HOLD_SEC=600      # (5) 开仓后2根5m(10分钟)内不主动反转/横盘平仓，防开仓即被单根强反转打出
                # v3：止损/目标锚定15m(止损约1.2~2.4倍ATR15、目标看最近流动性池)，到达需要数小时，
                # 40分钟横盘强平会在目标达成前过早离场(真实回测中显著拉低PF)，放宽到90分钟。
                FAST_STAGNATE_SEC=5400     # (8) 持仓满18根5m(90分钟)却从未走出利润，判定走不动，释放资金
                _fast_min_hold=hold_sec<FAST_MIN_HOLD_SEC
                if _last>0 and entry_px>0 and base_sl_pct>0 and side_p in ("long","short"):
                    gain_r=(_last-entry_px)/(entry_px*base_sl_pct) if side_p=="long" else (entry_px-_last)/(entry_px*base_sl_pct)
                    with LOCK:
                        p["fast_max_gain_r"]=max(float(p.get("fast_max_gain_r",0) or 0),gain_r)
                        max_gain_r=float(p["fast_max_gain_r"])
                    exit_mode_now=str(p.get("exit_mode") or "NORMAL")
                    # (4) 保本移动：浮盈首次达1R、且仍是NORMAL(未交给趋势跟踪)时，把SL移到成本+双边成本，只做一次、不动TP。
                    be_buffer=2.0*(float(cfg.get("fee_pct",0.0005))+float(cfg.get("slippage_pct",0.0005)))
                    if (not p.get("fast_be_done")) and exit_mode_now=="NORMAL" and gain_r>=1.0:
                        be_sl_px=entry_px*(1+be_buffer) if side_p=="long" else entry_px*(1-be_buffer)
                        sl_id=p.get("sl_attach_clordid"); cur_sl_px=float(p.get("sl") or 0)
                        ok_direction_=(side_p=="long" and be_sl_px>cur_sl_px and be_sl_px<_last) or (side_p=="short" and be_sl_px<cur_sl_px and be_sl_px>_last)
                        if sl_id and ok_direction_:
                            _am=alpha_live.amend_sl_only(symbol,side_p,be_sl_px,sl_id=sl_id,wait_timeout=4.0)
                            if _am.get("verified"):
                                with LOCK:
                                    p["sl"]=be_sl_px; p["trail_sl"]=be_sl_px; p["fast_be_done"]=True
                                _fast_detail(symbol,"保本止损",f"浮盈达{gain_r:.2f}R，止损已上移到成本+{be_buffer*100:.2f}%锁定不亏；TP保持不变")
                            else:
                                _fast_detail(symbol,"保本止损",f"保本改单未验证，保留原止损：{_am.get('error') or _am.get('errors')}","warning")
                    # (8) 横盘时间止损：过了最小持仓、满90分钟、从未走出0.3R利润、且未交给趋势跟踪，主动平仓。
                    # 区间均值回归单不用（回归需要时间展开，早砍有害，只认结构止损/目标/4小时到期）。
                    if (not _fast_min_hold) and (not _fast_is_mr) and hold_sec>=FAST_STAGNATE_SEC and max_gain_r<0.3 and exit_mode_now=="NORMAL":
                        _fast_detail(symbol,"横盘时间止损",f"持仓{hold_sec/60:.0f}分钟从未走出0.3R利润(最大{max_gain_r:.2f}R)，判定走不动，主动平仓释放资金","warning")
                        r=_managed_close(symbol,side_p)
                        if r.get("status")=="no_position": return
                        flat=alpha_live.wait_position_closed(symbol,side_p,timeout=10)
                        if not flat.get("closed"):
                            with LOCK: STATE["live_error"]=f"{symbol} 横盘止损已提交但仓位仍存在，暂停后续开仓"
                            return
                        exit_px=float(r.get("average") or entry_px); qty=float(r.get("filled") or p.get("filled") or p.get("contracts") or 0)
                        try:
                            attr=trade_attribution(symbol,entry_px,exit_px,side_p,qty,exit_fee=float(r.get("fee") or 0),reason="FAST_STAGNATION")
                        except Exception:
                            attr={"net_pnl":0.0}
                        close_row={"time":time.time(),"symbol":symbol,"side":side_p,"action":"CLOSE","reason":"FAST_STAGNATION","order_id":r.get("order_id"),"filled":qty,"average":exit_px,"flat_confirmed":True,"attribution":attr}
                        with LOCK:
                            STATE.setdefault("attribution",[]).append({**attr,"symbol":symbol,"side":side_p,"reason":"FAST_STAGNATION","time":close_row["time"]})
                            STATE["attribution"]=STATE["attribution"][-500:]
                            STATE["live_trades"].append(close_row); STATE["history"]=STATE["live_trades"][-300:]; STATE["positions"].pop(symbol,None)
                        try: ledger_record("LIVE_CLOSE",symbol,{**r,"flat_confirmed":True,"attribution":attr,"reason":"FAST_STAGNATION"})
                        except Exception: pass
                        _record_live_consecutive_result(attr,"FAST_STAGNATION")
                        _record_strategy_health_close(attr,p,pred)
                        _fast_detail(symbol,"平仓确认",f"横盘时间止损完成；OKX仓位已归零；净盈亏={float(attr.get('net_pnl') or 0):+.4f} USDT")
                        _persist(); return
            except Exception as exc:
                _fast_detail(symbol,"持仓增强",f"保本/最小持仓/横盘管理异常，本轮保持原TP/SL：{exc}","warning")
        # ===== FAST持仓增强结束 =====
        # FAST持仓后反转/洗盘：只在不同的已完成5m K线上确认真反转；单根插针/扫盘/普通回撤不主动平仓。
        # (5) 最小持仓期内不做主动反转平仓（交易所TP/SL条件单仍在），过了最小持仓才进入反转判断。
        # v4 区间均值回归单整体跳过：动量“确认反转”恰是其对手盘逻辑，失效只认宽结构止损。
        if _is_fast(pred) and (not _fast_min_hold) and (not _fast_is_mr):
            try:
                now=time.time()
                last_check=float(p.get("fast_reversal_last_check",0) or 0)
                # 每15秒最多检查一次，避免每3秒重复请求OKX并拖慢全市场扫描。
                if now-last_check>=15:
                    rev=fast_position_reversal_check(symbol,str(p.get("side") or ""))
                    with LOCK:
                        p["fast_reversal_last_check"]=now
                        p["fast_reversal_last_result"]=rev
                        hist=list(p.get("fast_reversal_confirmed_bars") or [])
                        bar_ts=int(rev.get("bar_ts") or 0)
                        if rev.get("action")=="CONFIRMED" and bar_ts and bar_ts not in hist:
                            hist=(hist+[bar_ts])[-3:]
                        elif rev.get("action")=="HOLD" and not rev.get("reversal"):
                            # 洗盘/恢复会清掉连续反转确认，避免一次旧确认误触发平仓。
                            if rev.get("wash"):
                                hist=[]
                        p["fast_reversal_confirmed_bars"]=hist
                    _fast_detail(symbol,"反转/洗盘",f"{rev.get('action','HOLD')}｜评分={float(rev.get('score',0))*100:.0f}%｜{rev.get('reason','')}")
                    hist=list(p.get("fast_reversal_confirmed_bars") or [])
                    strong_once=rev.get("action")=="CONFIRMED" and float(rev.get("score",0) or 0)>=0.90
                    confirmed_exit=rev.get("action")=="CONFIRMED" and (strong_once or len(hist)>=2)
                    if confirmed_exit:
                        _fast_detail(symbol,"主动平仓","确认真反转：结构已有效破坏，满足多项反向裸K证据；不是单纯洗盘，执行主动退出","warning")
                        r=_managed_close(symbol,str(p.get("side") or ""))
                        if r.get("status")=="no_position": return
                        flat=alpha_live.wait_position_closed(symbol,str(p.get("side") or ""),timeout=10)
                        if not flat.get("closed"):
                            with LOCK: STATE["live_error"]=f"{symbol} 反转主动平仓已提交但仓位仍存在，暂停后续开仓"
                            return
                        exit_px=float(r.get("average") or p.get("entry") or 0)
                        qty=float(r.get("filled") or p.get("filled") or p.get("contracts") or 0)
                        try:
                            attr=trade_attribution(symbol,float(p.get("entry") or exit_px),exit_px,str(p.get("side") or ""),qty,exit_fee=float(r.get("fee") or 0),reason="FAST_CONFIRMED_REVERSAL")
                        except Exception as exc:
                            attr={"net_pnl":0.0,"error":str(exc)}
                        close_row={"time":time.time(),"symbol":symbol,"side":p.get("side"),"action":"CLOSE","reason":"FAST_CONFIRMED_REVERSAL","order_id":r.get("order_id"),"filled":qty,"average":exit_px,"flat_confirmed":True,"attribution":attr}
                        with LOCK:
                            STATE.setdefault("attribution",[]).append({**attr,"symbol":symbol,"side":p.get("side"),"reason":"FAST_CONFIRMED_REVERSAL","time":close_row["time"]})
                            STATE["attribution"]=STATE["attribution"][-500:]
                            STATE["live_trades"].append(close_row); STATE["history"]=STATE["live_trades"][-300:]; STATE["positions"].pop(symbol,None)
                        try: ledger_record("LIVE_CLOSE",symbol,{**r,"flat_confirmed":True,"attribution":attr,"reason":"FAST_CONFIRMED_REVERSAL"})
                        except Exception: pass
                        _record_live_consecutive_result(attr,"FAST_CONFIRMED_REVERSAL")
                        _record_strategy_health_close(attr,p,pred)
                        _fast_detail(symbol,"平仓确认",f"OKX真实仓位已归零；主动反转平仓完成；净盈亏={float(attr.get('net_pnl') or 0):+.4f} USDT")
                        _activity(f"实盘 {symbol}：FAST确认真反转，已主动平仓并确认OKX仓位归零")
                        _persist()
                        return
            except Exception as exc:
                _fast_detail(symbol,"反转/洗盘",f"监控异常：{exc}；为避免误杀，本轮保持原TP/SL","warning")
        # Only after a live exchange position is confirmed do we allow optional staged TP
        # and the original exit controller to act. partial_tp is an opt-in no-op when OFF.
        if _is_fast(pred):
            # 分批止盈以网页 partial_tp 开关为唯一权威（开关关则单一TP，不再宣称FAST自动开启）；
            # 趋势跟踪对区间回归单按设计跳过。
            _partial_eff = bool(gate_enabled('partial_tp'))
            if str(p.get('fast_engine') or '') == 'RANGE_REVERSION':
                _trail_txt = '跳过(区间单不用)'
            else:
                _trail_txt = '开启' if gate_enabled('trend_trail') else '关闭'
            _fast_detail(symbol,"持仓管理",f"OKX真实仓位已确认；方向={'做多' if p.get('side')=='long' else '做空'}；数量={float(p.get('filled') or p.get('contracts') or 0):.8g}；入场均价={float(p.get('entry') or 0):.8g}")
            _fast_detail(symbol,"持仓管理",f"FAST初始TP/SL={float(p.get('tp') or 0):.8g}/{float(p.get('sl') or 0):.8g}；分批止盈={'开启' if _partial_eff else '关闭'}(按开关)；趋势跟踪={_trail_txt}；持仓后不由FAST重算初始TP/SL")
        # 分批止盈由OKX原生条件单托管，本地不再轮询。
        _live_exit_controller(symbol,p,pred,cfg)
        _live_dynamic_protection(symbol,p,pred,cfg)
        if time.time()-p["opened_at"]>=p["max_seconds"]:
            r=_managed_close(symbol,p["side"])
            if r.get("status")=="no_position": return
            flat=alpha_live.wait_position_closed(symbol,p["side"],timeout=10)
            if not flat.get("closed"):
                with LOCK: STATE["live_error"]=f"{symbol} 平仓已提交但仓位仍存在，暂停后续开仓"
                return
            attr=trade_attribution(symbol,p.get("entry",r.get("average") or 0),float(r.get("average") or p.get("entry") or 0),p["side"],float(r.get("filled") or p.get("filled") or 0),exit_fee=float(r.get("fee") or 0),reason="TIME")
            try:
                rich=alpha_trade_metrics(p["side"],p.get("entry",r.get("average") or 0),p.get("price_path") or [p.get("last_ws_fill",{}).get("fillPx") or p.get("entry",0)],float(r.get("average") or p.get("entry") or 0),float(r.get("filled") or p.get("filled") or 0),exit_fee=float(r.get("fee") or 0))
                attr.update({k:v for k,v in rich.items() if k in ("mfe_pct","mae_pct")})
                with LOCK: STATE.setdefault("attribution",[]).append(attr); STATE["attribution"]=STATE["attribution"][-500:]
            except Exception: pass
            with LOCK:
                STATE["live_trades"].append({"time":time.time(),"symbol":symbol,"side":p["side"],"action":"CLOSE","reason":"TIME","order_id":r.get("order_id"),"filled":r.get("filled"),"average":r.get("average"),"flat_confirmed":True,"attribution":attr}); STATE["positions"].pop(symbol,None)
            if _is_fast(pred):
                _fast_detail(symbol,"平仓",f"达到持仓时间上限；已提交平仓；成交均价={float(r.get('average') or 0):.8g}；实际数量={float(r.get('filled') or p.get('filled') or 0):.8g}")
                _fast_detail(symbol,"平仓确认",f"OKX仓位已确认归零；本次净盈亏={float(attr.get('net_pnl') or 0):+.4f} USDT")
            _activity(f"实盘 {symbol}：达到持仓时间上限，已平仓并确认仓位归零")
            if p.get("order_id"): ops_record(p["order_id"],"CLOSED",{"close_order_id":r.get("order_id"),"close_average":r.get("average"),"reason":"TIME","attribution":attr})
            try: ledger_record("LIVE_CLOSE",symbol,{**r,"flat_confirmed":True,"attribution":attr})
            except Exception: pass
            _record_live_consecutive_result(attr, "TIME")
            _record_strategy_health_close(attr, p, pred)
            _persist()
    except Exception as exc:
        with LOCK: STATE["live_error"]=f"{symbol} 仓位管理失败: {exc}"
        _activity(f"实盘 {symbol}：仓位管理异常：{exc}", "error")


def _exchange_close_fill(symbol: str, p: dict) -> dict:
    """从OKX真实成交中寻找本次已平仓的最后一笔反向成交。只读，不下单。"""
    out={"price":0.0,"qty":0.0,"fee":0.0,"trade_id":"","order_id":""}
    try:
        ex=getattr(okx_client, "_exchange", None)
        if not ex: return out
        cs=config.trading.get_ccxt_symbol(symbol)
        trades=ex.fetch_my_trades(cs, since=max(0, int(float(p.get("opened_at") or 0)*1000)), limit=100) or []
        opened_ms=float(p.get("opened_at") or 0)*1000.0
        want_side="sell" if str(p.get("side"))=="long" else "buy"
        candidates=[]
        seen=set()
        for t in trades:
            ts=float(t.get("timestamp") or 0)
            if opened_ms and (not ts or ts < opened_ms): continue
            tid=str(t.get("id") or "")
            if tid and tid in seen: continue
            if tid: seen.add(tid)
            pos_side=str((t.get("info") or {}).get("posSide") or "")
            if pos_side in ("long", "short") and pos_side != p.get("side"): continue
            if str(t.get("side") or "").lower()!=want_side: continue
            price=float(t.get("price") or 0); amount=float(t.get("amount") or 0)
            if price<=0 or amount<=0: continue
            fee=float((t.get("fee") or {}).get("cost") or 0)
            candidates.append((ts,price,amount,fee,str(t.get("id") or ""),str(t.get("order") or "")))
        if candidates:
            candidates.sort(key=lambda x:x[0])
            if p.get("fast_version") == "v7":
                # V7 原生分批退出要累计所有成交，不只取最后一笔。
                expected=float(p.get("filled") or p.get("contracts") or 0)
                amount=sum(x[2] for x in candidates)
                if expected <= 0 or abs(amount-expected) > max(1e-8, expected*1e-6):
                    return out  # 明细不完整/含额外交易时不猜收益，也不删除记录。
                price=sum(x[1]*x[2] for x in candidates)/amount
                fee=sum(x[3] for x in candidates)
                tid=",".join(x[4] for x in candidates)
                oid=candidates[-1][5]
            else:
                _,price,amount,fee,tid,oid=candidates[-1]
            out.update({"price":price,"qty":amount,"fee":fee,"trade_id":tid,"order_id":oid})
    except Exception as exc:
        logger.debug(f"实盘平仓成交读取失败 {symbol}: {exc}")
    return out


def _finalize_stopped_live_closes(max_rounds: int = 3, interval: float = 2.0) -> None:
    """停止实盘时继续完成已经发生的平仓确认；不允许停止动作丢掉未完成的3次确认。"""
    for round_no in range(1, max(1,int(max_rounds))+1):
        with LOCK:
            positions={s:dict(p) for s,p in (STATE.get("positions") or {}).items() if p.get("live")}
        if not positions: return
        try:
            real=alpha_live.positions()
        except Exception as exc:
            _activity(f"停止收尾：交易所仓位查询失败，第{round_no}次确认暂不计数：{exc}", "warning")
            if round_no < max_rounds: time.sleep(interval)
            continue
        for symbol,p in positions.items():
            cs=config.trading.get_ccxt_symbol(symbol)
            try: mode=alpha_live.pos_mode()
            except Exception: mode="long_short_mode"
            if mode=="long_short_mode":
                alive=next((x for x in real if x.get("symbol")==cs and x.get("side")==p.get("side") and float(x.get("contracts") or 0)>0),None)
            else:
                alive=next((x for x in real if x.get("symbol")==cs and float(x.get("contracts") or 0)>0),None)
            if alive:
                with LOCK: STATE.setdefault("position_missing_counts",{}).pop(symbol,None)
                continue
            with LOCK:
                mc=STATE.setdefault("position_missing_counts",{})
                mc[symbol]=int(mc.get(symbol,0))+1
                count=mc[symbol]
            _activity(f"停止收尾：{symbol} 第{count}次未查询到交易所仓位，仍按3次确认规则处理", "warning")
            if count < 3: continue
            # 仓位已平：撤销所有残留算法单(移动/分批/固定)，交易所端兜底，防止停止后残留单误平未来新仓。
            _flat_cleanup_or_block(symbol,p)
            fill=_exchange_close_fill(symbol,p)
            exit_px=float(fill.get("price") or 0)
            qty=float(fill.get("qty") or 0)
            if exit_px <= 0 or qty <= 0:
                with LOCK:
                    STATE.setdefault('flat_fill_pending',{})[symbol]=dict(p)
                    STATE['positions'].pop(symbol,None)
                    STATE["live_error"]=f"{symbol} 仓位已归零，成交明细等待对账"
                _activity(STATE["live_error"], "warning")
                _persist()
                continue
            attr=trade_attribution(symbol,float(p.get("entry") or exit_px),exit_px,str(p.get("side") or ""),qty,exit_fee=float(fill.get("fee") or 0),reason="EXCHANGE_POSITION_GONE")
            close_row={"time":time.time(),"symbol":symbol,"side":p.get("side"),"action":"CLOSE","reason":"EXCHANGE_POSITION_GONE","order_id":fill.get("order_id") or p.get("order_id"),"filled":qty,"average":exit_px,"flat_confirmed":True,"attribution":attr,"exchange_trade_id":fill.get("trade_id") or ""}
            with LOCK:
                # 幂等：若其他线程已经完成确认，这里不重复写入。
                if symbol not in STATE.get("positions",{}):
                    continue
                STATE.setdefault("attribution",[]).append({**attr,"symbol":symbol,"side":p.get("side"),"reason":"EXCHANGE_POSITION_GONE","time":close_row["time"]})
                STATE["attribution"]=STATE["attribution"][-500:]
                STATE.setdefault("live_trades",[]).append(close_row)
                STATE["history"]=STATE["live_trades"][-300:]
                STATE["positions"].pop(symbol,None)
                STATE.setdefault("position_missing_counts",{}).pop(symbol,None)
            try: ledger_record("LIVE_CLOSE_RECONCILED",symbol,attr)
            except Exception: pass
            _record_live_consecutive_result(attr,"EXCHANGE_POSITION_GONE")
            _record_strategy_health_close(attr, p, None)
            _activity(f"停止收尾：{symbol} 连续3次确认仓位消失，已完成真实成交归因并保存")
            _persist()
        if round_no < max_rounds: time.sleep(interval)


def _loop(cfg):
    logger.warning(f"[ALPHA-X ULTRA] 启动 mode={STATE['mode']}；交易周期={STATE.get('strategy_mode','SHORT')}；旧 Trader 永不调用")
    if STATE["mode"]=="live": _reconcile_live(cfg)
    while True:
        with LOCK:
            if not STATE["running"]: break
        try:
            if STATE["mode"]=="live":
                _retry_flat_cleanup()
                _retry_flat_fills()
                try:
                    equity,_=_live_account_snapshot()
                    with LOCK:
                        if STATE.get("day")!=time.strftime("%Y-%m-%d"):
                            STATE["day"]=time.strftime("%Y-%m-%d"); STATE["day_start_equity"]=equity; STATE["consecutive_losses"]=0
                        STATE["equity"]=equity; STATE["balance"]=equity; STATE["risk_block"]=_risk_block(cfg)
                except Exception as exc:
                    good=_last_good_account()
                    if good:
                        # 偶发网络抖动：沿用最近一次成功读数，本轮照常运行，不挂错误
                        logger.warning(f"[ALPHA-X] 本轮读取实时权益失败，沿用 {good[2]:.0f} 秒前的读数：{exc}")
                    else:
                        with LOCK: STATE["live_error"]=f"{EQUITY_ERROR_PREFIX}（超过{EQUITY_STALE_OK:.0f}秒没有成功读数），暂停开仓: {exc}"
            if str(STATE.get("strategy_mode") or "").upper()=="FAST" and STATE.get("auto_select") and _fast_mode.get_active_version()=="v7":
                # V7 全自动选币：交易池 = 已持仓币(强制) + 24h涨幅榜前20
                _note_fast_phase("selecting")
                with LOCK: _held=list((STATE.get("positions") or {}).keys())
                symbols=tv_universe.top_gainers(_held)
                with LOCK: STATE["symbols"]=list(symbols)
                if not symbols:
                    _activity("V7选币：涨幅榜暂无合格币种，等待下一轮")
                    time.sleep(5); continue
            elif str(STATE.get("strategy_mode") or "").upper()=="FAST" and STATE.get("auto_select"):
                # 全自动选币：交易池 = 已持仓币(强制) + 白名单 + 动态观察池补位
                _note_fast_phase("selecting")
                with LOCK: _held=list((STATE.get("positions") or {}).keys())
                _eq=float(STATE.get("equity") or STATE.get("balance") or 0)
                symbols=fast_universe.maybe_update(_held, _eq, float(cfg.get("leverage",3) or 3))
                with LOCK: STATE["symbols"]=list(symbols)
                if not symbols:
                    _activity("自动选币：全市场暂无同时满足流动性/方向/结构的币种，等待下一轮筛选")
                    time.sleep(5); continue
            else:
                symbols=(STATE.get("symbols") if str(STATE.get("strategy_mode") or "").upper()=="FAST" else (STATE.get("symbols") or config.trading.get_active_symbols()))
            _note_fast_phase("scanning", pool=len(symbols))
            if str(STATE.get("strategy_mode") or "").upper()=="FAST" and _fast_mode.get_active_version()=="v7":
                # 并行预取全部币的K线与全市场报价；之后逐币决策直接用缓存，币多时一轮扫描快很多
                try:
                    _pre_err=_fast_mode.prefetch_v7(list(symbols))
                    if _pre_err:
                        _sample='；'.join(f"{k}:{str(v)[:60]}" for k,v in list(_pre_err.items())[:3])
                        logger.info(f"[V7 预取] {len(_pre_err)} 个币预取失败（逐币扫描时会再取一次），例如：{_sample}")
                except Exception as exc:logger.warning(f"[V7 预取] 失败，改为逐币读取：{exc}")
            preds={}
            alloc={}
            _activity(f"{STATE['mode']}：开始新一轮行情扫描，共 {len(symbols)} 个币种（自动选币）" if STATE.get("auto_select") else f"{STATE['mode']}：开始新一轮行情扫描，共 {len(symbols)} 个币种")
            if str(STATE.get("strategy_mode") or "").upper()=="FAST" and _fast_mode.get_active_version()=="v62":
                try:
                    market_state=_fast_mode.prepare_market_benchmark(list(symbols))
                    mode_cn="趋势" if abs(float(market_state.get("return_72h") or 0))>=.05 else "区间"
                    _activity(f"V6.3全市场状态：有效币种={market_state.get('symbols',0)}；近72小时等权变化={float(market_state.get('return_72h') or 0)*100:+.2f}%；本轮使用{mode_cn}规则")
                except Exception as exc:
                    _fast_mode.invalidate_market_benchmark()
                    _activity(f"V6.3全市场状态不可用：{exc}；本轮等待，不产生新信号","warning")
            for s in symbols:
                if not STATE["running"]: break
                pred=predict(s); preds[s]=pred
                with LOCK: STATE["last"][s]=pred
                _activity(f"{STATE['mode']} {s}：信号={pred.get('signal','FLAT')}，置信度={float(pred.get('confidence',0))*100:.1f}% ，原因={pred.get('reason','')}")
                if STATE["mode"] == "live":
                    if str(pred.get('strategy_mode') or '').upper() == 'FAST':
                        fd=pred.get('fast_data') or {}
                        src=fd.get('sources') or {}
                        missing=[k for k,v in src.items() if not v]
                        if missing:
                            _activity(f"实盘 {s}：真实OKX数据扫描 → 不完整（{','.join(missing)}），本轮禁止开仓", "warning")
                        else:
                            _activity(f"实盘 {s}：真实OKX数据扫描 → 成功；快速策略正在运行（无训练模型）")
                    _black_window_signal_header(s, pred)
            # 组合层：按风险调整后的置信度/波动率分配总风险预算，避免多个高度波动币种同时吃满单笔额度。
            raw={}
            vols={}
            for s,pred in preds.items():
                if pred.get("signal") not in ("LONG","SHORT") or not pred.get("model_ready"): continue
                ctx=pred.get("market_context") or {}
                vr=float(ctx.get("stress") or 0)
                # FAST：组合层只负责多币种之间按止损/波动率分配，
                # 信心与压力只在下游risk_budget计算一次，避免同一因素重复降仓。
                if _is_fast(pred):
                    raw[s]=1.0
                else:
                    raw[s]=float(pred.get("confidence",0))*(1.0-0.65*vr)
                vols[s]=max(float(pred.get("sl",0.01)),0.002)
            if raw:
                from alpha_institutional import portfolio_allocator
                alloc=portfolio_allocator(raw,vols,max_weight=1.0,max_gross=1.0)
            # V7：每笔都按设定的单笔风险下单，不按“同一轮碰巧出了几个信号”平分；总风险由最大持仓数控制。
            # 空位不够时，按成本占止损的比例从低到高优先开仓，而不是按涨幅榜顺序先到先得。
            with LOCK: _held=set((STATE.get("positions") or {}).keys())
            _order,_full,_msg=_v7_entry_plan(preds,_held,int(_scheme3_cfg(cfg).get("max_positions",4)))
            for s in _full: alloc[s]=1.0
            if _msg: _activity(_msg)
            for s in _order:
                pred=preds[s]
                if not STATE["running"]: break
                if STATE["mode"]=="live":
                    base_alloc=float(alloc.get(s,0.0))
                    adaptive_mult=float((pred.get("adaptive_context") or {}).get("size_multiplier",1.0) or 1.0)
                    fast_entry_mult=float(pred.get("fast_entry_size_multiplier",1.0) or 1.0) if _is_fast(pred) else 1.0
                    fast_entry_mult=max(0.25,min(1.0,fast_entry_mult))
                    final_alloc=max(0.0,min(1.20,base_alloc*max(0.35,min(1.20,adaptive_mult))*fast_entry_mult))
                    if _is_fast(pred) and fast_entry_mult < 0.999:
                        _activity(f"实盘 {s}：FAST入场强度={pred.get('signal_tier','一般')}；该级别降仓系数={fast_entry_mult:.2f}，只降低仓位，不改变方向/硬风控")
                    if abs(adaptive_mult-1.0)>0.001:
                        _activity(f"实盘 {s}：环境综合仓位系数={adaptive_mult:.2f}；组合基础权重={base_alloc:.3f}；最终风险权重={final_alloc:.3f}")
                    _live_manage(s,pred,cfg); _live_step(s,pred,cfg,final_alloc)
                else: _paper_step(s,pred,cfg)
            with LOCK: STATE["cycle_errors"]=0; STATE["last_cycle"]=time.time()
            # 自动选币交易池最多15币，适当放宽扫描间隔以稳定控制在OKX限频内
            cycle_sleep=(5 if STATE.get("auto_select") else 3) if str(STATE.get('strategy_mode') or '').upper()=='FAST' else 30
            _activity(f"{STATE['mode']}：本轮处理完成，下一轮约{cycle_sleep}秒后执行")
            time.sleep(cycle_sleep)
        except Exception as exc:
            with LOCK:
                STATE["cycle_errors"]=int(STATE.get("cycle_errors",0))+1
                STATE["live_error"]=str(exc) if STATE["mode"]=="live" else str(exc)
                STATE["last_error_ts"]=time.time()
                STATE["risk_block"]=STATE["live_error"] if STATE["mode"]=="live" else ""
            logger.exception(f"[ALPHA-X ULTRA] loop error: {exc}"); time.sleep(5 if str(STATE.get('strategy_mode') or '').upper()=='FAST' else 10)


LIMIT_BOUNDS={"max_positions":(1,10,int),"risk_pct":(0.001,0.10,float),"leverage":(1,50,int),"max_same_side":(0,10,int)}


def running_limits():
    """正在运行的引擎实际使用的最大持仓/单笔风险/杠杆；没运行时返回 None。"""
    cfg=RUNNING_CFG
    with LOCK:running=bool(STATE.get("running"))
    if not running or cfg is None:return None
    return {k:cfg.get(k,0 if k=='max_same_side' else None) for k in LIMIT_BOUNDS}


def update_running_limits(**values):
    """运行中修改最大持仓/单笔风险/杠杆：只影响之后的新开仓，已有持仓不变。未运行时不做任何事。"""
    clean={}
    for k,v in values.items():
        if v is None or k not in LIMIT_BOUNDS:continue
        lo,hi,cast=LIMIT_BOUNDS[k];v=cast(v)
        if not (lo<=v<=hi) or (isinstance(v,float) and not math.isfinite(v)):raise ValueError(f"{k} 必须在 {lo}~{hi} 之间")
        clean[k]=v
    cfg=RUNNING_CFG
    with LOCK:running=bool(STATE.get("running"))
    if not running or cfg is None or not clean:return running_limits()
    with LOCK:cfg.update(clean)
    _activity("运行中修改仓位参数（只影响之后的新开仓）："+"，".join(f"{k}={v}" for k,v in clean.items()))
    return running_limits()


def attribution_summary():
    with LOCK:
        rows=list(STATE.get("attribution") or [])
    return alpha_trade_summary(rows)

def attribution_full_report(limit: int = 300, include_exchange: bool = True):
    """完整交易归因报告：合并本地归因、实盘记录、账本事件、交易所真实成交，生成中文诊断。"""
    with LOCK:
        attrs=list(STATE.get("attribution") or [])
        live=list(STATE.get("live_trades") or [])
    events=ledger_recent(max(100, min(int(limit)*4, 5000)))
    fills=[]
    if include_exchange:
        symbols=set(str(x.get("symbol") or "") for x in live if x.get("symbol"))
        symbols.update(str(x.get("symbol") or "") for x in attrs if x.get("symbol"))
        for sym in list(symbols)[:20]:
            try:
                cs=config.trading.get_ccxt_symbol(sym)
                rows=okx_client._exchange.fetch_my_trades(cs, limit=min(100, max(20, int(limit)))) if getattr(okx_client, "_exchange", None) else []
                for t in rows or []:
                    fills.append({"id":t.get("id"),"order_id":t.get("order"),"timestamp":t.get("timestamp"),"symbol":t.get("symbol"),"side":t.get("side"),"price":float(t.get("price") or 0),"amount":float(t.get("amount") or 0),"cost":float(t.get("cost") or 0),"fee":float((t.get("fee") or {}).get("cost") or 0),"fee_currency":(t.get("fee") or {}).get("currency","")})
            except Exception as exc:
                logger.debug(f"归因报告读取交易所成交失败 {sym}: {exc}")
    return alpha_full_attribution(attrs, live, events, fills, limit=limit)

def governance_snapshot(symbol: str, challenger_fingerprint: str = ""):
    r=research_status(); s=(r.get("symbols") or {}).get(symbol,{})
    champ=s.get("champion") or {}; chall=next((x for x in s.get("challengers",[]) if x.get("fingerprint")==challenger_fingerprint),None)
    if not chall: return {"symbol":symbol,"eligible":False,"reason":"challenger_not_found"}
    return governance_compare(champ,chall)

def recovery_health():
    """Recovery gate only: pending close/protection recovery, independent of WS health."""
    with LOCK:
        close_pending=bool(STATE.get("close_recovery") or {})
        protection_pending=bool(STATE.get("protection_blocks") or {})
    return {"healthy": not (close_pending or protection_pending),
            "close_recovery_pending": close_pending,
            "protection_blocks_pending": protection_pending}

def health():
    """Operational liveness. Silence of private-order events is not a WS failure.
    Transport liveness is supervised by websocket-client ping/pong.
    """
    with LOCK:
        running=bool(STATE.get("running")); mode=STATE.get("mode"); last=STATE.get("last_cycle"); errs=int(STATE.get("cycle_errors",0)); ws=dict(STATE.get("ws") or {})
    age=(time.time()-float(last)) if last else 1e9
    transport_age=float(ws.get("transport_age") or 1e9)
    stale_cycle=running and age>180
    stale_ws=mode=="live" and running and (not ws.get("running") or transport_age>45)
    return {"healthy":not(stale_cycle or stale_ws or errs>=5),"cycle_age_sec":age,
            "ws_transport_age_sec":transport_age,"ws_event_age_sec":float(ws.get("event_age") or 0),
            "ws_age_sec":transport_age,"cycle_errors":errs,"stale_cycle":stale_cycle,
            "stale_ws":stale_ws,"breaker":ops_breaker_status()}

def research():
    return research_status()

def autonomy_snapshot():
    with LOCK:
        trades=list(STATE.get("live_trades") or []) + list(STATE.get("paper_trades") or [])
        attrs=list(STATE.get("attribution") or [])
        health_state=health()
    exec_rows=[]
    for t in trades:
        q=t.get("execution_quality")
        if isinstance(q,dict): exec_rows.append(q)
    ex=execution_summary(exec_rows)
    ra=regime_attribution(attrs + trades)
    decision=autonomous_decision(health=health_state, execution=ex)
    return {"version":VERSION,"decision":decision,"health":health_state,"regime_attribution":ra,"execution":ex}


def integrated_readiness():
    """Expose the same gates used by the live pre-trade path; never grants live permission."""
    h=health()
    br=ops_breaker_status()
    with LOCK:
        fence=INTEGRATION_FENCE
        mode=STATE.get("mode")
    fence_ok=(mode!="live") or bool(fence and authorize_execution(fence))
    return {"version":VERSION,"integrated":True,"healthy":h.get("healthy",False),
            "breaker":br,"fencing_ok":fence_ok,"mode":mode,
            "new_risk_allowed":bool(h.get("healthy",False) and not br.get("open") and fence_ok),
            "note":"最终下单仍受每笔 pretrade_pipeline、OKX 实际状态和 native TP/SL 验证约束"}

def strategy_health_status(limit: int = 20):
    """只读策略自我复盘面板；不会触发交易。"""
    try:
        return strategy_health_report(limit)
    except Exception as exc:
        return {"trades": [], "groups": [], "total_records": 0, "error": str(exc)}


def status():
    with LOCK:
        uni=fast_universe.snapshot() if STATE.get("auto_select") else {"enabled": False}
        return {"version":VERSION,**STATE,"universe":uni,"strategy_profile":_strategy_profile(STATE.get("strategy_mode","SHORT")),"gate_switches":dict(STATE.get("gate_switches") or DEFAULT_GATE_SWITCHES),"research":research_status(),"autonomy":autonomy_snapshot()}


def _fence_heartbeat_loop():
    global INTEGRATION_FENCE
    while not FENCE_STOP_EVENT.wait(10.0):
        with LOCK:
            token=dict(INTEGRATION_FENCE or {})
            running=bool(STATE.get("running")) and STATE.get("mode")=="live"
        if not running or not token:
            continue
        renewed=renew_execution_fence(token)
        if renewed:
            with LOCK:
                INTEGRATION_FENCE=renewed
            ledger_record("EXECUTION_FENCE_RENEWED","SYSTEM",renewed)
        else:
            with LOCK:
                STATE["risk_block"]="执行主节点 fencing 续期失败，可能存在其他实盘进程接管；禁止新开仓"
            _activity("实盘执行授权续期失败：Fencing 未重新签发，保持禁止新开仓", "warning")


def _validate_start_models(symbols, strategy_mode: str, live: bool = False) -> None:
    """启动前只做模型就绪检查，避免前端显示“已启动”但后台随后全部无模型。

    这里只验证与本次启动模式严格匹配的 ALPHA-X 模型；V17/旧 Trader/旧 ML
    不参与。缺少模型时直接拒绝启动，让“训练完成 -> 启动”成为确定性的链路。
    """
    missing=[]
    for symbol in list(symbols or []):
        try:
            obj=load(str(symbol), strategy_mode)
            if not obj:
                missing.append(str(symbol))
                continue
            if live:
                meta=obj.get("meta") if isinstance(obj,dict) else {}
                if not isinstance(meta,dict) or not bool(meta.get("live_ready", False)):
                    reason=(meta.get("live_ready_reason") if isinstance(meta,dict) else None) or "最终测试未达到实盘最低要求"
                    missing.append(f"{symbol}({reason})")
        except Exception as exc:
            missing.append(f"{symbol}({exc})")
    if missing:
        label=_strategy_profile(strategy_mode).get("label", strategy_mode)
        raise RuntimeError(f"启动前模型检查失败：{label}模型未就绪：{', '.join(missing)}。请先完成对应模式训练")


@_control_serialized
def start(settings=None):
    global LOOP_THREAD
    with LOCK:
        if STATE["running"]: return False
    if LOOP_THREAD is not None and LOOP_THREAD.is_alive():
        raise RuntimeError("上一轮仍在收尾，请稍后再启动")
    cfg={**DEFAULT,**(settings or {})}; mode="live" if cfg.get("live_enabled") else "paper"
    with LOCK:
        if STATE.get("positions") and STATE.get("mode") != mode:
            raise RuntimeError("仍有持仓或待对账记录，不能切换实盘/模拟模式")
    strategy_mode=_normalize_strategy_mode(cfg.get("strategy_mode", STATE.get("strategy_mode","SHORT")))
    cfg["strategy_mode"]=strategy_mode
    start_symbols=list(cfg.get("symbols") or ([] if str(cfg.get("strategy_mode") or "").upper()=="FAST" else config.trading.get_active_symbols()) or [])
    # FAST 自动选币：手选币作为“白名单(锁定必做)”，可以为空，由选币层动态补候选
    auto_select=bool(cfg.get("auto_select")) and strategy_mode=="FAST"
    if not start_symbols and not auto_select:
        raise RuntimeError("启动失败：没有选择任何交易币种")
    # FAST 是独立的无模型实时策略模式；启动前只验证真实OKX核心数据源。
    if strategy_mode=="FAST":
        # FAST 是独立的日内裸K模式：绝对不能进入模型就绪/模型 live_ready 校验。
        # 自动选币开启时，候选币是运行时动态选出的，只对“白名单(手选锁定币)”做启动前检查；
        # 动态候选币的数据完整性由主循环每轮 fast_data sources 检查兜底。
        check_symbols = start_symbols  # 自动选币时这里只含白名单，可能为空
        for symbol in check_symbols:
            chk=fast_data_check(str(symbol))
            if not chk.get("connected"):
                bad="；".join(str(m) for m in (chk.get("missing") or []))
                raise RuntimeError(f"快速实盘启动前真实数据检查失败：{symbol}；{bad or '核心数据源不可用'}")
        if auto_select:
            fast_universe.configure({
                "enabled": True,
                "whitelist": start_symbols,
                "max_positions": int(cfg.get("max_positions", 10) or 10),
                "max_notional_pct": float(cfg.get("max_notional_pct", 0.40) or 0.40),
                "coarse_top": cfg.get("uni_coarse_top"),
                "watchlist_size": cfg.get("uni_watchlist"),
                "min_turnover_usdt": cfg.get("uni_min_turnover"),
                "blacklist": cfg.get("uni_blacklist") or [],
            })
            _activity(f"快速实盘·自动选币已开启：白名单(锁定)={start_symbols or '无'}，目标最多{int(cfg.get('max_positions',10) or 10)}仓，系统自动全市场筛选并直接进入信号开仓")
        else:
            fast_universe.configure({"enabled": False, "whitelist": start_symbols})
            _activity("快速实盘：启动前已通过真实OKX数据源检查；明确跳过训练模型检查")
    else:
        _validate_start_models(start_symbols, strategy_mode, live=(mode=="live"))
    if mode=="live":
        if ops_breaker_status().get("open"): raise RuntimeError("ALPHA-X KILL SWITCH 尚未解除，请人工执行 /alpha/unlock")
        live_check()
    with LOCK:
        if STATE["running"]: return False
        STATE["running"]=True; STATE["mode"]=mode; STATE["strategy_mode"]=strategy_mode; STATE["live_error"]=""; STATE["risk_block"]=""; STATE["started_at"]=time.time()
        # 自动选币时初始化为白名单，动态交易池由主循环按选币结果持续刷新
        STATE["symbols"]=list(cfg.get("symbols") or []) if (cfg.get("symbols") or auto_select) else STATE.get("symbols")
        STATE["auto_select"]=bool(auto_select)
        if mode=="paper" and not STATE.get("paper_trades") and not STATE.get("positions"):
            STATE["balance"]=cfg["paper_start_balance"]; STATE["equity"]=STATE["balance"]; STATE["day_start_equity"]=STATE["equity"]; STATE["live_start_equity"]=None
    if mode=="live":
        global ALPHA_WS, INTEGRATION_FENCE
        INTEGRATION_FENCE=issue_execution_fence(f"alpha-{os.getpid()}")
        FENCE_STOP_EVENT.clear()
        ledger_record("EXECUTION_FENCE_ISSUED","SYSTEM",INTEGRATION_FENCE)
        global FENCE_HEARTBEAT_THREAD
        FENCE_HEARTBEAT_THREAD=threading.Thread(target=_fence_heartbeat_loop,daemon=True,name="alpha-fence-heartbeat")
        FENCE_HEARTBEAT_THREAD.start()
        try:
            # 记录本次实盘启动时的真实账户权益，网页“总盈亏”以它为基准，避免拿模拟盘10000U当实盘初始资金。
            start_equity, _ = _live_account_snapshot()
            with LOCK:
                STATE["live_start_equity"] = float(start_equity)
                STATE["equity"] = float(start_equity)
                STATE["balance"] = float(start_equity)
                STATE["day_start_equity"] = float(start_equity)
                STATE["ws"] = {}
            _activity(f"实盘启动：记录本次启动时真实账户权益 {float(start_equity):.2f} USDT")
            ALPHA_WS=AlphaPrivateWS(_on_ws_event)
            ALPHA_WS.start()
            with LOCK: STATE["ws"]=ALPHA_WS.status()
        except Exception as exc:
            with LOCK: STATE["ws"]={"error":str(exc)}
    _persist()
    global RUNNING_CFG
    RUNNING_CFG=cfg
    LOOP_THREAD=threading.Thread(target=_loop,args=(cfg,),daemon=True,name="alpha-ultra-loop")
    LOOP_THREAD.start()
    return True



def emergency_flatten():
    """紧急平掉 ALPHA-X 自己登记的实盘仓位；未知/人工仓位不碰。"""
    with LOCK:
        if STATE["mode"] != "live":
            return {"ok": True, "closed": [], "message": "当前不是实盘模式"}
        targets=list(STATE["positions"].items())
    closed=[]; errors=[]
    for symbol,p in targets:
        try:
            r=_managed_close(symbol,p["side"])
            if not r.get("flat_confirmed"):
                raise RuntimeError("紧急平仓未确认仓位归零")
            closed.append({"symbol":symbol,"side":p["side"],**r})
            if r.get("status") != "no_position":
                with LOCK:
                    STATE["positions"].pop(symbol,None)
                    ks_attr=trade_attribution(symbol,p.get("entry",0),float(r.get("average") or p.get("entry") or 0),p["side"],float(r.get("filled") or p.get("filled") or 0),exit_fee=float(r.get("fee") or 0),reason="KILL_SWITCH")
                    STATE["live_trades"].append({"time":time.time(),"symbol":symbol,"side":p["side"],"action":"CLOSE","reason":"KILL_SWITCH","order_id":r.get("order_id"),"filled":r.get("filled"),"average":r.get("average"),"flat_confirmed":True,"attribution":ks_attr})
                    _record_strategy_health_close(ks_attr, p, None)
                    _record_live_consecutive_result(ks_attr, "KILL_SWITCH")
        except Exception as exc:
            errors.append({"symbol":symbol,"error":str(exc)})
    with LOCK:
        STATE["running"]=False
        STATE["risk_block"]="KILL SWITCH"
    ops_open_breaker("KILL_SWITCH")
    with LOCK:
        STATE["history"]=STATE["live_trades"][-300:]
    _persist()
    return {"ok":not errors,"closed":closed,"errors":errors}

@_control_serialized
def stop():
    global ALPHA_WS, FENCE_HEARTBEAT_THREAD
    # 停止只禁止新的策略循环；已经发生但尚未完成3次确认的实盘平仓必须收尾。
    FENCE_STOP_EVENT.set()
    with LOCK: STATE["running"]=False
    if LOOP_THREAD is not None and LOOP_THREAD.is_alive():
        LOOP_THREAD.join(timeout=15)
        if LOOP_THREAD.is_alive():
            raise RuntimeError("已禁止新开仓，正在等待本轮请求收尾，请稍后再试")
    try:
        if ALPHA_WS: ALPHA_WS.stop(); STATE["ws"]=ALPHA_WS.status()
    except Exception: pass
    _persist()
    try:
        _finalize_stopped_live_closes(max_rounds=3, interval=2.0)
    finally:
        _persist()
