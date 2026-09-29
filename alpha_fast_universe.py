"""
ALPHA-X FAST 自动选币层（全自动模式）
=====================================
三层漏斗，越往外越便宜越慢、越往里越贵越快，避免3秒扫全市场打爆OKX限频：
  L0 全市场粗筛：1个请求拿全部USDT永续24h快照，按成交额/点差/涨跌/买得起过滤 -> 约20个
  L1 观察池精筛：只对粗筛胜出者拉4H/1H/15m，按方向/波动/结构/位置打分 -> 约15个
  L2 交易池：已持仓币(强制保留) + 白名单(手选锁定) + 观察池补位，交回主循环跑现有FAST信号

设计原则：
  1. 选币层只决定“看哪些币/偏多还是偏空”，绝不绕过任何现有开仓硬门与风控；
  2. 已持仓币永远在交易池里，选币绝不能漏管真实仓位；
  3. 进入/退出带滞回防抖 + 冷却，避免候选在边界反复横跳；
  4. 任何异常都自吞、返回上一次结果，绝不让选币层影响主循环交易；
  5. 由主循环按时间片串行调用，不另起线程，避免并发竞态。
"""
from __future__ import annotations
import math
import time
import threading
from typing import Any, Dict, List, Optional

import numpy as np
from loguru import logger

from config import config
from okx_client import okx_client
# 复用信号层的纯价格行为函数，保证选币方向与实盘信号口径一致
from alpha_fast_mode import (
    _bar_frame, _swings, _structure, _directional_context, _price_location,
    _v4_regime, _bollinger, _rsi_arr,
    FAST_V4_ENSEMBLE, FAST_V4_TREND_SLEEVE,
)
# 运行时引擎开关（页面可切 v4/v4+趋势/v3）：必须动态读模块属性，不能用上面 import 进来的副本，
# 否则页面切换后选币排名仍按旧引擎。下方排名分支统一读 _afm.* 。
import alpha_fast_mode as _afm

LOCK = threading.RLock()

# 默认参数（可由 start(settings) 覆盖）
DEFAULT_UNIVERSE = {
    "enabled": False,           # 总开关，默认关=回到手动选币，完全向后兼容
    "coarse_top": 20,           # L0粗筛保留数量
    "watchlist_size": 15,       # L1观察池大小（=10仓+5候选补位）
    "trade_pool_buffer": 5,     # 交易池在最大持仓之外多盯几个候选
    "min_turnover_usdt": 5e7,   # L0：24h成交额下限(5000万U)
    "max_spread_bps": 20.0,     # L0：点差上限(bps)
    "min_abs_pct": 1.0,         # L0：24h涨跌幅绝对值下限%（剔除死水）
    "max_abs_pct": 35.0,        # L0：24h涨跌幅绝对值上限%（剔除妖币）
    # L0波动甜区：24h涨跌幅落在该区间最健康（有动能但未到末端），超过end_pct视为末端快速降分
    "sweet_pct_lo": 3.0,
    "sweet_pct_hi": 18.0,
    "end_pct": 25.0,
    "coarse_interval": 180,     # L0粗筛间隔(秒)
    "refine_interval": 45,      # L1精筛间隔(秒)
    "enter_hyst": 2,            # 连续精筛达标N次才进观察池
    "exit_hyst": 2,             # 连续不达标N次才移出
    "cooldown_sec": 900,        # 移出后冷却(秒)
    "min_agreement": 0.55,      # L1：高周期方向一致度下限
    "min_swing": 4,             # L1：15m结构高低点最少个数
    "vol_low": 0.003,           # L1：15m近20根振幅/价格 下限（v3旧引擎沿用，保持原行为）
    "vol_low_v4": 0.004,        # v4区间均值回归专用下限（选币v2：双数据因果验证，0.003→0.004在75天19币与199天6币两套样本上PF均明显抬升、胜率约65%，广撒网下仍约6.6单/天；挡掉回归空间被手续费吃光的死水/大盘低波期）
    "vol_high": 0.06,          # L1：上限（剔除疯狂波动）
    "vol_sweet": 0.012,         # L1：15m中位波幅甜区(1.2%)，钟形给分中心
    "fourh_reverse_strength": 0.35,  # 4H强逆向结构力度阈值（与实盘信号层硬过滤完全对齐）
    "extension_atr": 2.2,       # L1：现价相对15m均线乖离超过该ATR倍数=追在末端，剔除
    "same_side_ratio": 0.8,     # 同方向持仓占比上限(10仓->最多8个同方向)
    # 买得起口径（与alpha_risk.risk_budget的单仓名义上限对齐，configure按实盘风控覆盖）
    "max_notional_pct": 0.40,   # 单仓名义占权益上限
    "budget_safety": 0.90,      # 买得起预算安全垫（分散/信心波动后仍买得到1张）
    "whitelist": [],            # 手选锁定币，永远在交易池
    "blacklist": [],            # 永不参与
}

U: Dict[str, Any] = {
    "cfg": dict(DEFAULT_UNIVERSE),
    "coarse_pool": [],         # L0结果
    "watchlist": [],           # L1结果：[{symbol,side,score,reasons,enter_cnt,exit_cnt}]
    "enter_track": {},         # 候选连续达标计数（尚未进观察池也要累计，否则永远凑不满enter_hyst）
    "cooldown": {},            # symbol -> 冷却到期时间戳
    "last_coarse": 0.0,
    "last_refine": 0.0,
    "trade_set": [],
    "scanned": 0,
    "last_msg": "自动选币未运行",
    "history": [],             # 选入/淘汰日志（最近100条）
}


def configure(opts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """启动时写入配置并重置运行态。"""
    with LOCK:
        cfg = dict(DEFAULT_UNIVERSE)
        if opts:
            for k, v in opts.items():
                if k in cfg and v is not None:
                    cfg[k] = v
        cfg["whitelist"] = [str(s).upper() for s in (cfg.get("whitelist") or [])]
        cfg["blacklist"] = [str(s).upper() for s in (cfg.get("blacklist") or [])]
        U["cfg"] = cfg
        U["coarse_pool"] = []
        U["watchlist"] = []
        U["enter_track"] = {}
        U["cooldown"] = {}
        U["last_coarse"] = 0.0
        U["last_refine"] = 0.0
        U["trade_set"] = list(cfg["whitelist"])
        U["history"] = []
        return dict(cfg)


def _log(action: str, symbol: str, why: str) -> None:
    with LOCK:
        U["history"].append({"time": time.time(), "action": action, "symbol": symbol, "why": why})
        U["history"] = U["history"][-100:]


def _fetch_frames(symbol: str):
    """给单个候选币拉4H/1H/15m（4H/1H有长缓存，实际请求很少）。失败返回None。"""
    ex = getattr(okx_client, "_exchange", None)
    if ex is None:
        return None
    cs = config.trading.get_ccxt_symbol(symbol)
    try:
        f4 = _bar_frame(ex.fetch_ohlcv(cs, "4h", limit=40), "4h")
        f1 = _bar_frame(ex.fetch_ohlcv(cs, "1h", limit=80), "1h")
        f15 = _bar_frame(ex.fetch_ohlcv(cs, "15m", limit=120), "15m")
        if min(len(f4.get("close", [])), len(f1.get("close", [])), len(f15.get("close", []))) < 30:
            return None
        return {"4h": f4, "1h": f1, "15m": f15}
    except Exception:
        return None


# ---------------- 选币评分辅助（纯价格行为，与信号层口径一致） ----------------
def _sma(arr: np.ndarray, n: int) -> float:
    a = np.asarray(arr, dtype=float)
    if a.size == 0:
        return 0.0
    return float(np.mean(a[-min(n, a.size):]))


def _bell(x: float, center: float, half: float) -> float:
    """以center为满分、向两侧线性衰减到0（half为半宽），结果裁剪到[0,1]。"""
    if half <= 0:
        return 1.0 if x == center else 0.0
    return float(np.clip(1.0 - abs(x - center) / half, 0.0, 1.0))


def _extension_atr(frame: Dict[str, np.ndarray]) -> float:
    """现价相对15m SMA20的乖离，用ATR(近20根中位波幅)归一。
    >0=价格在均线上方；绝对值越大越延伸，追单越危险（末端特征）。"""
    c = frame.get("close", np.array([])); h = frame.get("high", np.array([])); l = frame.get("low", np.array([]))
    n = min(20, len(c))
    if n < 10:
        return 0.0
    atr = float(np.median(np.maximum(h[-n:] - l[-n:], 1e-12)))
    if atr <= 0:
        return 0.0
    return float((c[-1] - _sma(c, 20)) / atr)


# ---------------- L0 全市场粗筛 ----------------
def coarse_scan(equity: float, leverage: float) -> List[Dict[str, Any]]:
    cfg = U["cfg"]
    black = set(cfg["blacklist"])
    tickers = okx_client.fetch_swap_tickers()
    U["scanned"] = len(tickers)
    # 单仓可用名义预算：与 alpha_risk.risk_budget 的单仓 cap 口径对齐——
    # min(单仓名义上限=权益*max_notional_pct, 总可用名义=权益*杠杆*0.8 等分到每仓)，再留安全垫。
    # 权益尚未读到时暂不按金额过滤（返回inf），避免启动初期把全市场误杀。
    eq = max(float(equity or 0), 0.0)
    lev = max(float(leverage or 3), 1.0)
    if eq > 0:
        max_pos = max(int(cfg.get("max_positions", 10) or 10), 1)
        cap_single = eq * float(cfg["max_notional_pct"])
        cap_even = eq * lev * 0.80 / max_pos
        per_budget = min(cap_single, cap_even) * float(cfg["budget_safety"])
    else:
        per_budget = float("inf")
    # 24h涨跌幅甜区钟形：甜区内满分，死水/末端趋0（不再奖励暴涨暴跌，避免选到反转末端）
    sweet_center = (float(cfg["sweet_pct_lo"]) + float(cfg["sweet_pct_hi"])) * 0.5
    sweet_half = max(float(cfg["end_pct"]) - sweet_center, 1e-9)
    pool = []
    for t in tickers:
        sym = t["symbol"]
        if sym in black:
            continue
        last = float(t["last"]); bid = float(t["bid"]); ask = float(t["ask"]); qv = float(t["quote_volume"]); pct = float(t["pct"])
        # 成交额
        if qv < float(cfg["min_turnover_usdt"]):
            continue
        # 涨跌幅区间（有波动但不妖）
        abs_pct = abs(pct)
        if abs_pct < float(cfg["min_abs_pct"]) or abs_pct > float(cfg["max_abs_pct"]):
            continue
        # 点差
        spread_bps = abs(ask - bid) / max((ask + bid) * 0.5, 1e-12) * 1e4 if bid > 0 and ask > 0 else -1.0
        if spread_bps >= 0 and spread_bps > float(cfg["max_spread_bps"]):
            continue
        # 买得起至少1张（读本地markets缓存，不发请求）
        spec = okx_client.market_spec(sym)
        min_notional = float(spec.get("min_notional") or 0)
        if min_notional > 0 and per_budget < min_notional:
            continue
        # 粗筛分：成交额流动性(0.70) + 健康波动甜区(0.30)；流动性主导，不再按涨跌幅大小线性奖励
        turn_score = float(np.clip(math.log10(max(qv, 1.0)) / math.log10(5e9), 0, 1))
        vol_fit = _bell(abs_pct, sweet_center, sweet_half)
        score = 0.70 * turn_score + 0.30 * vol_fit
        pool.append({"symbol": sym, "last": last, "quote_volume": qv, "pct": pct,
                     "spread_bps": spread_bps, "coarse_score": score, "vol_fit": round(vol_fit, 3)})
    pool.sort(key=lambda x: x["coarse_score"], reverse=True)
    return pool[: int(cfg["coarse_top"])]


# ---------------- L1 观察池精筛 ----------------
def _refine_one(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    cfg = U["cfg"]
    sym = item["symbol"]
    frames = _fetch_frames(sym)
    if frames is None:
        return None
    ctx = _directional_context(frames["4h"], frames["1h"], frames["15m"])
    direction = int(ctx.get("direction", 0))
    agreement = float(ctx.get("agreement", 0.0))
    if direction == 0 or agreement < float(cfg["min_agreement"]):
        return None

    # 1) 4H大级别强逆向硬剔除——与实盘信号层 _build_decision 的硬过滤阈值(0.35)完全对齐，
    #    选币阶段就不把“实盘必拦”的逆大级别币放进观察池。
    s4 = ctx.get("4h") or {}
    s4_bias = int(s4.get("bias", 0) or 0)
    s4_strength = float(s4.get("strength", 0.0) or 0.0)
    if s4_bias and s4_bias != direction and s4_strength >= float(cfg["fourh_reverse_strength"]):
        return None

    f15 = frames["15m"]; c = f15["close"]; h = f15["high"]; l = f15["low"]
    # 2) 波动适中：近20根中位振幅/价格（硬区间）
    rng = np.maximum(h[-20:] - l[-20:], 1e-12)
    atr_pct = float(np.median(rng) / max(float(c[-1]), 1e-12))
    if atr_pct < float(cfg["vol_low"]) or atr_pct > float(cfg["vol_high"]):
        return None
    # 3) 有结构：高低摆动点数量
    hs, ls = _swings(f15)
    swing_n = len(hs) + len(ls)
    if swing_n < int(cfg["min_swing"]):
        return None
    # 4) 位置硬门：做多不在高位追、做空不在低位追
    loc = _price_location(f15); pos = float(loc.get("position", 0.5))
    if direction > 0 and pos > 0.68:
        return None
    if direction < 0 and pos < 0.32:
        return None
    # 5) 末端硬剔除：现价相对15m均线乖离过大=已连续单边延伸，追进去最容易被反转扫损
    ext = _extension_atr(f15)
    ext_limit = float(cfg["extension_atr"])
    if (direction > 0 and ext > ext_limit) or (direction < 0 and ext < -ext_limit):
        return None

    # ---- 多维打分（越符合“有方向 + 回踩中位 + 动能未衰竭 + 结构清晰”越高）----
    # 趋势健康：方向一致度为主；4H同向微调，4H逆向但未达硬阈值则降分
    trend = agreement
    if s4_bias == direction:
        trend = min(1.0, agreement + 0.05)
    elif s4_bias and s4_bias != direction:
        trend = max(0.0, agreement - 0.10)
    trend = float(np.clip(trend, 0, 1))
    # 回踩位置：做多偏好区间中下沿(回踩支撑)、做空偏好中上沿(回踩阻力)，钟形
    if direction > 0:
        pullback = _bell(pos, 0.42, 0.30)
        fresh = _bell(ext, 0.30, ext_limit)
    else:
        pullback = _bell(pos, 0.58, 0.30)
        fresh = _bell(ext, -0.30, ext_limit)
    # 结构清晰：摆动点数量 + 15m结构同向
    s15 = ctx.get("15m") or {}
    b15 = int(s15.get("bias", 0) or 0); st15 = float(s15.get("strength", 0.0) or 0.0)
    if (direction > 0 and b15 > 0) or (direction < 0 and b15 < 0):
        align_struct = 0.5 + 0.5 * st15
    elif b15 == 0:
        align_struct = 0.40
    else:
        align_struct = 0.15
    struct = 0.6 * min(swing_n / 10.0, 1.0) + 0.4 * align_struct
    # 波动适配：以甜区1.2%为中心钟形（硬区间已切，这里只排序）
    vol_fit = _bell(atr_pct, float(cfg["vol_sweet"]),
                    (float(cfg["vol_high"]) - float(cfg["vol_low"])) / 2.0)

    score = float(np.clip(
        0.26 * trend + 0.28 * pullback + 0.24 * fresh + 0.14 * struct + 0.08 * vol_fit, 0, 1))
    side = "LONG" if direction > 0 else "SHORT"
    fourh_txt = "4H同向" if s4_bias == direction else ("4H中性" if not s4_bias else "4H轻微逆向")
    reasons = [
        f"{ctx.get('direction_source','方向')}·一致度{agreement:.2f}（{fourh_txt}）",
        f"回踩位置{pos:.2f}({'回踩中下位有利做多' if direction>0 else '回踩中上位有利做空'})",
        f"均线乖离{ext:+.1f}ATR({'未延伸·非末端' if abs(ext)<1.0 else '略有延伸'})",
        f"15m波幅{atr_pct*100:.2f}%·结构点{swing_n}",
        f"24h成交额{item['quote_volume']/1e8:.2f}亿U",
    ]
    return {"symbol": sym, "side": side, "score": round(score, 4),
            "reasons": reasons, "pct": item.get("pct", 0.0), "quote_volume": item["quote_volume"],
            "pos": round(pos, 3), "ext_atr": round(ext, 2), "atr_pct": round(atr_pct, 4)}


def _refine_one_v4_range(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """v4 区间均值回归口味的 L1 精筛（只改排名，不放松 L0 的流动性/点差/买得起硬门）。

    与旧趋势精筛的区别：不再要求高周期方向一致（区间里本就没有方向）、不做"追高杀低/末端"
    硬剔除（均值回归恰恰要在极端处反向做）。排名偏好：①处于区间regime；②15m已实现波动在
    "够覆盖成本但不疯狂"的甜区；③价格正走到布林/RSI 极端（临近回归机会）。趋势regime的币
    不是被删除而是排到最后（regime会轮动，且持仓币永远保留）。
    """
    cfg = U["cfg"]
    sym = item["symbol"]
    frames = _fetch_frames(sym)
    if frames is None:
        return None
    reg = _v4_regime(frames)
    regime = int(reg.get("regime", 9))
    f15 = frames["15m"]; c = f15["close"]; h = f15["high"]; l = f15["low"]
    if len(c) < 25:
        return None
    # 波动硬区间（与旧精筛一致）：剔除死水与疯狂波动，这道安全门保留
    rng = np.maximum(h[-20:] - l[-20:], 1e-12)
    atr_pct = float(np.median(rng) / max(float(c[-1]), 1e-12))
    # 波动硬区间：v4 用选币v2抬高后的下限 vol_low_v4，v3 仍用 vol_low
    vol_floor = float(cfg.get("vol_low_v4", cfg["vol_low"]))
    if atr_pct < vol_floor or atr_pct > float(cfg["vol_high"]):
        return None
    px = float(c[-1])
    # ① regime 适配：区间满分、中性过渡给部分分、强趋势近乎0（不硬删，便于轮动补位）
    range_fit = {0: 1.0, 9: 0.45, 1: 0.05, -1: 0.05}.get(regime, 0.2)
    # ② 波动甜区（钟形，仅排序）
    vol_fit = _bell(atr_pct, 0.009, 0.007)
    # ③ 临近回归机会：15m 布林拉伸 + RSI2 极端（选币层用15m，避免为每个候选多拉一根5m）
    bb = _bollinger(f15, int(cfg.get("range_bb_n", 20)), 2.0)
    rsi = float(_rsi_arr(c, 2)[-1])
    stretch = min(abs(px - bb["mid"]) / max(2.0 * bb["sd"], 1e-12), 1.3) / 1.3 if bb.get("ok") else 0.0
    rsi_ext = abs(rsi - 50.0) / 50.0
    opp = 0.6 * stretch + 0.4 * rsi_ext
    # 流动性仅作极小的并列微调（L0 已按成交额/点差硬筛+排序）
    liq_tie = 0.02 * float(np.clip(float(item.get("coarse_score", 0.5)), 0, 1))
    score = float(np.clip(0.45 * range_fit + 0.25 * vol_fit + 0.30 * opp + liq_tie, 0, 1))
    # 观察池方向只用于多/空补位均衡：按当前极端方向预判下一笔回归方向
    if (bb.get("ok") and px <= bb["dn"]) or rsi <= 30:
        side = "LONG"
    elif (bb.get("ok") and px >= bb["up"]) or rsi >= 70:
        side = "SHORT"
    else:
        ctx = _directional_context(frames["4h"], frames["1h"], frames["15m"])
        side = "LONG" if int(ctx.get("direction", 1)) >= 0 else "SHORT"
    loc = _price_location(f15); pos = float(loc.get("position", 0.5))
    ext = _extension_atr(f15)
    regime_txt = {0: "区间(可高抛低吸)", 9: "中性过渡", 1: "多头趋势(回归单少做)", -1: "空头趋势(回归单少做)"}.get(regime, "?")
    reasons = [
        f"v4regime={regime_txt}·ER15={reg.get('er15')}/ER1H={reg.get('er1h')}",
        f"15m波幅{atr_pct*100:.2f}%·布林拉伸{stretch:.2f}·RSI2={rsi:.0f}",
        f"位置{pos:.2f}·预判{('低吸做多' if side=='LONG' else '高抛做空')}",
        f"24h成交额{item['quote_volume']/1e8:.2f}亿U·点差{item.get('spread_bps',-1):.1f}bps",
    ]
    return {"symbol": sym, "side": side, "score": round(score, 4),
            "reasons": reasons, "pct": item.get("pct", 0.0), "quote_volume": item["quote_volume"],
            "pos": round(pos, 3), "ext_atr": round(ext, 2), "atr_pct": round(atr_pct, 4)}


def refine_watchlist() -> List[Dict[str, Any]]:
    cfg = U["cfg"]
    now = time.time()
    passed = []
    # v4 主力是“区间均值回归”（趋势延续 sleeve 默认关）：选币排名改用区间口味；
    # 若手动开回趋势 sleeve（FAST_V4_TREND_SLEEVE=True）或回退 v3，则用原趋势排名。
    # 动态读取运行时引擎开关（页面可切），不能用 import 时的副本：
    use_range_rank = bool(getattr(_afm, "FAST_V4_ENSEMBLE", True) and not getattr(_afm, "FAST_V4_TREND_SLEEVE", False))
    refine_fn = _refine_one_v4_range if use_range_rank else _refine_one
    for item in U["coarse_pool"]:
        r = refine_fn(item)
        if r:
            passed.append(r)
    passed.sort(key=lambda x: x["score"], reverse=True)
    passed_map = {r["symbol"]: r for r in passed}

    old = {w["symbol"]: w for w in U["watchlist"]}
    new_watch: List[Dict[str, Any]] = []
    # 冷却表清理
    with LOCK:
        U["cooldown"] = {s: t for s, t in U["cooldown"].items() if t > now}
        cool = dict(U["cooldown"])

    # 已在观察池：达标则保留并清退出计数，不达标累计退出计数，达exit_hyst移出+冷却
    keep_syms = set()
    enter_track = dict(U.get("enter_track", {}))
    for sym, w in old.items():
        if sym in passed_map:
            w["enter_cnt"] = int(w.get("enter_cnt", 1)) + 1
            w["exit_cnt"] = 0
            w.update({k: passed_map[sym][k] for k in ("side", "score", "reasons")})
            keep_syms.add(sym)
            enter_track[sym] = int(cfg["enter_hyst"])  # 已在池，连续达标记满
        else:
            w["exit_cnt"] = int(w.get("exit_cnt", 0)) + 1
            if w["exit_cnt"] < int(cfg["exit_hyst"]):
                keep_syms.add(sym)  # 滞回，暂留
            else:
                with LOCK:
                    U["cooldown"][sym] = now + int(cfg["cooldown_sec"])
                enter_track.pop(sym, None)
                _log("移出观察池", sym, "连续精筛不达标，进入15分钟冷却")
    # 新进入：连续达标计数独立持久化，达enter_hyst才进
    for r in passed:
        sym = r["symbol"]
        if sym in keep_syms or sym in cool:
            continue
        ent = int(enter_track.get(sym, 0)) + 1
        enter_track[sym] = ent
        r["enter_cnt"] = ent
        r["exit_cnt"] = 0
        if ent >= int(cfg["enter_hyst"]):
            keep_syms.add(sym)
            _log("选入观察池", sym, "；".join(r["reasons"]))
    # 本轮没达标的非观察池候选，连续计数清零（必须“连续”达标）
    for sym in list(enter_track.keys()):
        if sym not in passed_map and sym not in old:
            enter_track.pop(sym, None)
    with LOCK:
        U["enter_track"] = enter_track
    # 组装并按分排序，截断到watchlist_size
    cand = []
    for sym in keep_syms:
        if sym in passed_map:
            w = passed_map[sym]; w.setdefault("enter_cnt", enter_track.get(sym, int(cfg["enter_hyst"]))); w.setdefault("exit_cnt", 0)
            cand.append(w)
        elif sym in old:
            cand.append(old[sym])  # 滞回暂留的沿用旧数据
    cand.sort(key=lambda x: x["score"], reverse=True)
    cand = cand[: int(cfg["watchlist_size"])]
    with LOCK:
        U["watchlist"] = cand
    return cand


# ---------------- 时间片调度（主循环每轮调用，很轻） ----------------
def maybe_update(held_symbols: List[str], equity: float, leverage: float) -> List[str]:
    """按时间片跑L0/L1，并解析当前交易池。返回本轮应扫描的内部符号列表。"""
    cfg = U["cfg"]
    if not cfg.get("enabled"):
        # 关闭时直接用白名单（=手选币），保持原行为
        with LOCK:
            U["trade_set"] = list(cfg.get("whitelist") or [])
            return list(U["trade_set"])
    now = time.time()
    try:
        if now - U["last_coarse"] >= int(cfg["coarse_interval"]) or not U["coarse_pool"]:
            U["coarse_pool"] = coarse_scan(equity, leverage)
            U["last_coarse"] = now
            U["last_msg"] = f"L0粗筛：扫描{U['scanned']}个USDT永续，保留{len(U['coarse_pool'])}个"
            logger.info(f"[自动选币] {U['last_msg']}")
        if U["coarse_pool"] and (now - U["last_refine"] >= int(cfg["refine_interval"]) or not U["watchlist"]):
            U["watchlist"] = refine_watchlist()
            U["last_refine"] = now
            U["last_msg"] = f"L1精筛：观察池{len(U['watchlist'])}个"
    except Exception as exc:
        logger.warning(f"[自动选币] 选币轮询异常，沿用上一次结果：{exc}")
    return resolve_trade_set(held_symbols)


def resolve_trade_set(held_symbols: List[str]) -> List[str]:
    """交易池 = 持仓币(强制) + 白名单(强制) + 观察池补位；做同方向均衡与上限。"""
    cfg = U["cfg"]
    held = [str(s).upper() for s in (held_symbols or [])]
    white = list(cfg.get("whitelist") or [])
    max_pos = int(cfg.get("max_positions", 10))
    pool_target = max_pos + int(cfg.get("trade_pool_buffer", 5))
    same_cap = max(1, int(math.ceil(max_pos * float(cfg.get("same_side_ratio", 0.8)))))

    watch = sorted(U["watchlist"], key=lambda x: x["score"], reverse=True)
    side_of = {w["symbol"]: w["side"] for w in watch}
    ordered: List[str] = []
    seen = set()

    def add(sym):
        sym = str(sym).upper()
        if sym and sym not in seen:
            seen.add(sym); ordered.append(sym)

    # 1) 持仓 + 白名单 永远在最前
    for s in held:
        add(s)
    for s in white:
        add(s)
    # 2) 观察池补位，做多/做空轮流补以保持方向均衡，并用同方向上限约束
    longs = [w["symbol"] for w in watch if w["side"] == "LONG"]
    shorts = [w["symbol"] for w in watch if w["side"] == "SHORT"]
    li = si = 0

    def side_count(side):
        return sum(1 for s in ordered if side_of.get(s) == side) + \
               sum(1 for s in held if side_of.get(s) == side)

    while len(ordered) < pool_target and (li < len(longs) or si < len(shorts)):
        # 优先补当前更少的方向
        pick_long = side_count("LONG") <= side_count("SHORT")
        chosen = None
        if pick_long and li < len(longs):
            chosen = longs[li]; li += 1
        elif si < len(shorts):
            chosen = shorts[si]; si += 1
        elif li < len(longs):
            chosen = longs[li]; li += 1
        if not chosen:
            break
        sd = side_of.get(chosen)
        if sd and side_count(sd) >= same_cap:
            # 该方向已达上限：尝试另一侧
            other = shorts[si] if sd == "LONG" and si < len(shorts) else (longs[li] if sd == "SHORT" and li < len(longs) else None)
            if other:
                if sd == "LONG":
                    si += 1
                else:
                    li += 1
                chosen = other
            else:
                continue
        add(chosen)
    with LOCK:
        U["trade_set"] = ordered
    return ordered


def snapshot() -> Dict[str, Any]:
    """给前端状态页：扫描数/观察池/交易池/选入淘汰日志。"""
    with LOCK:
        return {
            "enabled": bool(U["cfg"].get("enabled")),
            "scanned": U["scanned"],
            "coarse_count": len(U["coarse_pool"]),
            "watchlist": [dict(w) for w in U["watchlist"]],
            "trade_set": list(U["trade_set"]),
            "last_msg": U["last_msg"],
            "history": list(U["history"][-30:]),
            "cfg": {k: v for k, v in U["cfg"].items() if k not in ("whitelist", "blacklist")},
        }
