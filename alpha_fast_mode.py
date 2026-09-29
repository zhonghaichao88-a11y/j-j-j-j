"""ALPHA-X FAST：日内裸K独立策略。

职责边界：
1. 只负责用真实、已完成的 4H/1H/15m/5m OHLC + ticker 判断日内方向和入场。
2. 自己计算本次 FAST 交易的结构型 TP/SL 与盈亏比。
3. 不训练模型，不读取 SHORT/MID/LONG/MULTI 模型。
4. 不在持仓后重新改写 TP/SL；下单后由 alpha_engine / alpha_live 的原有执行、保护、持仓管理链负责执行。
5. 不使用技术指标作为主决策依据；EMA、订单流、L2、资金费率等不参与 FAST 核心入场判定。
"""
from __future__ import annotations
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple
import numpy as np
from loguru import logger

from config import config
from okx_client import okx_client

STATE: Dict[str, Dict[str, Any]] = {}
DATA_CACHE: Dict[str, Dict[str, Any]] = {}
PULLBACK_STATE: Dict[str, Dict[str, Any]] = {}
MARKET_SYNC_STATE: Dict[str, Dict[str, Any]] = {}
MARKET_BENCHMARK: Dict[str, Any] = {"return_72h": 0.0, "symbols": 0, "updated_at": 0.0}
# V6.3 market-state freshness: True only when THIS scan round built it successfully.
# It is reset to False when the build fails so signals never reuse a stale regime.
MARKET_BENCHMARK_READY: bool = False

# FAST 核心只需要价格行为数据；衍生品/盘口数据保留为兼容性状态信息，但不参与核心方向评分。
CORE_SOURCES = ("ticker", "ohlcv_5m", "ohlcv_15m", "ohlcv_1h", "ohlcv_4h")

FRAME_TTL = {"5m": 1.5, "15m": 3.0, "1h": 20.0, "4h": 90.0}
FRAME_LIMIT = {"5m": 120, "15m": 120, "1h": 100, "4h": 80}


def _finite(v, default=0.0):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _bar_frame(rows: List[List[float]], timeframe: str, limit=None) -> Dict[str, np.ndarray]:
    if not rows:
        return {}
    arr = np.asarray(rows, dtype=float)
    if arr.ndim != 2 or arr.shape[1] < 6:
        return {}
    minutes = {"5m": 5, "15m": 15, "1h": 60, "4h": 240}.get(timeframe)
    if not minutes:
        return {}
    now_ms = time.time() * 1000.0
    # 只允许已经完整收盘的K线，严禁把当前未收盘K线拿来做方向/入场判断。
    complete = (arr[:, 0] + minutes * 60_000) <= now_ms + 1000.0
    arr = arr[complete]
    if len(arr) == 0:
        return {}
    # 防重复、排序，确保时间顺序稳定。
    arr = arr[np.argsort(arr[:, 0])]
    cap=int(limit or FRAME_LIMIT[timeframe])
    if len(arr) > cap:
        arr = arr[-cap:]
    return {"ts": arr[:, 0], "open": arr[:, 1], "high": arr[:, 2], "low": arr[:, 3], "close": arr[:, 4], "volume": arr[:, 5]}


def _cached_fetch(cache_key: str, ttl: float, fetch_fn):
    now = time.time()
    cached = DATA_CACHE.get(cache_key)
    if cached and now - float(cached.get("ts", 0)) < ttl:
        return cached.get("value")
    value = fetch_fn()
    DATA_CACHE[cache_key] = {"ts": now, "value": value}
    return value


# Fixed major-coin basket used ONLY to widen the regime sample when the trade
# pool holds fewer than four symbols; these never enter the actual trade pool.
BENCHMARK_REFERENCE_SYMBOLS = ["BTC","ETH","SOL","XRP","DOGE","ADA","AVAX","LINK","LTC","DOT","UNI","ATOM","NEAR"]


def invalidate_market_benchmark() -> None:
    """Mark this scan round's market regime invalid; signals then wait for a fresh build."""
    global MARKET_BENCHMARK_READY
    MARKET_BENCHMARK_READY=False


def prepare_market_benchmark(symbols: List[str]) -> Dict[str, Any]:
    """Build the V6.3 market regime from completed 5m bars over an exact 72h window.

    Uses the same 5m, exact-864-bar (72h) geometric equal-weight definition as the
    replay benchmark so live and replay classifications match.  When the trade pool
    holds fewer than four symbols, a fixed major-coin reference basket is added for
    the regime calculation only (it never enters the trade pool).  A failed symbol
    is skipped; fewer than four valid symbols is explicitly unavailable instead of
    silently substituting zero.
    """
    if not okx_client.is_connected or not getattr(okx_client, "_exchange", None):
        raise RuntimeError("OKX尚未连接，无法计算全市场状态")
    # Trade-pool symbols first, then a de-duplicated reference basket to reach >=4.
    bench_symbols=list(dict.fromkeys(list(symbols)+BENCHMARK_REFERENCE_SYMBOLS))
    def one(symbol):
        cs=config.trading.get_ccxt_symbol(symbol)
        # 865 completed 5m bars => the last close vs the close 864 bars (exact 72h) earlier.
        rows=okx_client.get_ohlcv(symbol=cs,timeframe="5m",limit=865)
        close=np.asarray([float(r[4]) for r in (rows or [])],float)
        if len(close)<865 or close[-865]<=0 or close[-1]<=0:return None
        return float(np.log(close[-1]/close[-865]))
    values=[]
    with ThreadPoolExecutor(max_workers=min(6,max(1,len(bench_symbols)))) as pool:
        futures=[pool.submit(one,s) for s in bench_symbols]
        for future in as_completed(futures):
            try:
                value=future.result()
                if value is not None and math.isfinite(value):values.append(value)
            except Exception as exc:
                logger.warning(f"V6.3全市场状态数据跳过：{exc}")
    if len(values)<4:
        raise RuntimeError(f"全市场状态有效币种不足4个（当前{len(values)}个）")
    MARKET_BENCHMARK.update(return_72h=float(math.exp(float(np.mean(values)))-1.0),symbols=len(values),updated_at=time.time())
    global MARKET_BENCHMARK_READY
    MARKET_BENCHMARK_READY=True
    return dict(MARKET_BENCHMARK)


def _last(frame: Dict[str, np.ndarray]) -> float:
    c = frame.get("close", np.array([]))
    return float(c[-1]) if len(c) else 0.0


def _swings(frame: Dict[str, np.ndarray], left: int = 2, right: int = 2) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
    """稳健识别裸K摆动点：只使用已完成K线，避免平台小噪声连续制造HH/HL。"""
    h = frame.get("high", np.array([])); l = frame.get("low", np.array([]))
    if len(h) < left + right + 5:
        return [], []
    highs: List[Tuple[int, float]] = []; lows: List[Tuple[int, float]] = []
    min_sep = max(left + right, 3)
    for i in range(left, len(h) - right):
        hw = h[i-left:i+right+1]; lw = l[i-left:i+right+1]
        hi = float(h[i]); lo = float(l[i])
        # 严格极值：平台相等高低点不直接当成新的结构点。
        if hi > float(np.max(np.delete(hw, left))):
            if not highs or i - highs[-1][0] >= min_sep:
                highs.append((i, hi))
            elif hi > highs[-1][1]:
                highs[-1] = (i, hi)
        if lo < float(np.min(np.delete(lw, left))):
            if not lows or i - lows[-1][0] >= min_sep:
                lows.append((i, lo))
            elif lo < lows[-1][1]:
                lows[-1] = (i, lo)
    return highs, lows


def _structure(frame: Dict[str, np.ndarray]) -> Dict[str, Any]:
    highs, lows = _swings(frame)
    h = frame.get("high", np.array([])); l = frame.get("low", np.array([])); c = frame.get("close", np.array([]))
    if len(highs) < 2 or len(lows) < 2 or len(c) < 12:
        return {"bias": 0, "label": "结构不足", "hh": False, "hl": False, "lh": False, "ll": False, "last_high": 0.0, "last_low": 0.0, "prev_high": 0.0, "prev_low": 0.0, "strength": 0.0}
    h1, h2 = highs[-1][1], highs[-2][1]
    l1, l2 = lows[-1][1], lows[-2][1]
    median_range = float(np.median(np.maximum(h[-min(len(h), 30):] - l[-min(len(l), 30):], 1e-12)))
    price = max(abs(float(c[-1])), 1e-12)
    min_move = max(median_range * 0.12, price * 0.00035)
    hh, hl = (h1 - h2) > min_move, (l1 - l2) > min_move
    lh, ll = (h2 - h1) > min_move, (l2 - l1) > min_move
    if hh and hl:
        bias, label = 1, "多头HH-HL"
    elif lh and ll:
        bias, label = -1, "空头LH-LL"
    elif hh and not ll:
        bias, label = 1, "高点抬高·结构偏多"
    elif ll and not hh:
        bias, label = -1, "低点下移·结构偏空"
    elif lh and not hl:
        bias, label = -1, "高点下移·结构偏空"
    elif hl and not lh:
        bias, label = 1, "低点抬高·结构偏多"
    else:
        bias, label = 0, "震荡/过渡结构"
    strength = float(np.clip(max(abs(h1-h2), abs(l1-l2)) / max(median_range, 1e-12), 0.0, 3.0) / 3.0)
    return {"bias": bias, "label": label, "hh": hh, "hl": hl, "lh": lh, "ll": ll, "last_high": h1, "last_low": l1, "prev_high": h2, "prev_low": l2, "strength": strength, "last_high_index": highs[-1][0], "prev_high_index": highs[-2][0], "last_low_index": lows[-1][0], "prev_low_index": lows[-2][0]}


def _bos_choch(frame: Dict[str, np.ndarray], structure: Dict[str, Any]) -> Dict[str, Any]:
    """只认收盘价有效突破结构摆动点；取消容易误报的固定6根局部突破冒充BOS。"""
    c = frame.get("close", np.array([])); h = frame.get("high", np.array([])); l = frame.get("low", np.array([]))
    if len(c) < 12:
        return {"bos": 0.0, "choch": 0.0, "event": "无", "break_price": 0.0, "buffer": 0.0}
    hi = float(structure.get("last_high") or 0); lo = float(structure.get("last_low") or 0)
    px = float(c[-1])
    ranges = np.maximum(h[-min(len(h), 30):] - l[-min(len(l), 30):], 1e-12)
    buffer = float(np.median(ranges) * 0.08)
    bos = 0.0; choch = 0.0; event = "无"; break_price = 0.0
    if hi > 0 and px > hi + buffer:
        bos = 1.0; break_price = hi; event = "多头BOS"
        if int(structure.get("bias", 0)) < 0:
            choch = 1.0; event = "空转多CHOCH"
    elif lo > 0 and px < lo - buffer:
        bos = -1.0; break_price = lo; event = "空头BOS"
        if int(structure.get("bias", 0)) > 0:
            choch = -1.0; event = "多转空CHOCH"
    return {"bos": bos, "choch": choch, "event": event, "break_price": break_price, "buffer": buffer}


def _n_pattern(frame: Dict[str, np.ndarray]) -> Dict[str, Any]:
    """按时间顺序验证N字：必须是交替的回撤→推进结构，避免只比较最后两个高低点。"""
    highs, lows = _swings(frame)
    if len(highs) < 2 or len(lows) < 2:
        return {"score": 0.0, "label": "无明显N字"}
    pivots = [(i, v, "H") for i, v in highs[-4:]] + [(i, v, "L") for i, v in lows[-4:]]
    pivots.sort(key=lambda x: x[0])
    compressed = []
    for p in pivots:
        if compressed and compressed[-1][2] == p[2]:
            if (p[2] == "H" and p[1] > compressed[-1][1]) or (p[2] == "L" and p[1] < compressed[-1][1]):
                compressed[-1] = p
        else:
            compressed.append(p)
    if len(compressed) < 4:
        return {"score": 0.0, "label": "N字未完成"}
    seq = compressed[-4:]
    types = [p[2] for p in seq]
    if types == ["L", "H", "L", "H"] and seq[2][1] > seq[0][1] and seq[3][1] > seq[1][1]:
        return {"score": 1.0, "label": "多头N字推进", "pivots": seq}
    if types == ["H", "L", "H", "L"] and seq[2][1] < seq[0][1] and seq[3][1] < seq[1][1]:
        return {"score": -1.0, "label": "空头N字推进", "pivots": seq}
    return {"score": 0.0, "label": "N字未完成", "pivots": seq}


def _fvg(frame: Dict[str, np.ndarray]) -> Dict[str, Any]:
    """三K FVG：要求真实价格缺口，并判断当前是否仍在有效区间。"""
    h = frame.get("high", np.array([])); l = frame.get("low", np.array([])); c = frame.get("close", np.array([]))
    if len(c) < 5:
        return {"score": 0.0, "label": "无FVG", "low": 0.0, "high": 0.0, "direction": 0}
    ranges = np.maximum(h[-min(len(h), 30):] - l[-min(len(l), 30):], 1e-12)
    min_gap = max(float(np.median(ranges)) * 0.05, float(abs(c[-1])) * 0.0002)
    found = None
    for i in range(2, len(c)):
        gap_up = float(l[i]) - float(h[i-2])
        gap_dn = float(l[i-2]) - float(h[i])
        if gap_up >= min_gap:
            found = (1, float(h[i-2]), float(l[i]), i)
        elif gap_dn >= min_gap:
            found = (-1, float(h[i]), float(l[i-2]), i)
    if not found:
        return {"score": 0.0, "label": "无有效FVG", "low": 0.0, "high": 0.0, "direction": 0}
    d, low, high, idx = found
    px = float(c[-1]); inside = low <= px <= high
    # 已被完整穿越的缺口不再当作有效入场证据。
    if d > 0 and px < low:
        return {"score": 0.0, "label": "多头FVG已失效", "low": low, "high": high, "direction": d, "index": idx}
    if d < 0 and px > high:
        return {"score": 0.0, "label": "空头FVG已失效", "low": low, "high": high, "direction": d, "index": idx}
    # FVG 不是永久证据：越老、离当前价格越远，权重越低；彻底过期则不参与入场评分。
    age_bars=max(0, len(c)-1-idx)
    zone_distance=0.0 if inside else min(min(abs(px-low),abs(px-high))/max(abs(px),1e-12),1.0)
    age_factor=float(np.clip(1.0-age_bars/18.0,0.0,1.0))
    distance_factor=float(np.clip(1.0-zone_distance/0.015,0.0,1.0))
    relevance=age_factor*distance_factor
    if age_bars>18 or zone_distance>=0.015 or relevance<=0.0:
        return {"score":0.0,"label":("多头" if d>0 else "空头")+"FVG过期/距离过远","low":low,"high":high,"direction":d,"index":idx,"inside":inside,"age_bars":age_bars,"distance":zone_distance}
    base=1.0 if inside else 0.55
    score=float(d*base*(0.35+0.65*relevance))
    label=("多头FVG" if d>0 else "空头FVG") + ("正在回补" if inside else "有效延伸")
    return {"score":score,"label":label,"low":low,"high":high,"direction":d,"index":idx,"inside":inside,"age_bars":age_bars,"distance":zone_distance}


def _liquidity_sweep(frame: Dict[str, np.ndarray]) -> Dict[str, Any]:
    h = frame.get("high", np.array([])); l = frame.get("low", np.array([])); c = frame.get("close", np.array([]))
    if len(c) < 12:
        return {"score": 0.0, "label": "无扫盘", "sweep_high": 0.0, "sweep_low": 0.0}
    prior_hi = float(np.max(h[-11:-1])); prior_lo = float(np.min(l[-11:-1])); last_h = float(h[-1]); last_l = float(l[-1]); px = float(c[-1])
    rng = max(last_h-last_l, 1e-12); body = abs(float(c[-1]-frame.get("open",np.array([0]))[-1]))
    if last_l < prior_lo and px > prior_lo and (px-last_l)/rng >= 0.45:
        score=float(np.clip((px-last_l)/rng,0,1))
        return {"score":score,"label":"下扫流动性后收回","sweep_high":0.0,"sweep_low":last_l}
    if last_h > prior_hi and px < prior_hi and (last_h-px)/rng >= 0.45:
        score=-float(np.clip((last_h-px)/rng,0,1))
        return {"score":score,"label":"上扫流动性后收回","sweep_high":last_h,"sweep_low":0.0}
    return {"score":0.0,"label":"无明显扫盘","sweep_high":0.0,"sweep_low":0.0}


def _order_block(frame: Dict[str, np.ndarray]) -> Dict[str, Any]:
    o = frame.get("open", np.array([])); c = frame.get("close", np.array([])); h = frame.get("high", np.array([])); l = frame.get("low", np.array([]))
    if len(c) < 8:
        return {"score": 0.0, "label": "无明显OB", "low": 0.0, "high": 0.0}
    # 最近一次明显位移前的反向K作为候选OB；这里只做结构标记，不使用成交量指标。
    for i in range(len(c)-2, max(1, len(c)-10), -1):
        body = abs(float(c[i+1]) - float(o[i+1])) / max(abs(float(c[i+1])), 1e-12)
        if body >= 0.003:
            if float(c[i+1]) > float(o[i+1]) and float(c[i]) < float(o[i]):
                low_i, high_i = float(l[i]), float(h[i])
                px=float(c[-1]); inside=low_i<=px<=high_i
                distance=0.0 if inside else min(min(abs(px-low_i),abs(px-high_i))/max(abs(px),1e-12),1.0)
                # OB 本身只从最近约8根K寻找，重点防止价格已经远离该区域仍被当作强证据。
                if px < low_i: return {"score":0.0,"label":"多头Order Block已失效","low":low_i,"high":high_i,"index":i,"inside":False,"distance":distance}
                relevance=float(np.clip(1.0-distance/0.015,0.0,1.0))
                score=0.75*(1.0 if inside else (0.35+0.65*relevance))
                return {"score":score,"label":"多头Order Block","low":low_i,"high":high_i,"index":i,"inside":inside,"distance":distance}
            if float(c[i+1]) < float(o[i+1]) and float(c[i]) > float(o[i]):
                low_i, high_i = float(l[i]), float(h[i])
                px=float(c[-1]); inside=low_i<=px<=high_i
                distance=0.0 if inside else min(min(abs(px-low_i),abs(px-high_i))/max(abs(px),1e-12),1.0)
                if px > high_i: return {"score":0.0,"label":"空头Order Block已失效","low":low_i,"high":high_i,"index":i,"inside":False,"distance":distance}
                relevance=float(np.clip(1.0-distance/0.015,0.0,1.0))
                score=-0.75*(1.0 if inside else (0.35+0.65*relevance))
                return {"score":score,"label":"空头Order Block","low":low_i,"high":high_i,"index":i,"inside":inside,"distance":distance}
    return {"score": 0.0, "label": "无明显OB", "low": 0.0, "high": 0.0}


def _breakout_pullback_reclaim(frame: Dict[str, np.ndarray]) -> Dict[str, Any]:
    o = frame.get("open", np.array([])); h = frame.get("high", np.array([])); l = frame.get("low", np.array([])); c = frame.get("close", np.array([]))
    if len(c) < 15:
        return {"score": 0.0, "label": "无完整突破回踩"}
    prior_hi = float(np.max(h[-12:-4])); prior_lo = float(np.min(l[-12:-4]));
    recent = c[-4:]
    if float(recent[0]) > prior_hi and float(np.min(l[-3:])) <= prior_hi and float(c[-1]) > prior_hi:
        return {"score": 1.0, "label": "突破→回踩→重新站回"}
    if float(recent[0]) < prior_lo and float(np.max(h[-3:])) >= prior_lo and float(c[-1]) < prior_lo:
        return {"score": -1.0, "label": "跌破→回踩→重新压回"}
    # 单纯突破也算触发，但强度低于完整回踩确认。
    if float(c[-1]) > prior_hi: return {"score": 0.65, "label": "多头突破"}
    if float(c[-1]) < prior_lo: return {"score": -0.65, "label": "空头突破"}
    return {"score": 0.0, "label": "等待突破/回踩确认"}



def _smart_pullback_state(symbol: str, frame5: Dict[str, np.ndarray], frame15: Dict[str, np.ndarray],
                          direction: int, px: float) -> Dict[str, Any]:
    """FAST持久化智能回踩状态机：突破后持续等待回踩，避免每轮扫描重新归零。"""
    h15=frame15.get("high",np.array([])); l15=frame15.get("low",np.array([])); c15=frame15.get("close",np.array([])); ts15=frame15.get("ts",np.array([]))
    h5=frame5.get("high",np.array([])); l5=frame5.get("low",np.array([])); c5=frame5.get("close",np.array([])); o5=frame5.get("open",np.array([]))
    if len(c15)<16 or len(c5)<8 or px<=0:
        return {"state":"WAIT","confirmed":False,"reason":"回踩数据不足","level":0.0,"score":0.0,"active":False}
    key=symbol or "__unknown__"; st=PULLBACK_STATE.get(key) or {}; current_bar=int(ts15[-1]) if len(ts15) else 0
    prior_hi=float(np.max(h15[-12:-4])); prior_lo=float(np.min(l15[-12:-4])); bp=_breakout_pullback_reclaim(frame15)
    new_breakout=(direction>0 and float(c15[-1])>prior_hi) or (direction<0 and float(c15[-1])<prior_lo)
    # 回踩参考点只在“新的已完成15m结构突破”时更新。普通插针/同一根K重复扫描不更新，
    # 防止参考点不断追着价格移动，也防止一直死等一个已经过时的旧点。
    if st and int(st.get("direction",0))!=direction:
        st={}
        PULLBACK_STATE.pop(key,None)
    if new_breakout and current_bar:
        old_level=float(st.get("level") or 0.0) if st else 0.0
        old_bar=int(st.get("breakout_bar",0)) if st else 0
        fresh_bar=current_bar != old_bar
        structural_move=abs((prior_hi if direction>0 else prior_lo)-old_level)/max(px,1e-12) if old_level>0 else 1.0
        # 同方向的新结构突破必须明显脱离旧参考点，并且只能由新完成K触发。
        valid_new_structure=bool(fresh_bar and (old_level<=0 or structural_move>=0.0015))
        if valid_new_structure:
            level=prior_hi if direction>0 else prior_lo
            extension=abs(px-level)/max(px,1e-12)
            if (not st and extension>=0.0025) or st:
                st={"direction":direction,"level":level,"breakout_bar":current_bar,
                    "created_at":time.time(),"bars_waited":0,"last_bar":current_bar}
                PULLBACK_STATE[key]=st
    if not st:
        return {"state":"NONE","confirmed":False,"reason":"未进入必须等待回踩的区域","level":0.0,"score":0.0,"active":False}
    level=float(st.get("level") or 0.0)
    if level<=0:
        PULLBACK_STATE.pop(key,None); return {"state":"NONE","confirmed":False,"reason":"回踩关键位无效，重置等待状态","level":0.0,"score":0.0,"active":False}
    if current_bar and int(st.get("last_bar",0))!=current_bar:
        st["bars_waited"]=int(st.get("bars_waited",0))+1; st["last_bar"]=current_bar
    # 最多等待8根15m完成K（约2小时），防止状态锁死数小时/数天。
    if int(st.get("bars_waited",0))>8 or time.time()-float(st.get("created_at",time.time()))>3*3600:
        PULLBACK_STATE.pop(key,None)
        return {"state":"EXPIRED","confirmed":False,"reason":"突破回踩等待超时，放弃追单并重新寻找新结构","level":level,"score":0.0,"active":False}
    near=abs(px-level)/max(px,1e-12)<=0.0035
    if direction>0:
        touched=bool(np.min(l5[-3:])<=level*1.0015); reclaimed=bool(float(c5[-1])>level and float(c5[-1])>=float(o5[-1]))
        strong_reclaim=bool(reclaimed and float(c5[-1]-l5[-1])>=0.45*max(float(h5[-1]-l5[-1]),1e-12))
        confirmed=near and touched and (strong_reclaim or (bp.get("score",0)>=0.95 and reclaimed))
    else:
        touched=bool(np.max(h5[-3:])>=level*0.9985); reclaimed=bool(float(c5[-1])<level and float(c5[-1])<=float(o5[-1]))
        strong_reclaim=bool(reclaimed and float(h5[-1]-c5[-1])>=0.45*max(float(h5[-1]-l5[-1]),1e-12))
        confirmed=near and touched and (strong_reclaim or (bp.get("score",0)<=-0.95 and reclaimed))
    if confirmed:
        PULLBACK_STATE.pop(key,None)
        return {"state":"CONFIRMED","confirmed":True,"reason":"突破后持续等待：已回踩关键位并由5m重新确认","level":level,"score":1.0,"active":False,"bars_waited":int(st.get("bars_waited",0))}
    return {"state":"WAIT_PULLBACK" if near else "WAITING","confirmed":False,
            "reason":"突破已记录，持续等待回踩关键位" + ("；当前已进入回踩区，等待5m确认" if near else "；暂不追单"),
            "level":level,"score":0.75 if near else 0.55,"active":True,"bars_waited":int(st.get("bars_waited",0)),"breakout_bar":int(st.get("breakout_bar",0))}

def _fast_position_reversal_check(frame5: Dict[str, np.ndarray], frame15: Dict[str, np.ndarray],
                                  side: str, entry: float = 0.0) -> Dict[str, Any]:
    """FAST持仓后反转/洗盘识别。宁可多确认一次，也不因单根插针误杀趋势仓。"""
    c5=frame5.get("close",np.array([])); o5=frame5.get("open",np.array([]))
    h5=frame5.get("high",np.array([])); l5=frame5.get("low",np.array([]))
    if len(c5)<12 or len(c5)!=len(o5) or len(c5)!=len(h5):
        return {"action":"HOLD","score":0.0,"reason":"持仓反转数据不足","reversal":False,"wash":False,"bar_ts":int(frame5.get("ts",np.array([0]))[-1]) if len(frame5.get("ts",np.array([]))) else 0}
    direction=1 if side=="long" else -1
    s15=_structure(frame15); bos15=_bos_choch(frame15,s15)
    s5=_structure(frame5); bos5=_bos_choch(frame5,s5)
    sweep5=_liquidity_sweep(frame5); disp5=_displacement(frame5)
    # 最近确认摆动位作为“真正结构失效位”，而不是拿一根最低/最高影线当反转。
    highs,lows=_swings(frame5)
    if direction>0:
        levels=[float(x[1]) for x in lows[-6:] if float(x[1])>0]
        # 持仓保护结构只认最新一个“已确认”的5m摆动低点，随新结构向前移动，
        # 不再长期盯着更早的低点。_swings 的 right=2 已保证该摆动点不是当前未确认K线。
        key=levels[-1] if levels else float(np.min(l5[-8:-1]))
        swept=float(np.min(l5[-2:]))<key and float(c5[-1])>key
        break_now=float(c5[-1])<key
        bearish_votes=sum([
            1 if direction*float(bos5.get("choch",0))< -0.40 else 0,
            1 if direction*float(bos5.get("bos",0))< -0.40 else 0,
            1 if float(disp5.get("direction",0))<0 and float(disp5.get("score",0))>=0.55 else 0,
            1 if float(c5[-1])<float(o5[-1]) and float(c5[-2])<float(o5[-2]) else 0,
        ])
        # 扫低后收回关键位：明确视为洗盘/正常回撤，不主动平仓。
        if swept:
            return {"action":"HOLD","score":0.15,"reason":"下破关键位后快速收回，偏向流动性扫盘/洗盘，不主动平仓","reversal":False,"wash":True,"key_level":key,"votes":bearish_votes,"bar_ts":int(frame5.get("ts",np.array([0]))[-1]) if len(frame5.get("ts",np.array([]))) else 0}
        confirmed=break_now and bearish_votes>=2 and float(c5[-1])<key*0.9995
        score=min(1.0,0.35*(1 if break_now else 0)+0.20*bearish_votes+0.25*(float(disp5.get("score",0)) if float(disp5.get("direction",0))<0 else 0))
    else:
        levels=[float(x[1]) for x in highs[-6:] if float(x[1])>0]
        # 空单同理：只使用最新已确认5m摆动高点作为结构失效参考。
        key=levels[-1] if levels else float(np.max(h5[-8:-1]))
        swept=float(np.max(h5[-2:]))>key and float(c5[-1])<key
        break_now=float(c5[-1])>key
        bearish_votes=sum([
            1 if direction*float(bos5.get("choch",0))< -0.40 else 0,
            1 if direction*float(bos5.get("bos",0))< -0.40 else 0,
            1 if float(disp5.get("direction",0))>0 and float(disp5.get("score",0))>=0.55 else 0,
            1 if float(c5[-1])>float(o5[-1]) and float(c5[-2])>float(o5[-2]) else 0,
        ])
        if swept:
            return {"action":"HOLD","score":0.15,"reason":"上破关键位后快速收回，偏向流动性扫盘/洗盘，不主动平仓","reversal":False,"wash":True,"key_level":key,"votes":bearish_votes,"bar_ts":int(frame5.get("ts",np.array([0]))[-1]) if len(frame5.get("ts",np.array([]))) else 0}
        confirmed=break_now and bearish_votes>=2 and float(c5[-1])>key*1.0005
        score=min(1.0,0.35*(1 if break_now else 0)+0.20*bearish_votes+0.25*(float(disp5.get("score",0)) if float(disp5.get("direction",0))>0 else 0))
    if confirmed:
        return {"action":"CONFIRMED","score":score,"reason":"结构有效破坏 + 反向价格位移/连续确认，判定真反转","reversal":True,"wash":False,"key_level":key,"votes":bearish_votes,"bar_ts":int(frame5.get("ts",np.array([0]))[-1]) if len(frame5.get("ts",np.array([]))) else 0}
    if break_now or bearish_votes>=1:
        return {"action":"WATCH","score":score,"reason":"出现初步反向迹象，但尚不足以判定真反转，继续观察","reversal":False,"wash":False,"key_level":key,"votes":bearish_votes}
    return {"action":"HOLD","score":score,"reason":"原方向结构仍保持，继续持仓","reversal":False,"wash":False,"key_level":key,"votes":bearish_votes}

def _candle_strength(frame: Dict[str, np.ndarray]) -> float:
    o = frame.get("open", np.array([])); h = frame.get("high", np.array([])); l = frame.get("low", np.array([])); c = frame.get("close", np.array([]))
    if len(c) == 0: return 0.0
    rng = max(float(h[-1]-l[-1]), 1e-12); body = (float(c[-1])-float(o[-1]))/rng
    return float(np.clip(body, -1, 1))


def _price_location(frame: Dict[str, np.ndarray]) -> Dict[str, Any]:
    h = frame.get("high", np.array([])); l = frame.get("low", np.array([])); c = frame.get("close", np.array([]))
    if len(c) < 20: return {"score": 0.0, "label": "价格位置数据不足", "support": 0.0, "resistance": 0.0}
    px = float(c[-1]); support = float(np.min(l[-20:-1])); resistance = float(np.max(h[-20:-1])); span=max(resistance-support,1e-12)
    pos=(px-support)/span
    if pos <= 0.35: score=0.45; label="靠近支撑"
    elif pos >= 0.65: score=-0.45; label="靠近阻力"
    else: score=0.0; label="区间中部"
    return {"score": score, "label": label, "support": support, "resistance": resistance, "position": pos}


def _trigger_quality(frame5: Dict[str, np.ndarray], frame15: Dict[str, np.ndarray], direction: int) -> Dict[str, Any]:
    """5m裸K真实触发：一个明确的同向触发即可参与，不要求同时出现两个形态。"""
    s5 = _structure(frame5); bp5 = _breakout_pullback_reclaim(frame5); sweep5 = _liquidity_sweep(frame5)
    bos5 = _bos_choch(frame5, s5); n5 = _n_pattern(frame5); candle5 = _candle_strength(frame5)
    raw = {
        "突破回踩": float(bp5.get("score", 0.0) or 0.0) * direction,
        "BOS": float(bos5.get("bos", 0.0) or 0.0) * direction,
        "CHOCH": float(bos5.get("choch", 0.0) or 0.0) * direction,
        "流动性扫盘": float(sweep5.get("score", 0.0) or 0.0) * direction,
        "N字": float(n5.get("score", 0.0) or 0.0) * direction,
        "强势K": float(candle5 or 0.0) * direction,
    }
    aligned_items=[(k,v) for k,v in raw.items() if v>=0.18]
    opposite=max([-v for v in raw.values() if v<0], default=0.0)
    trigger=max([v for _,v in aligned_items], default=0.0)
    # 改进2：单根K线实体（强势K）不能单独构成触发，必须至少有一个结构性价格行为事件（突破回踩/BOS/CHOCH/扫盘/N字）。
    structural_keys=("突破回踩","BOS","CHOCH","流动性扫盘","N字")
    structural_items=[(k,v) for k,v in aligned_items if k in structural_keys]
    structural_trigger=max([v for _,v in structural_items], default=0.0)
    # 真实触发需要清晰的同向结构性证据，单根普通K线不足以开仓；明显反向时不进。
    ok=bool(trigger>=0.18 and structural_trigger>=0.18 and (opposite < 0.45 or trigger >= opposite + 0.10))
    return {"ok":ok,"score":float(np.clip(trigger,0,1)),"aligned_votes":len(aligned_items),"structural_votes":len(structural_items),"evidence":[k for k,_ in aligned_items],"structural_evidence":[k for k,_ in structural_items],"opposite_score":float(opposite),"structure":s5,"breakout":bp5,"sweep":sweep5,"bos":bos5,"n":n5,"candle":candle5}


def _directional_context(frame4: Dict[str, np.ndarray], frame1: Dict[str, np.ndarray], frame15: Dict[str, np.ndarray]) -> Dict[str, Any]:
    """4H/1H只负责大方向；15m只负责结构，不参与大方向投票。"""
    s4=_structure(frame4); s1=_structure(frame1); s15=_structure(frame15)
    b4=int(s4["bias"]); b1=int(s1["bias"])
    # 4H/1H继续参与大方向判断，但不再因为二者短暂冲突而直接把FAST方向清零。
    # 日内策略以更灵敏的1H为冲突时的主导方向；4H逆向只降低一致度/后续风险权重。
    if b4 and b1 and b4 != b1:
        direction=b1; source="1H主导（4H逆向，降风险）"
    elif b4:
        direction=b4; source="4H主方向"
    elif b1:
        direction=b1; source="1H主方向（4H中性）"
    else:
        direction=0; source="4H/1H均中性"
    if direction:
        agreement=1.0 if (b4 and b1 and b4==b1) else (0.72 if b4==direction or b1==direction else 0.55)
    else:
        agreement=0.0
    return {"direction":direction,"agreement":agreement,"direction_source":source,"4h":s4,"1h":s1,"15m":s15}


def _ema_arr(values: np.ndarray, n: int) -> np.ndarray:
    """指数均线序列（只用已收盘K）。"""
    x = np.asarray(values, dtype=float)
    if len(x) < 2:
        return x.copy() if len(x) else x
    alpha = 2.0 / (n + 1.0)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = alpha * x[i] + (1.0 - alpha) * out[i - 1]
    return out


def _atr_arr(frame: Dict[str, np.ndarray], n: int = 14) -> np.ndarray:
    """Wilder 风格 ATR 序列（这里用简单均值，足够稳定）。"""
    h = frame.get("high", np.array([])); l = frame.get("low", np.array([])); c = frame.get("close", np.array([]))
    m = len(c)
    if m < 2:
        return np.full(m, 0.0)
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    out = np.full(m, 0.0)
    if m >= n:
        csum = np.cumsum(np.insert(tr, 0, 0.0))
    else:
        csum = None
    for i in range(m):
        lo = max(0, i - n + 1)
        out[i] = float(np.mean(tr[lo:i + 1]))
    return out


def _vwap_arr(frame: Dict[str, np.ndarray], window: int) -> np.ndarray:
    """滚动会话 VWAP（典型价*成交量）。加密 24/7，用滚动窗口近似当日会话中枢。"""
    h = frame.get("high", np.array([])); l = frame.get("low", np.array([]))
    c = frame.get("close", np.array([])); v = frame.get("volume", np.array([]))
    m = len(c)
    if m == 0 or len(v) != m:
        return np.full(m, np.nan)
    typ = (h + l + c) / 3.0
    pv = typ * v
    k = min(window, m)
    cs_pv = np.concatenate([[0.0], np.cumsum(pv)])
    cs_v = np.concatenate([[0.0], np.cumsum(v)])
    out = np.full(m, np.nan)
    for i in range(m):
        lo = max(0, i - k + 1)
        vsum = cs_v[i + 1] - cs_v[lo]
        out[i] = (cs_pv[i + 1] - cs_pv[lo]) / vsum if vsum > 1e-12 else c[i]
    return out


def _fast_tp_sl(frame15: Dict[str, np.ndarray], frame5: Dict[str, np.ndarray], side: str, entry: float, min_rr: float = 1.4) -> Dict[str, Any]:
    """FAST v3：ATR 波动自适应的结构 TP/SL。

    真实数据回测结论（见 backtest/ 报告）：
    - v2 止损过紧（中位 0.27%），0.15% 往返成本在如此窄的止损上≈0.7R，且被 5m 噪声频繁扫损；
    - 固定过远目标(2.4R)很难到达。v3 改为：
      止损 = 扫损/摆动失效位 + 0.5*ATR15 缓冲，并设 0.8~2.0 倍 ATR15 的波动上下限；
      止盈 = 最近反向流动性池（唐奇安/摆动高/低点、等高等低），就近兑现，盈亏比裁剪到 1.2~1.8；
      若前方结构目标不足以达到最低盈亏比，用 ATR 兜底目标而不是弃单（保证接得到单）。
    """
    h15 = frame15.get("high", np.array([])); l15 = frame15.get("low", np.array([]))
    h5 = frame5.get("high", np.array([])); l5 = frame5.get("low", np.array([]))
    if entry <= 0 or len(h15) < 20 or len(h5) < 10:
        return {"ok": False, "reason": "结构数据不足，无法可靠计算FAST TP/SL"}
    atr15 = _atr_arr(frame15, 14); atr5 = _atr_arr(frame5, 14)
    a15 = float(atr15[-1]) if len(atr15) and np.isfinite(atr15[-1]) and atr15[-1] > 0 else float(np.median(np.maximum(h15[-20:] - l15[-20:], 1e-12)))
    a5 = float(atr5[-1]) if len(atr5) and np.isfinite(atr5[-1]) and atr5[-1] > 0 else float(np.median(np.maximum(h5[-20:] - l5[-20:], 1e-12)))
    a_unit = max(a15, 1.6 * a5, entry * 0.0015)  # 以 15m 波动为主，防止 5m 噪声把止损压得过窄
    pools = _liquidity_pools(frame15)
    # 真实数据回测：更宽的失效位止损(≈1.2~2.4倍15m-ATR)显著降低被影线噪声扫损概率、且成本/R更小；
    # 目标就近取最近流动性池(rr≈1.2~1.5)，到达率最高。
    rr_floor = 1.2; sl_floor_k, sl_cap_k = 1.2, 2.4
    rr_cap = max(1.5, float(min_rr))
    if side == "LONG":
        sw_low = float(np.min(l15[-6:]))           # 最近扫损/摆动失效参考
        struct_dist = max(entry - sw_low, 0.0)
        stop_dist = float(np.clip(struct_dist + 0.5 * a_unit, sl_floor_k * a_unit, sl_cap_k * a_unit))
        sl_px = entry - stop_dist
        eqh = float(pools.get("eqh_price", 0) or 0)
        swing_tps = [float(x[1]) for x in _swings(frame15)[0][-12:] if float(x[1]) > entry]
        don = float(np.max(h15[-20:]))
        cands = sorted(set([t for t in swing_tps + ([eqh] if eqh > entry else []) + [don] if t > entry]))
        chosen = None
        for t in cands:
            if (t - entry) / max(stop_dist, 1e-12) >= rr_floor:
                chosen = t; break
        if chosen is None:
            tp_px = entry + rr_floor * stop_dist      # ATR/结构兜底目标，不弃单
            tgt_note = "前方流动性目标不足，按最低盈亏比兜底目标"
        else:
            tp_px = min(chosen, entry + rr_cap * stop_dist)
            tgt_note = "最近买方流动性池目标"
    else:
        sw_high = float(np.max(h15[-6:]))
        struct_dist = max(sw_high - entry, 0.0)
        stop_dist = float(np.clip(struct_dist + 0.5 * a_unit, sl_floor_k * a_unit, sl_cap_k * a_unit))
        sl_px = entry + stop_dist
        eql = float(pools.get("eql_price", 0) or 0)
        swing_tps = [float(x[1]) for x in _swings(frame15)[1][-12:] if 0 < float(x[1]) < entry]
        don = float(np.min(l15[-20:]))
        cands = sorted(set([t for t in swing_tps + ([eql] if 0 < eql < entry else []) + [don] if 0 < t < entry]), reverse=True)
        chosen = None
        for t in cands:
            if (entry - t) / max(stop_dist, 1e-12) >= rr_floor:
                chosen = t; break
        if chosen is None:
            tp_px = entry - rr_floor * stop_dist
            tgt_note = "前方流动性目标不足，按最低盈亏比兜底目标"
        else:
            tp_px = max(chosen, entry - rr_cap * stop_dist)
            tgt_note = "最近卖方流动性池目标"
    sl_pct = abs(entry - sl_px) / entry; tp_pct = abs(tp_px - entry) / entry; rr = tp_pct / max(sl_pct, 1e-12)
    if sl_pct < 0.001 or sl_pct > 0.08:
        return {"ok": False, "reason": f"结构止损距离异常{sl_pct:.2%}"}
    if tp_pct < 0.002 or tp_pct > 0.15:
        return {"ok": False, "reason": f"结构目标距离异常{tp_pct:.2%}"}
    return {"ok": True, "tp": tp_pct, "sl": sl_pct, "tp_price": tp_px, "sl_price": sl_px, "rr": rr,
            "target_rr": rr, "atr15": a15, "reason": f"ATR波动自适应失效位止损 + {tgt_note}(1:{rr:.2f})"}


def _liquidity_pools(frame: Dict[str, np.ndarray], lookback: int = 30, tolerance: float = 0.0015) -> Dict[str, Any]:
    """裸K流动性池：识别近似等高/等低，作为前方流动性目标与扫盘证据。"""
    h=frame.get("high",np.array([])); l=frame.get("low",np.array([]))
    if len(h)<8 or len(l)<8:
        return {"eqh":False,"eql":False,"eqh_price":0.0,"eql_price":0.0,"score":0.0,"label":"流动性池不足"}
    hh=h[-min(len(h),lookback):]; ll=l[-min(len(l),lookback):]
    eqh=False; eql=False; eqh_px=0.0; eql_px=0.0
    for i in range(max(0,len(hh)-12),len(hh)):
        if i+3 < len(hh):
            a=float(hh[i]); b=float(np.max(hh[i+1:i+4]))
            if a>0 and abs(a-b)/a <= tolerance: eqh=True; eqh_px=(a+b)/2
        if i+3 < len(ll):
            a=float(ll[i]); b=float(np.min(ll[i+1:i+4]))
            if a>0 and abs(a-b)/a <= tolerance: eql=True; eql_px=(a+b)/2
    score=0.55 if (eqh or eql) else 0.0
    label="存在等高/等低流动性池" if score else "未发现明显等高/等低"
    return {"eqh":eqh,"eql":eql,"eqh_price":eqh_px,"eql_price":eql_px,"score":score,"label":label}


def _displacement(frame: Dict[str, np.ndarray], lookback: int = 20) -> Dict[str, Any]:
    """裸K位移：大实体突破，衡量真正的价格扩张而非指标信号。"""
    o=frame.get("open",np.array([])); h=frame.get("high",np.array([])); l=frame.get("low",np.array([])); c=frame.get("close",np.array([]))
    n=len(c)
    if n<8: return {"direction":0,"score":0.0,"label":"位移不足","body_ratio":0.0}
    start=max(0,n-lookback-1); ranges=np.maximum(h[start:n-1]-l[start:n-1],1e-12); bodies=np.abs(c[start:n-1]-o[start:n-1])
    baseline=float(np.median(ranges)) if len(ranges) else 0.0
    rng=max(float(h[-1]-l[-1]),1e-12); body=float(abs(c[-1]-o[-1]));
    body_ratio=body/rng; expansion=rng/max(baseline,1e-12)
    score=float(np.clip(0.35*min(body_ratio/0.7,1.0)+0.65*min(expansion/1.8,1.0),0,1))
    direction=1 if c[-1]>o[-1] else -1 if c[-1]<o[-1] else 0
    label="强势位移" if score>=0.72 and direction else "一般位移" if score>=0.45 and direction else "位移不足"
    return {"direction":direction,"score":score,"label":label,"body_ratio":body_ratio,"expansion":expansion}


def _rejection_wick(frame: Dict[str, np.ndarray]) -> Dict[str, Any]:
    """裸K拒绝：长影线+收盘位置，识别被市场快速否定的价格。"""
    o=frame.get("open",np.array([])); h=frame.get("high",np.array([])); l=frame.get("low",np.array([])); c=frame.get("close",np.array([]))
    if len(c)<2: return {"direction":0,"score":0.0,"label":"拒绝不足"}
    rng=max(float(h[-1]-l[-1]),1e-12); body=abs(float(c[-1]-o[-1])); upper=float(h[-1]-max(o[-1],c[-1])); lower=float(min(o[-1],c[-1])-l[-1])
    if lower/rng>=0.55 and c[-1]>=o[-1]: return {"direction":1,"score":float(np.clip(lower/rng,0,1)),"label":"下影拒绝"}
    if upper/rng>=0.55 and c[-1]<=o[-1]: return {"direction":-1,"score":float(np.clip(upper/rng,0,1)),"label":"上影拒绝"}
    return {"direction":0,"score":0.0,"label":"未见明显拒绝"}


def _compression_expansion(frame: Dict[str, np.ndarray], window: int = 6) -> Dict[str, Any]:
    """裸K压缩→扩张：近期波幅收缩后出现方向性扩张。"""
    h=frame.get("high",np.array([])); l=frame.get("low",np.array([])); c=frame.get("close",np.array([]))
    if len(c)<window+5: return {"direction":0,"score":0.0,"label":"压缩结构不足"}
    recent=np.maximum(h[-window:]-l[-window:],1e-12); prior=np.maximum(h[-window-6:-window]-l[-window-6:-window],1e-12)
    r=float(np.mean(recent)); pr=float(np.mean(prior)) if len(prior) else r
    expansion=r/max(pr,1e-12); direction=1 if c[-1]>c[-2] else -1 if c[-1]<c[-2] else 0
    score=float(np.clip((expansion-1.0)/0.9,0,1)) if expansion>=1.05 else 0.0
    label="压缩后扩张" if score>=0.45 and direction else "未形成明显扩张"
    return {"direction":direction,"score":score,"label":label,"expansion":expansion}


def _premium_discount(frame: Dict[str, np.ndarray]) -> Dict[str, Any]:
    """区间上下半区：用最近结构高低点判断当前价格处于溢价/折价。"""
    h=frame.get("high",np.array([])); l=frame.get("low",np.array([])); c=frame.get("close",np.array([]))
    if len(c)<10: return {"position":"未知","score":0.0,"mid":0.0,"label":"区间不足"}
    hi=float(np.max(h[-20:])); lo=float(np.min(l[-20:])); mid=(hi+lo)/2; px=float(c[-1])
    if hi<=lo: return {"position":"未知","score":0.0,"mid":mid,"label":"区间无效"}
    pos="折价区" if px<mid else "溢价区" if px>mid else "平衡区"
    score=float(np.clip(abs(px-mid)/(hi-lo)*2,0,1))
    return {"position":pos,"score":score,"mid":mid,"high":hi,"low":lo,"label":pos}


def _efficiency_ratio(close: np.ndarray, n: int = 14) -> float:
    """Kaufman 效率比 ER=|净位移|/路径总长，区间[0,1]。

    趋势单边 ER 高(→1)，来回拉锯的震荡 ER 低(→0)。用于在震荡市关掉顺势结构/突破单。
    """
    c=np.asarray(close,float)
    if len(c)<n+1: return 0.5
    seg=c[-(n+1):]; net=abs(seg[-1]-seg[0]); path=float(np.sum(np.abs(np.diff(seg))))
    if path<=1e-12: return 0.0
    return float(np.clip(net/path,0.0,1.0))


def _dealing_range(frame: Dict[str, np.ndarray]) -> Dict[str, Any]:
    """裸K日内“交易区间/折扣-溢价”：用最近一腿已确认摆动低->高定义 dealing range。

    多头只在折扣区（区间下半、回踩到位）接，空头只在溢价区（区间上半、反弹到位）接；
    区间正中间是“无人区”，不做顺势单。区间外视为已突破，交给突破回踩路径处理。
    """
    h=frame.get("high",np.array([])); l=frame.get("low",np.array([])); c=frame.get("close",np.array([]))
    if len(c)<15:
        return {"ok":False,"pos":0.5,"low":0.0,"high":0.0,"mid":0.0,"beyond":0,"zone":"数据不足"}
    highs,lows=_swings(frame)
    if highs and lows:
        rh=float(highs[-1][1]); rl=float(lows[-1][1])
        if rh<=rl: rh,rl=float(np.max(h[-20:])),float(np.min(l[-20:]))
    else:
        rh,rl=float(np.max(h[-20:])),float(np.min(l[-20:]))
    span=max(rh-rl,1e-12); px=float(c[-1]); pos=float(np.clip((px-rl)/span,0.0,1.0))
    beyond=1 if px>rh else (-1 if px<rl else 0)
    if beyond!=0: zone="区间外(突破)"
    elif pos<=0.45: zone="折扣区"
    elif pos>=0.55: zone="溢价区"
    else: zone="中部无人区"
    # OTE 甜区：多头约在区间 0.18~0.45（对应0.55~0.82回撤），空头镜像。
    sweet_long = (0.18<=pos<=0.45); sweet_short = (0.55<=pos<=0.82)
    return {"ok":True,"pos":pos,"low":rl,"high":rh,"mid":(rh+rl)/2.0,"beyond":beyond,
            "zone":zone,"sweet_long":sweet_long,"sweet_short":sweet_short}


def _ob_fvg_mitigation(frame: Dict[str, np.ndarray], direction: int, px: float, tol: float = 0.0030) -> Dict[str, Any]:
    """裸K日内高质量进场：价格回补(mitigation)到“同向订单块 ∩ 同向FVG”共振区。

    机构订单块要有效，需先有位移/失衡(FVG)；价格只缓解一次该区域。
    返回是否构成共振回补、是否触及OB、是否触及FVG。
    """
    ob=_order_block(frame); fvg=_fvg(frame)
    ob_a = bool(direction*float(ob.get("score",0) or 0)>0 and float(ob.get("high",0) or 0)>0)
    fvg_a = bool(direction*float(fvg.get("score",0) or 0)>0 and float(fvg.get("high",0) or 0)>0)
    def tap(z):
        lo=float(z.get("low",0) or 0); hi=float(z.get("high",0) or 0)
        if hi<=lo or px<=0: return False
        return lo*(1-tol) <= px <= hi*(1+tol)
    mit_ob=ob_a and tap(ob); mit_fvg=fvg_a and tap(fvg)
    overlap=False
    if ob_a and fvg_a:
        ol=max(float(ob["low"]),float(fvg["low"])); oh=min(float(ob["high"]),float(fvg["high"]))
        overlap=oh>=ol
        merged_lo=min(float(ob["low"]),float(fvg["low"])); merged_hi=max(float(ob["high"]),float(fvg["high"]))
    else:
        merged_lo=merged_hi=0.0
    in_merged = bool(merged_hi>0 and merged_lo*(1-tol) <= px <= merged_hi*(1+tol))
    # 共振：OB与FVG区间重叠，且价格正在回补该合并区，且至少OB被触及（OB为进场触发）。
    confluence=bool(overlap and in_merged and mit_ob)
    return {"confluence":confluence,"mit_ob":mit_ob,"mit_fvg":mit_fvg,"overlap":overlap,
            "ob_label":ob.get("label",""),"fvg_label":fvg.get("label","")}


def _killzone_ok(ts_ms: float) -> bool:
    """可选：ICT killzone 时段（UTC）。伦敦 07:00-10:00、纽约 12:00-16:00 为主窗口。

    加密24/7，该过滤默认关闭(_REQUIRE_KILLZONE=False)，开启前请用真实时间戳验证。
    """
    try:
        import datetime as _dt
        h=_dt.datetime.utcfromtimestamp(float(ts_ms)/1000.0).hour
        return (7<=h<10) or (12<=h<16)
    except Exception:
        return True


# v2 修补开关（在v1基础上叠加裸K日内机构模型）
# 是否只在伦敦/纽约 killzone 时段交易（默认关，需真实数据验证后再开）。
_REQUIRE_KILLZONE = False
# 震荡过滤（可选，默认关闭）：15m Kaufman效率比低于该值视为拉锯震荡，
# 低ER时回避“假突破 + 区间中部结构单”，保留边缘回踩/扫盘MSS/机构区回补。
# 回放：开启(0.30)能显著提升混合/震荡市单笔期望，但会减少成交并削掉一部分强趋势利润，
# 取舍因人而异，故默认 0=关闭；震荡行情多、想少做单的用户可设 0.30 自行用真实数据验证。
_CHOP_ER_MAX = 0.0
# 机构区(OB∩FVG)回补的最低15m效率比：低于该值说明在拉锯震荡、回补多为假回补，不做。
_MITIGATION_MIN_ER = 0.30
# 是否把等高高点/等低低点(流动性池)作为优先止盈目标（朝最近 resting liquidity 交付）。
_USE_LIQUIDITY_TP = True

# 修补开关：是否允许“强趋势加速”不等回踩直接市价追单。
# 回放显示该追单是“进场即被反打止损”的主要来源之一，默认关闭，统一要求回踩确认。
_ALLOW_ACCELERATION_CHASE = False
# 修补开关：裸15m结构突破（无完整回踩/扫盘反转）时，是否必须等到5m回踩或扫盘收回确认才进场。
# True=只买回踩/重新站回，不打突破第一根（回放中显著降低即损、提升期望）。
_REQUIRE_5M_RETEST_FOR_STRUCTURE = True

def _fast_entry_quality(
    symbol: str,
    frame5: Dict[str, np.ndarray],
    frame15: Dict[str, np.ndarray],
    direction: int,
    px: float,
    s15: Dict[str, Any],
    bos15: Dict[str, Any],
    n15: Dict[str, Any],
    fvg15: Dict[str, Any],
    sweep15: Dict[str, Any],
    ob15: Dict[str, Any],
    pb15: Dict[str, Any],
    pools15: Dict[str, Any],
    disp15: Dict[str, Any],
    reject15: Dict[str, Any],
    comp15: Dict[str, Any],
    loc15: Dict[str, Any],
    pd15: Dict[str, Any],
    trigger: Dict[str, Any],
) -> Dict[str, Any]:
    """FAST-only price-action entry gate.

    This is deliberately independent from ML/model confidence.  It ranks the
    quality of the current price location and trigger, while allowing either
    a breakout/reclaim path or a liquidity/reversal path.
    """
    o15=frame15.get("open",frame15.get("close",np.array([])))
    h15=frame15.get("high",np.array([])); l15=frame15.get("low",np.array([]))
    c15=frame15.get("close",np.array([])); c5=frame5.get("close",np.array([]))
    if len(c15)<20 or len(c5)<10 or px<=0:
        return {"decision":"WAIT","score":0.0,"tier":"不做","reason":"裸K入场位置数据不足"}

    # Position is evaluated against completed 15m structure, not the live ticker
    # alone.  This avoids treating a late chase as a fresh breakout.
    support=float(np.min(l15[-20:-1])); resistance=float(np.max(h15[-20:-1]))
    span=max(resistance-support,1e-12)
    pos=float(np.clip((px-support)/span,0,1))

    # Directional location: longs prefer discount/lower half, shorts prefer
    # premium/upper half. Middle is tradable but not premium quality.
    location_edge = (0.5-pos) * 2.0 * direction
    location_score=float(np.clip(0.5 + 0.5*location_edge,0,1))
    near_bad = (direction>0 and pos>=0.82) or (direction<0 and pos<=0.18)
    # 突破型入场与回踩型入场的“好位置”不同：突破确认后允许处于区间高位/低位，
    # 只要不是极端延伸，就不因为传统discount/premium评分而重复扣分。
    breakout_level=float(np.max(h15[-12:-4]) if direction>0 else np.min(l15[-12:-4]))
    breakout_distance=abs(px-breakout_level)/max(px,1e-12) if breakout_level>0 else 1.0
    breakout_path_preview=bool((direction>0 and px>=breakout_level) or (direction<0 and px<=breakout_level))
    if breakout_path_preview and breakout_distance<=0.012:
        location_score=max(location_score, float(np.clip(0.72-20.0*breakout_distance,0.50,0.72)))

    # Chase detector: compare current completed 5m body/range and extension
    # from the recent 5m range. A strong candle can trigger, but an already
    # extended candle should wait for a reclaim/pullback.
    o5=frame5.get("open",np.array([])); h5=frame5.get("high",np.array([])); l5=frame5.get("low",np.array([]))
    rng5=max(float(h5[-1]-l5[-1]),1e-12)
    body5=abs(float(c5[-1]-o5[-1]))
    body_ratio=body5/rng5
    recent_span=max(float(np.max(h5[-7:-1])-np.min(l5[-7:-1])),1e-12)
    extension=max(abs(px-float(np.mean([np.max(h5[-7:-1]),np.min(l5[-7:-1])])))/recent_span,0.0)
    chase=(body_ratio>=0.78 and extension>=0.55)
    chase_penalty=0.22 if chase else 0.0

    # 改进4：量能确认。最近3根5m均量 / 此前18根均量；数据不足时不拦截（保守放行）。
    v5=frame5.get("volume",np.array([]))
    vol_ratio=1.0
    if len(v5)>=21:
        vol_now=float(np.mean(v5[-3:])); vol_base=float(np.mean(v5[-21:-3]))
        vol_ratio=vol_now/max(vol_base,1e-12)

    # 正向证据也按“独立类别”计票，避免同一事件的 BOS+CHOCH、FVG+OB 等重复抬高评分。
    positive_categories={
        "结构": max(0.0,direction*s15.get("bias",0),direction*bos15.get("bos",0),direction*bos15.get("choch",0)),
        "动能": max(0.0,direction*disp15.get("score",0),direction*reject15.get("score",0)),
        "形态": max(0.0,direction*n15.get("score",0)),
        "价格区": max(0.0,direction*fvg15.get("score",0),direction*ob15.get("score",0)),
        "流动性": max(0.0,direction*sweep15.get("score",0),direction*pools15.get("score",0)),
        "回踩": max(0.0,direction*pb15.get("score",0)),
    }
    aligned=[v for v in positive_categories.values() if v>=0.30]
    strong=[v for v in positive_categories.values() if v>=0.65]
    aligned_categories=[k for k,v in positive_categories.items() if v>=0.30]
    opposite_components={
        "结构偏向": direction*s15.get("bias",0),
        "BOS": direction*bos15.get("bos",0),
        "CHOCH": direction*bos15.get("choch",0),
        "N字": direction*n15.get("score",0),
        "FVG": direction*fvg15.get("score",0),
        "扫盘": direction*sweep15.get("score",0),
        "OB": direction*ob15.get("score",0),
        "回踩": direction*pb15.get("score",0),
        "位移": direction*disp15.get("score",0),
        "拒绝K": direction*reject15.get("score",0),
    }
    opposite=max([-v for v in opposite_components.values() if v<0], default=0.0)
    # 反向结构采用“独立类别确认”，避免同一价格区域的 FVG+OB 被当成两票。
    # 规则：结构(BOS/CHOCH) / 动能(位移/拒绝K) / 形态(N字) / 价格区(FVG/OB)
    # 每一类别最多算1票；只有结构 + 另一独立类别才进入观察。
    reverse_items={
        "BOS": opposite_components.get("BOS",0.0) <= -0.45,
        "CHOCH": opposite_components.get("CHOCH",0.0) <= -0.45,
        "N字": opposite_components.get("N字",0.0) <= -0.65,
        "FVG": opposite_components.get("FVG",0.0) <= -0.65,
        "OB": opposite_components.get("OB",0.0) <= -0.65,
        "位移": opposite_components.get("位移",0.0) <= -0.45,
        "拒绝K": opposite_components.get("拒绝K",0.0) <= -0.55,
    }
    reverse_categories={
        "结构": bool(reverse_items["BOS"] or reverse_items["CHOCH"]),
        "动能": bool(reverse_items["位移"] or reverse_items["拒绝K"]),
        "形态": bool(reverse_items["N字"]),
        "价格区": bool(reverse_items["FVG"] or reverse_items["OB"]),
    }
    reverse_structural_votes=int(reverse_categories["结构"])
    reverse_total_votes=sum(1 for v in reverse_categories.values() if v)
    reverse_break=bool(
        opposite_components.get("BOS",0.0) <= -0.70 or
        opposite_components.get("CHOCH",0.0) <= -0.70
    )
    reverse_force=bool(
        opposite_components.get("位移",0.0) <= -0.65 or
        opposite_components.get("拒绝K",0.0) <= -0.65 or
        opposite_components.get("N字",0.0) <= -0.65
    )
    # 真反转：关键结构破坏 + 独立反向动能/形态。
    reverse_confirmed=bool(reverse_break and reverse_force and reverse_total_votes>=2)
    # 观察级反转必须包含“结构”类别 + 另一个独立类别。
    # 因此单纯 FVG+OB（同一区域）不会把系统锁在 WAIT。
    reverse_watch=bool(not reverse_confirmed and reverse_categories["结构"] and reverse_total_votes>=2)
    reverse_single=bool(not reverse_confirmed and reverse_total_votes==1 and opposite>=0.70)

    # 15m 明显反向时，5m 单独出现同向触发不能抢跑。
    # 必须先看到 15m 自身出现“结构翻转 + 动能/回收”的路径，才允许顺着大方向进场。
    countertrend_15m=bool(direction and direction*float(s15.get("bias",0) or 0) <= -0.70)
    reversal_path_15m=bool(
        countertrend_15m and
        (direction*bos15.get("bos",0) >= 0.45 or direction*bos15.get("choch",0) >= 0.45) and
        (direction*disp15.get("score",0) >= 0.55 or
         direction*reject15.get("score",0) >= 0.55 or
         direction*pb15.get("score",0) >= 0.75)
    )

    # ---- v2: 裸K日内机构进场模型 ----
    px15=float(c15[-1])
    er15=_efficiency_ratio(c15,14)
    deal=_dealing_range({"open":o15,"high":h15,"low":l15,"close":c15})
    mit=_ob_fvg_mitigation({"open":o15,"high":h15,"low":l15,"close":c15}, direction, px15)
    raw_mitigation=bool(mit.get("confluence"))   # 同向 OB∩FVG 回补（候选）

    # Three legitimate intraday entry archetypes:
    # A) displacement/BOS -> breakout -> pullback/reclaim（突破回踩重新站回）
    # B) liquidity sweep -> CHoCH/MSS -> displacement/FVG（扫损→结构位移，完整MSS序列）
    # C) mitigation into aligned OB∩FVG in discount/premium（回补机构区）
    breakout_path = direction*pb15.get("score",0) >= 0.75
    # v2收紧扫盘反转：必须 扫损 + CHoCH结构位移 + (位移K 或 FVG失衡)，不再接受单根影线反转。
    sweep_path = (
        direction*sweep15.get("score",0) >= 0.55 and
        direction*bos15.get("choch",0) >= 0.45 and
        (direction*disp15.get("score",0) >= 0.55 or direction*fvg15.get("score",0) >= 0.45)
    )
    structure_path = len(aligned_categories)>=2 and (
        direction*bos15.get("bos",0)>=0.45 or
        direction*bos15.get("choch",0)>=0.45 or
        direction*n15.get("score",0)>=0.65 or
        direction*disp15.get("score",0)>=0.65
    )
    # 机构区回补只在“有趋势效率”时做：震荡(低ER)里价格反复在边缘假回补，回放全亏；
    # 趋势中ER高、或属于扫盘MSS路径时才放行。该门独立于可选的全局震荡过滤，默认生效。
    mitigation_path = bool(raw_mitigation and (sweep_path or er15>=_MITIGATION_MIN_ER))
    # v2折扣/溢价位置：顺势单必须在正确半区，除非是已确认的突破回踩/扫盘MSS/OB∩FVG回补。
    if direction>0:
        good_zone = bool(deal.get("pos",0.5)<=0.55)
        sweet_zone = bool(deal.get("sweet_long"))
    else:
        good_zone = bool(deal.get("pos",0.5)>=0.45)
        sweet_zone = bool(deal.get("sweet_short"))
    location_exempt = bool(breakout_path or sweep_path or mitigation_path)
    trigger_score=float(trigger.get("score",0) or 0)
    trigger_ok=bool(trigger.get("ok") and trigger_score>=0.30 and trigger.get("aligned_votes",0)>=1)

    # Quality score: structure is dominant; location/chase prevents late entries.
    structure_quality=float(np.clip(
        0.35*min(len(aligned_categories)/4.0,1.0) +
        0.25*min(max(strong,default=0.0),1.0) +
        0.20*min(max(direction*pb15.get("score",0),0.0),1.0) +
        0.20*min(max(direction*disp15.get("score",0),0.0),1.0),
        0,1
    ))
    path_ok=breakout_path or sweep_path or mitigation_path or structure_path
    reverse_penalty=0.08 if reverse_single else 0.0
    # v2：OB∩FVG回补共振、OTE甜区 给予质量加分；错误半区轻度扣分（降级而非一律拦）。
    mit_bonus = 0.10 if mitigation_path else (0.04 if mit.get("mit_ob") else 0.0)
    zone_bonus = 0.05 if sweet_zone else (0.0 if (good_zone or location_exempt) else -0.03)
    no_mans_land = bool(0.40 <= deal.get("pos",0.5) <= 0.60)
    score=float(np.clip(
        0.46*structure_quality +
        0.26*trigger_score +
        0.18*location_score +
        mit_bonus + zone_bonus -
        chase_penalty -
        reverse_penalty,
        0,1
    ))

    # Smart pullback: only extended/late entries normally wait for a confirmed retest.
    # NEW: strong-trend acceleration override. If the market is clearly expanding
    # and continuing in the same direction, do not force a pullback that may never
    # happen. Enter with a reduced size, then let the existing position-management
    # chain handle the trade.
    pullback=_smart_pullback_state(symbol,frame5,frame15,direction,px)

    acceleration_follow=False
    acceleration_reason=""
    if pullback.get("active") and not pullback.get("confirmed") and len(c5)>=8 and len(o5)>=8 and len(h5)>=8 and len(l5)>=8:
        bodies=[]
        directional=[]
        for i in range(-3,0):
            rr=max(float(h5[i]-l5[i]),1e-12)
            body=abs(float(c5[i]-o5[i]))/rr
            bodies.append(body)
            directional.append(1 if float(c5[i])>float(o5[i]) else -1 if float(c5[i])<float(o5[i]) else 0)
        dir_count=sum(1 for x in directional if x==direction)
        body_avg=float(np.mean(bodies)) if bodies else 0.0
        last_body=float(bodies[-1]) if bodies else 0.0
        level=float(pullback.get("level") or 0.0)
        above_breakout=(px>level if direction>0 else px<level) if level>0 else False
        continuation_gap=abs(px-level)/max(px,1e-12) if level>0 else 0.0
        recent_range=max(float(np.max(h5[-6:-1])-np.min(l5[-6:-1])),1e-12)
        recent_move=abs(float(c5[-1]-c5[-4]))/max(recent_range,1e-12)
        acceleration_structure=(
            direction*bos15.get("bos",0)>=0.45 or
            direction*disp15.get("score",0)>=0.65 or
            direction*n15.get("score",0)>=0.65
        )
        acceleration_trigger=(trigger_score>=0.45 and trigger.get("aligned_votes",0)>=1)
        # At least 2 of the latest 3 completed 5m candles must continue in the
        # same direction, with real body expansion. This avoids turning every
        # breakout into an automatic chase entry.
        acceleration_follow=bool(
            _ALLOW_ACCELERATION_CHASE and
            above_breakout and
            continuation_gap>=0.0020 and
            dir_count>=2 and
            body_avg>=0.52 and
            last_body>=0.60 and
            recent_move>=0.45 and
            acceleration_structure and
            acceleration_trigger and
            not reverse_confirmed and
            not reverse_watch
        )
        if acceleration_follow:
            acceleration_reason=(
                "强趋势加速未给回踩：最近3根5m至少2根同向、实体扩张、"
                "15m结构延续且5m触发确认；改为小仓跟随，不强等回踩"
            )
            pullback={**pullback,"state":"ACCELERATION_FOLLOW","confirmed":True,
                      "active":False,"reason":acceleration_reason,"follow_mode":True,
                      "size_multiplier":0.50}
            PULLBACK_STATE.pop(symbol or "__unknown__",None)

    must_wait_pullback = (
        bool(pullback.get("active") and not pullback.get("confirmed")) or
        bool((near_bad or chase) and breakout_path and not pullback.get("confirmed"))
    )
    if must_wait_pullback:
        return {"decision":"WAIT","score":score,"tier":"观察","reason":str(pullback.get("reason") or "突破已形成但当前价格偏延伸，等待回踩确认；不追单"),
                "position":pos,"location_score":location_score,"chase":chase,"aligned_count":len(aligned),"aligned_categories":aligned_categories,"path":"智能回踩","pullback":pullback}
    # Hard safety/quality gates are few: direction conflict, weak trigger,
    # severe chase, or no coherent price-action path.  This keeps FAST active
    # without reopening the old "one candle = entry" behavior.
    if reverse_confirmed:
        return {"decision":"WAIT","score":score,"tier":"不做","reason":"15m关键结构破坏+反向动能形成组合确认，判定真反转，等待重新确认",
                "position":pos,"location_score":location_score,"chase":chase,"aligned_count":len(aligned),"aligned_categories":aligned_categories,"path":"冲突","opposite_score":opposite,"reverse_structural_votes":reverse_structural_votes,"reverse_total_votes":reverse_total_votes,"reverse_categories":reverse_categories,"countertrend_15m":countertrend_15m,"reversal_path_15m":reversal_path_15m}
    if reverse_watch:
        return {"decision":"WAIT","score":score,"tier":"观察","reason":"15m出现多项反向证据，但尚未形成关键结构+动能的真反转；等待下一轮确认",
                "position":pos,"location_score":location_score,"chase":chase,"aligned_count":len(aligned),"aligned_categories":aligned_categories,"path":"反向观察","opposite_score":opposite,"reverse_structural_votes":reverse_structural_votes,"reverse_total_votes":reverse_total_votes,"reverse_categories":reverse_categories,"countertrend_15m":countertrend_15m,"reversal_path_15m":reversal_path_15m}
    if countertrend_15m and not reversal_path_15m:
        return {"decision":"WAIT","score":score,"tier":"观察","reason":"15m仍处于明显反向结构，5m单独触发不足；等待15m先出现结构翻转+动能/回收确认",
                "position":pos,"location_score":location_score,"chase":chase,"aligned_count":len(aligned),"aligned_categories":aligned_categories,"path":"15m反向待翻转",
                "countertrend_15m":True,"reversal_path_15m":False,"reverse_categories":reverse_categories}
    if not trigger_ok:
        return {"decision":"WAIT","score":score,"tier":"不做","reason":"5m触发质量不足，等待更清晰的裸K触发",
                "position":pos,"location_score":location_score,"chase":chase,"aligned_count":len(aligned),"aligned_categories":aligned_categories,"path":"触发不足"}
    if not path_ok:
        return {"decision":"WAIT","score":score,"tier":"不做","reason":"15m尚未形成完整的裸K进场路径",
                "position":pos,"location_score":location_score,"chase":chase,"aligned_count":len(aligned),"aligned_categories":aligned_categories,"path":"结构不足"}
    if near_bad and not breakout_path:
        return {"decision":"WAIT","score":score,"tier":"不做","reason":"当前位置过度追高/追低，等待回踩或重新确认",
                "position":pos,"location_score":location_score,"chase":chase,"aligned_count":len(aligned),"aligned_categories":aligned_categories,"path":"追价"}
    if chase and not breakout_path:
        return {"decision":"WAIT","score":score,"tier":"不做","reason":"5m实体扩张后已经延伸，避免日内裸K追单",
                "position":pos,"location_score":location_score,"chase":chase,"aligned_count":len(aligned),"aligned_categories":aligned_categories,"path":"追价"}

    # 修补：裸结构/动量突破 与 OB∩FVG回补（15m无完整回踩、也不是扫盘MSS）都必须再等到
    # 5m出现“突破回踩重新站回”或“扫流动性后收回”二次确认，不打突破第一根、不接未确认的机构区。
    # 回放统计：未确认的“结构触发/OB回补”在震荡里容易被均值回归反打；“突破回踩”路径期望显著为正。
    need_5m_confirm = (structure_path or mitigation_path) and not breakout_path and not sweep_path and not acceleration_follow
    if _REQUIRE_5M_RETEST_FOR_STRUCTURE and need_5m_confirm:
        trig_struct=list(trigger.get("structural_evidence") or [])
        retest5=("突破回踩" in trig_struct) or ("流动性扫盘" in trig_struct)
        if not retest5:
            return {"decision":"WAIT","score":score,"tier":"观察",
                    "reason":"15m结构/机构区已出现但5m尚未回踩/扫盘收回确认，等待重新站回再进，不抢第一根",
                    "position":pos,"location_score":location_score,"chase":chase,
                    "aligned_count":len(aligned),"aligned_categories":aligned_categories,"path":"等待5m回踩"}

    # v2位置门：只硬拦“区间正中间无人区”（盈亏比最差、上下都被动）。
    # 错误半区(浅溢价/浅折价)的趋势延续不硬拦，改为综合分轻度扣分+降档处理；
    # 突破回踩、扫盘MSS、OB∩FVG回补 三类机构路径位置已定义好，豁免。
    if not acceleration_follow and no_mans_land and not location_exempt:
        return {"decision":"WAIT","score":score,"tier":"观察",
                "reason":"价格处于15m区间正中间无人区（非折扣/溢价边缘、无机构区），等回踩到边缘或机构区再进",
                "position":pos,"location_score":location_score,"chase":chase,
                "aligned_count":len(aligned),"aligned_categories":aligned_categories,
                "path":"无人区过滤","deal_pos":round(float(deal.get("pos",0.5)),2)}

    # v2震荡门（克制版）：15m效率比过低=来回拉锯时，只拦两类最容易被均值回归反打的单——
    #   ①假突破(breakout_path)；②不在正确半区的中部结构单。
    # 保留：正确半区(多在折扣/空在溢价)的边缘顺势回踩、区间边缘扫盘MSS、机构区(OB∩FVG)回补。
    _chop = _CHOP_ER_MAX>0 and er15 < _CHOP_ER_MAX and not sweep_path and not mitigation_path
    _chop_block = _chop and (breakout_path or (structure_path and not good_zone))
    if _chop_block:
        return {"decision":"WAIT","score":score,"tier":"观察",
                "reason":f"15m趋势效率比{er15:.2f}过低（拉锯震荡），回避假突破/中部结构单，等边缘回踩或趋势展开",
                "position":pos,"location_score":location_score,"chase":chase,
                "aligned_count":len(aligned),"aligned_categories":aligned_categories,
                "path":"震荡过滤","er15":round(er15,2)}

    # 改进4：突破型路径必须有量能配合，无量突破假突破概率高；扫盘反转/OB回补是回踩性质，不卡量，数据不足也不拦截。
    needs_volume=bool((breakout_path or (structure_path and not mitigation_path)) and not sweep_path)
    if needs_volume and len(v5)>=21 and vol_ratio<0.9:
        return {"decision":"WAIT","score":score,"tier":"观察","reason":f"突破量能不足（近3根/前18根量比={vol_ratio:.2f}<0.90），等待放量确认，防假突破",
                "position":pos,"location_score":location_score,"chase":chase,"aligned_count":len(aligned),"aligned_categories":aligned_categories,"path":"无量突破","vol_ratio":vol_ratio}

    # 改进3：一般信号也要达到最低综合分0.42，砍掉尾部低质量单；强趋势加速跟随有独立严格条件，予以豁免。
    if not acceleration_follow and score < 0.42:
        return {"decision":"WAIT","score":score,"tier":"不做","reason":f"入场综合分{score:.2f}<0.42，整体质量偏低不参与",
                "position":pos,"location_score":location_score,"chase":chase,"aligned_count":len(aligned),"aligned_categories":aligned_categories,"path":"综合分不足","vol_ratio":vol_ratio}

    # Entry tiers: 很强/强 use full risk budget; 一般也允许参与，但降仓。
    # 一般仍必须通过前面的硬安全门、5m触发和完整裸K路径，避免变成“有方向就进”。
    entry_size_multiplier=0.50 if acceleration_follow else 1.0
    if acceleration_follow:
        tier="加速跟随"
    elif score>=0.68 and len(aligned)>=3 and trigger_score>=0.45 and location_score>=0.35:
        tier="很强"
    elif score>=0.52 and len(aligned)>=2 and trigger_score>=0.30 and location_score>=0.25:
        tier="强"
    else:
        tier="一般"
        entry_size_multiplier=0.50

    if acceleration_follow:
        path_label="加速跟随"; path_reason=acceleration_reason
    elif mitigation_path:
        path_label="OB/FVG回补"; path_reason="同向订单块∩FVG回补确认"
    elif breakout_path:
        path_label="突破回踩"; path_reason="突破→回踩重新站回"
    elif sweep_path:
        path_label="扫盘MSS"; path_reason="流动性扫损→CHoCH位移→确认"
    else:
        path_label="结构触发"; path_reason="结构+5m回踩触发"
    return {
        "decision":"ENTER","score":score,"tier":tier,
        "reason":path_reason+f"；位于15m{deal.get('zone','区间')}，位置可参与；非明显追价",
        "position":pos,"location_score":location_score,"chase":chase,
        "aligned_count":len(aligned),"path":path_label,
        "deal_zone":deal.get("zone"),"deal_pos":round(float(deal.get("pos",0.5)),2),
        "ob_fvg_mitigation":bool(mitigation_path),
        "evidence_count":len(aligned),"strong_evidence_count":len(strong),"evidence_categories":aligned_categories,
        "entry_size_multiplier":entry_size_multiplier,
        "opposite_score":opposite,
        "reverse_structural_votes":reverse_structural_votes,
        "reverse_total_votes":reverse_total_votes,
        "reverse_single":reverse_single,
        "reverse_confirmed":reverse_confirmed,
        "pullback":pullback,
    }


# =====================================================================================
# FAST v4「regime 双引擎」：1H/4H 定量 regime 闸门 + 区间均值回归 sleeve + 趋势扫损 sleeve
# -------------------------------------------------------------------------------------
# 真实数据回测（6币/199天5m，事件驱动，见 backtest/lab_*.py 与 v4_bt.py）的诚实结论：
#   * 单一“顺向扫损MSS”(v3) 在散户 taker 成本下胜率约38%、PF≈0.57，方向有微小正 edge
#     但盖不住成本；
#   * 独立复现发现：区间内“布林/VWAP 极值均值回归”具有正的毛 edge（高胜率 sleeve），而趋势
#     市的回踩/扫损延续是另一条 edge；二者在相反行情里互补，由高周期 regime 闸门二选一；
#   * 把胜率做高（分批+保本）与把盈亏比做大（让利润奔跑）是两个方向，v4 用 regime 分工：
#     区间 sleeve 求“高胜率、maker 挂单、就近回 VWAP”，趋势 sleeve 求“少而精、分批+保本+
#     让剩余仓位奔跑”。任何“60%胜率+高盈亏比+高频率+散户taker成本”同时成立的说法都不可信。
# 开关：FAST_V4_ENSEMBLE=True 走 v4；False 完全回退 v3（接口/字段保持兼容）。
FAST_V4_ENSEMBLE = True
# 趋势延续 sleeve（5m 顺向扫损MSS）在 199 天 INS/OOS 独立回测中均未取得扣费后正 edge
# （OOS 胜率约39%、PF≈0.46），默认关闭：趋势 regime 下宁可空仓等待，不硬做边际单。
# 区间均值回归 sleeve 两期都稳定在 ~63% 胜率，是 v4 的主力。想保留 v3 趋势打法可置 True。
FAST_V4_TREND_SLEEVE = False

# ============================================================================
# v5 版本选择器（v3 / v4 / v4_trend / v5）
#   - 默认 "v4"，与历史行为完全一致；v3/v4/v4_trend 仍由 FAST_V4_ENSEMBLE /
#     FAST_V4_TREND_SLEEVE 两个布尔开关驱动，旧分支逻辑零改动。
#   - 仅当显式置为 "v5" 时，_build_decision 才走新增的 _build_decision_v5。
#   - alpha_engine.set_fast_engine(version) 运行期写入并持久化；回测脚本直接拨
#     FAST_V4_ENSEMBLE 时不受影响（默认非 v5）。
# ============================================================================
FAST_ACTIVE_VERSION = "v4"

# ---- v5 参数（粗、圆；在 31 个小币 + 6 主流币 199 天 INS/OOS 上验证，未做单币过拟合） ----
# v5 区间回归在 v4 基础上增加三道“区间质量闸门”，拒绝在强趋势延伸 / 4H 已走趋势 /
# 布林带爆炸式扩张时逆势接飞刀，并要求极值收回 K 线实体半区确认。
FAST_V5_MR_BODY      = True
FAST_V5_MR_MAX_EXT1H = 2.5    # 收盘相对 1H EMA21 乖离 > 2.5×ATR1H 不逆势（置 >1e9 关闭）
FAST_V5_MR_MAX_4HGAP = 0.8    # |4H EMA21/50 间距(ATR4归一)| > 0.8 不逆势（置 >1e9 关闭）
FAST_V5_MR_BBW_X     = 1.3    # 5m 布林带宽 > 1.3×其近50根均值（波动爆炸）不逆势（置 >1e9 关闭）
# v5 趋势回踩延续 sleeve：31 小币 INS/OOS 事件驱动回测 PF≈0.86-0.97（扣费后不稳健），
# 默认关闭；保留实现与开关，交用户充分模拟后自行决定，绝不默认拿真钱跑未验证 sleeve。
FAST_V5_TREND_SLEEVE    = os.getenv("FAST_V5_TREND_SLEEVE", "0") == "1"
FAST_V5_TR_EXTEND_ATR   = 2.5
FAST_V5_TR_STOP_MIN_ATR = 1.3
FAST_V5_TR_STOP_MAX_ATR = 2.6
FAST_V5_TR_RR           = 2.0
FAST_V5_TR_FLOOR_PCT    = 0.006


def set_active_version(version: str) -> str:
    """供 alpha_engine 运行期切换策略版本；返回实际生效版本。"""
    global FAST_ACTIVE_VERSION
    v = str(version or "v4").strip().lower()
    if v not in ("v3", "v4", "v4_trend", "v5", "v6", "v62", "v7"):
        v = "v4"
    FAST_ACTIVE_VERSION = v
    return v


def get_active_version() -> str:
    return FAST_ACTIVE_VERSION


_V4 = {
    # —— regime 闸门（1H 定趋势、15m/1H 效率比定区间、4H 不强烈反向）——
    "trend_er": 0.30, "trend_gap": 0.30, "htf_gap": 0.50,
    "chop_er_15": 0.25, "chop_er_1h": 0.32, "range_gap_4h": 1.20,
    # —— 区间均值回归 sleeve（真实回测：宽止损≈2×ATR15 毛edge最好、成本/R最低）——
    "bb_n": 20, "bb_k": 2.0, "rsi_n": 2, "rsi_ovs": 20.0, "rsi_obv": 80.0,
    "rsi_extreme": 12.0, "band_touch": 0.0005,
    "mr_sl_lo": 1.9, "mr_sl_hi": 2.6, "mr_rr": 1.3,
    # 止损绝对地板：真实回测中 <0.4% 的微型止损被成本/噪声主导（PF≈0.5），宽到 0.45~0.8% 才显 edge；
    # 结构止损不足该宽度直接放弃，不硬做。INS/OOS 两期独立验证（非过拟合）。
    "mr_sl_min_pct": 0.004,
}


def _rsi_arr(close: np.ndarray, n: int = 2) -> np.ndarray:
    """Wilder RSI 序列（只用已收盘K；模块不依赖 pandas）。"""
    x = np.asarray(close, float); m = len(x)
    out = np.full(m, 50.0)
    if m <= n:
        return out
    d = np.diff(x, prepend=x[0]); up = np.maximum(d, 0.0); dn = np.maximum(-d, 0.0)
    au = float(np.mean(up[1:n + 1])); ad = float(np.mean(dn[1:n + 1]))
    for i in range(n + 1, m):
        au = (au * (n - 1) + up[i]) / n; ad = (ad * (n - 1) + dn[i]) / n
        out[i] = 100.0 if ad <= 1e-12 else 100.0 - 100.0 / (1.0 + au / ad)
    return out


def _bollinger(frame: Dict[str, np.ndarray], n: int = 20, k: float = 2.0) -> Dict[str, float]:
    """最新一根已收盘K的布林中轨/上下轨/标准差（SMA 口径）。"""
    c = frame.get("close", np.array([])); m = len(c)
    if m < n:
        return {"ok": False, "mid": float(c[-1]) if m else 0.0, "up": 0.0, "dn": 0.0, "sd": 0.0}
    seg = c[-n:]
    mid = float(np.mean(seg)); sd = float(np.std(seg))
    return {"ok": True, "mid": mid, "sd": sd, "up": mid + k * sd, "dn": mid - k * sd}


def _v4_regime(frames: Dict[str, Any]) -> Dict[str, Any]:
    """定量 regime：1=多头趋势 / -1=空头趋势 / 0=区间 / 9=中性不做。只用已收盘K。"""
    f15 = frames["15m"]; f1 = frames["1h"]; f4 = frames["4h"]
    c15 = f15["close"]; c1 = f1["close"]; c4 = f4["close"]
    if len(c15) < 55 or len(c1) < 55 or len(c4) < 30:
        return {"regime": 9, "label": "高周期K不足"}
    a15 = _atr_arr(f15, 14); a1 = _atr_arr(f1, 14)
    e21_15 = _ema_arr(c15, 21); e50_15 = _ema_arr(c15, 50)
    e21_1 = _ema_arr(c1, 21); e50_1 = _ema_arr(c1, 50)
    e21_4 = _ema_arr(c4, 21); e50_4 = _ema_arr(c4, 50)
    er15 = _efficiency_ratio(c15, 14); er1 = _efficiency_ratio(c1, 14)
    au1 = float(a1[-1]) if np.isfinite(a1[-1]) and a1[-1] > 0 else float(np.median(np.maximum(
        f1["high"][-20:] - f1["low"][-20:], 1e-12)))
    au4 = float(_atr_arr(f4, 14)[-1]); au4 = au4 if np.isfinite(au4) and au4 > 0 else 1.0
    gap1 = float((e21_1[-1] - e50_1[-1]) / max(au1, 1e-12))
    gap4 = float((e21_4[-1] - e50_4[-1]) / max(au4, 1e-12))
    slope1 = float((e21_1[-1] - e21_1[-6]) / max(abs(e21_1[-6]), 1e-12))
    px = float(c15[-1])
    tup = bool(gap1 > _V4["trend_gap"] and slope1 > 0 and er1 >= _V4["trend_er"]
               and px > e21_1[-1] and gap4 > -_V4["htf_gap"])
    tdn = bool(gap1 < -_V4["trend_gap"] and slope1 < 0 and er1 >= _V4["trend_er"]
               and px < e21_1[-1] and gap4 < _V4["htf_gap"])
    rangey = bool((not tup) and (not tdn) and er15 < _V4["chop_er_15"]
                  and er1 < _V4["chop_er_1h"] and abs(gap4) < _V4["range_gap_4h"])
    if tup:
        reg, label = 1, "多头趋势"
    elif tdn:
        reg, label = -1, "空头趋势"
    elif rangey:
        reg, label = 0, "区间震荡"
    else:
        reg, label = 9, "中性过渡"
    return {"regime": reg, "label": label, "er15": round(er15, 3), "er1h": round(er1, 3),
            "gap1h": round(gap1, 2), "gap4h": round(gap4, 2), "slope1h": round(slope1, 4)}


def _v4_meanrev(frames: Dict[str, Any], px: float) -> Dict[str, Any]:
    """区间 sleeve：5m 布林极值 + RSI2 超买超卖 +（扫损或极端超卖）共振，回归 VWAP/中轨。

    高胜率打法，天然适合在极值挂 post-only maker 限价单；止损放宽到 15m ATR 失效位，
    把“成本/R”压到最小；目标就近看会话 VWAP，达不到最低盈亏比则按兜底 RR。
    """
    f5 = frames["5m"]; f15 = frames["15m"]
    h5 = f5["high"]; l5 = f5["low"]; c5 = f5["close"]; o5 = f5["open"]; v5 = f5.get("volume")
    h15 = f15["high"]; l15 = f15["low"]; c15 = f15["close"]
    if len(c5) < 30 or len(c15) < 30 or px <= 0:
        return {"ok": False, "score": 0.0, "tier": "不做", "path": "-", "evidence": [],
                "entry_size_multiplier": 0.0, "reason": "已收盘K不足"}
    bb = _bollinger(f5, _V4["bb_n"], _V4["bb_k"])
    rsi = _rsi_arr(c5, _V4["rsi_n"])
    vwap5 = _vwap_arr(f5, 96)
    atr15 = _atr_arr(f15, 14)
    au = float(atr15[-1]) if np.isfinite(atr15[-1]) and atr15[-1] > 0 else float(np.median(
        np.maximum(h15[-20:] - l15[-20:], 1e-12)))
    r0, r1v = float(rsi[-1]), float(rsi[-2]) if len(rsi) > 1 else 50.0
    vw = float(vwap5[-1]) if np.isfinite(vwap5[-1]) else float(bb["mid"])
    tol = _V4["band_touch"]
    # 近3根是否扫掉近10根小级别低/高点（流动性诱导）
    sweep_lo = bool(np.min(l5[-3:]) < float(np.min(l5[-11:-3])))
    sweep_hi = bool(np.max(h5[-3:]) > float(np.max(h5[-11:-3])))
    med_v = float(np.median(v5[-21:-1])) if v5 is not None and len(v5) >= 21 else 0.0
    vol_climax = bool(med_v > 0 and v5[-1] >= 1.5 * med_v)
    evidence: List[str] = []

    def _level(side: str, anchor: float, target: float, score: float, ev: List[str]) -> Dict[str, Any]:
        stop = float(np.clip(anchor + 0.4 * au, _V4["mr_sl_lo"] * au, _V4["mr_sl_hi"] * au))
        sl_pct = stop / px
        if sl_pct < _V4["mr_sl_min_pct"]:
            return {"ok": False, "score": 0.0, "tier": "不做", "path": "-", "evidence": ev,
                    "entry_size_multiplier": 0.0,
                    "reason": f"结构止损仅{sl_pct:.2%}<{_V4['mr_sl_min_pct']:.2%}地板，成本/噪声占比过高，放弃"}
        tp_dist = max(abs(target - px), _V4["mr_rr"] * stop)
        tp_pct = tp_dist / px
        if sl_pct < 0.0015 or sl_pct > 0.05 or tp_pct <= sl_pct:
            return {"ok": False, "score": 0.0, "tier": "不做", "path": "-", "evidence": ev,
                    "entry_size_multiplier": 0.0, "reason": f"区间单 TP/SL 距离异常({sl_pct:.2%})"}
        if side == "LONG":
            tp_px, sl_px = px + tp_dist, px - stop
        else:
            tp_px, sl_px = px - tp_dist, px + stop
        rr = tp_pct / sl_pct
        if score >= 0.72:
            tier, mult = "很强", 1.0
        elif score >= 0.60:
            tier, mult = "强", 0.85
        else:
            tier, mult = "一般", 0.7
        return {"ok": True, "side": side, "score": round(float(score), 3), "tier": tier,
                "path": "区间极值回归", "evidence": ev, "entry_size_multiplier": mult,
                "rr": rr, "sl_pct": sl_pct, "tp_pct": tp_pct, "tp_price": tp_px, "sl_price": sl_px,
                "maker_preferred": True,
                "reason": "区间极值回归（建议post-only maker挂单）：" + "、".join(ev)}

    # —— 多头：刺破/收破下轨后强势收回轨内，且（扫损 或 RSI 极端 或 量能高潮）——
    long_reclaim = bool(l5[-1] <= bb["dn"] * (1 + tol) and c5[-1] > bb["dn"] and c5[-1] >= o5[-1])
    long_conf = bool(sweep_lo or r0 <= _V4["rsi_extreme"] or vol_climax)
    if long_reclaim and r0 <= _V4["rsi_ovs"] and long_conf:
        anchor = max(px - float(np.min(l15[-6:])), 0.0)
        target = vw if vw > px else float(bb["mid"])
        ev = ["5m下轨收回", f"RSI2={r0:.0f}"]
        if sweep_lo: ev.append("下扫流动性")
        if vol_climax: ev.append("放量拒绝")
        if vw > px: ev.append("回归VWAP")
        score = float(np.clip(0.55 + 0.12 * (sweep_lo) + 0.08 * (r0 <= _V4["rsi_extreme"])
                              + 0.05 * vol_climax + 0.07 * (vw > px), 0.0, 0.85))
        return _level("LONG", anchor, target, score, ev)
    # —— 空头：镜像 ——
    short_reclaim = bool(h5[-1] >= bb["up"] * (1 - tol) and c5[-1] < bb["up"] and c5[-1] <= o5[-1])
    short_conf = bool(sweep_hi or r0 >= 100 - _V4["rsi_extreme"] or vol_climax)
    if short_reclaim and r0 >= _V4["rsi_obv"] and short_conf:
        anchor = max(float(np.max(h15[-6:])) - px, 0.0)
        target = vw if 0 < vw < px else float(bb["mid"])
        ev = ["5m上轨收回", f"RSI2={r0:.0f}"]
        if sweep_hi: ev.append("上扫流动性")
        if vol_climax: ev.append("放量拒绝")
        if 0 < vw < px: ev.append("回归VWAP")
        score = float(np.clip(0.55 + 0.12 * sweep_hi + 0.08 * (r0 >= 100 - _V4["rsi_extreme"])
                              + 0.05 * vol_climax + 0.07 * (0 < vw < px), 0.0, 0.85))
        return _level("SHORT", anchor, target, score, ev)
    return {"ok": False, "score": 0.0, "tier": "不做", "path": "等待区间极值", "evidence": [],
            "entry_size_multiplier": 0.0,
            "reason": f"区间内未出现合格的极值收回（RSI2={r0:.0f}，等待布林外收回+共振）"}


def _build_decision_v4(symbol: str, data: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    """v4 regime 双引擎决策；输出 schema 与 v3 完全一致，额外在 fast_strategy 内带 v4 字段。"""
    frames = data["frames"]; px = data["ticker_last"]
    reg = _v4_regime(frames); regime = int(reg["regime"])
    spread_bps = float(data.get("spread_bps", -1.0) or -1.0)
    if spread_bps >= 0 and spread_bps > 20.0:
        return _decision_flat(symbol, f"买卖点差过宽({spread_bps:.1f}bps>20bps)，本轮不做", ctx, px, data,
                              extras={"tier": "不做"})
    engine = ""; entry: Dict[str, Any] = {}; tp_sl: Dict[str, Any] = {}; side = ""; direction = 0
    if regime in (1, -1):
        if not FAST_V4_TREND_SLEEVE:
            return _decision_flat(symbol, f"[趋势regime] {reg['label']}：5m趋势延续单OOS未验证正edge，默认空仓等待区间极值（FAST_V4_TREND_SLEEVE=False）",
                                  ctx, px, data, extras={"tier": "不做"})
        direction = regime
        entry = _v3_setup(frames, direction, px)
        if not entry.get("ok"):
            return _decision_flat(symbol, f"[趋势regime] {entry.get('reason','裸K共振不足')}", ctx, px, data,
                                  extras={"tier": "不做", "entry": entry})
        side = "LONG" if direction > 0 else "SHORT"
        tp_sl = _fast_tp_sl(frames["15m"], frames["5m"], side, px, min_rr=float(entry.get("rr", 1.4)))
        engine = "TREND_SWEEP"
    elif regime == 0:
        entry = _v4_meanrev(frames, px)
        if not entry.get("ok"):
            return _decision_flat(symbol, f"[区间regime] {entry.get('reason','无合格极值回归')}", ctx, px, data,
                                  extras={"tier": "不做", "entry": entry})
        side = str(entry["side"]); direction = 1 if side == "LONG" else -1
        tp_sl = {"ok": True, "tp": float(entry["tp_pct"]), "sl": float(entry["sl_pct"]),
                 "tp_price": float(entry["tp_price"]), "sl_price": float(entry["sl_price"]),
                 "rr": float(entry["rr"]),
                 "reason": "区间回归：15m-ATR失效位止损 + 就近VWAP/中轨目标（建议maker进场）"}
        engine = "RANGE_REVERSION"
    else:
        return _decision_flat(symbol, f"regime中性过渡（{reg['label']}，ER15={reg.get('er15')}/ER1H={reg.get('er1h')}），趋势与区间均不做",
                              ctx, px, data, extras={"tier": "不做"})
    if not tp_sl.get("ok"):
        return _decision_flat(symbol, "结构成立但 TP/SL 距离异常，等待更好入场", ctx, px, data,
                              extras={"tier": entry.get("tier", "不做"), "entry": entry, "tp_sl": tp_sl})

    s15 = ctx["15m"]
    sweep15 = _liquidity_sweep(frames["15m"]); pools15 = _liquidity_pools(frames["15m"])
    disp15 = _displacement(frames["15m"]); fvg15 = _fvg(frames["15m"]); ob15 = _order_block(frames["15m"])
    pd15 = _premium_discount(frames["15m"]); loc15 = _price_location(frames["15m"])
    trigger = _trigger_quality(frames["5m"], frames["15m"], direction)
    score = float(entry.get("score", 0.6) or 0.6)
    maker_preferred = bool(entry.get("maker_preferred", engine == "RANGE_REVERSION"))
    reason = f"[v4 {reg['label']}/{ '区间回归' if engine=='RANGE_REVERSION' else '趋势扫损' }] " \
             f"路径={entry.get('path')}；共振={'、'.join(entry.get('evidence', [])) or '无'}；" \
             f"评分={score:.2f}；TP/SL=1:{tp_sl['rr']:.2f}；{'建议maker挂单' if maker_preferred else '市价/回踩进场'}；{tp_sl.get('reason','')}"
    return {
        "signal": side,
        "reason": reason,
        "confidence": score, "raw_confidence": score, "entry_threshold": 0.50,
        "signal_tier": entry.get("tier", "一般"), "directional_margin": score,
        "base_tp": tp_sl["tp"], "base_sl": tp_sl["sl"], "tp": tp_sl["tp"], "sl": tp_sl["sl"],
        "horizon": 16, "strategy_mode": "FAST", "strategy_label": "日内裸K快速实盘(v4双引擎)",
        "model_ready": True, "version": "ALPHA-X-FAST-PRICE-ACTION-5.0-REGIME-ENSEMBLE",
        "timeframe": "5m触发/15m结构/1H-4H regime",
        "market_context": {"regime": f"FAST-v4/{reg['label']}", "stress": 0.0,
                           "spread_bps": float(spread_bps), "orderbook_imbalance": 0.0,
                           "funding_rate": 0.0, "open_interest": 0.0,
                           "oi_change_pct": 0.0, "no_trade": bool(data["missing"]), "reasons": data["missing"]},
        "adaptive_context": {"enabled": True, "regime": {"label": f"v4/{reg['label']}", **reg},
                             "multi_timeframe": ctx, "lead_lag": {}, "size_multiplier": 1.0},
        "fast_entry_size_multiplier": float(entry.get("entry_size_multiplier", 1.0) or 1.0),
        "strategy_committee": {"signal": side, "agreement": ctx.get("agreement", 0.0), "committee_score": score},
        "fast_data": data["summary"],
        "entry_price_confirmation": {
            "enabled": True, "decision": "ENTER", "score": score, "entry_price": px,
            "entry_size_multiplier": float(entry.get("entry_size_multiplier", 1.0) or 1.0),
            "entry_quality": entry.get("tier", "一般"),
            "reason": "FAST v4 进场确认：" + entry.get("reason", ""),
            "factors": {"进场评分": score, "regime": reg["label"], "引擎": engine,
                        "5m触发": float(trigger.get("score", 0.0)),
                        "非追价": 1.0 if engine == "RANGE_REVERSION" else 0.0}},
        "fast_strategy": {
            "version": "5.0-REGIME-ENSEMBLE", "v4_regime": reg, "v4_engine": engine,
            "maker_preferred": maker_preferred,
            "entry_size_multiplier": float(entry.get("entry_size_multiplier", 1.0) or 1.0),
            "tier": entry.get("tier", "一般"), "path": entry.get("path", engine),
            "evidence": entry.get("evidence", []), "context": ctx, "trigger": trigger,
            "liquidity_sweep": sweep15, "liquidity_pools": pools15, "displacement": disp15,
            "fvg": fvg15, "order_block": ob15, "premium_discount": pd15, "price_location": loc15,
            "entry_quality": entry, "structure_score": score, "context_score": score,
            "trigger_score": float(trigger.get("score", 0.0)), "er15": float(reg.get("er15", 0.0)),
            "adaptive_tp_pct": tp_sl["tp"], "adaptive_sl_pct": tp_sl["sl"],
            "tp_price": tp_sl["tp_price"], "sl_price": tp_sl["sl_price"],
            "rr": tp_sl["rr"], "tp_sl_reason": tp_sl.get("reason", "")},
        "fast_ownership": {"entry_owner": "FAST裸K-v4", "initial_tp_sl_owner": "FAST裸K-v4",
                           "post_entry_owner": "原有执行/持仓管理(分批止盈/趋势跟踪)",
                           "post_entry_tp_sl_recalculation": False}}


def _v3_setup(frames: Dict[str, Any], direction: int, px: float) -> Dict[str, Any]:
    """FAST v3 裸K进场评估（只用已收盘K）。

    主线（真实数据上唯一有正向前沿edge的打法）= 顺大方向的“流动性扫损 + 收回(MSS)”：
      1H 定偏向 → 15m EMA/结构同向 → 价格回踩扫掉近期小级别低/高点(诱单/止损流动性)后强势收回
      → 5m 精确触发(扫损收回 / 重新站回VWAP / BOS) → 在折扣区或VWAP附近接，不追垂直脉冲。
    副线 = 强趋势中回踩会话VWAP后重新站回的趋势延续单（放宽“必须等15m扫损”，保证接得到单）。
    """
    f5 = frames["5m"]; f15 = frames["15m"]
    h15 = f15["high"]; l15 = f15["low"]; c15 = f15["close"]; o15 = f15["open"]; v15 = f15["volume"]
    h5 = f5["high"]; l5 = f5["low"]; c5 = f5["close"]; o5 = f5["open"]; v5 = f5["volume"]
    if len(c15) < 30 or len(c5) < 30 or px <= 0:
        return {"ok": False, "score": 0.0, "tier": "不做", "path": "-", "evidence": [],
                "location_score": 0.0, "chase": True, "entry_size_multiplier": 0.0,
                "reason": "已收盘K线不足，等待数据"}
    d = int(direction)
    ema21_15 = _ema_arr(c15, 21); ema50_15 = _ema_arr(c15, 50)
    vwap15 = _vwap_arr(f15, 48); vwap5 = _vwap_arr(f5, 96)
    atr15 = _atr_arr(f15, 14)
    a15 = float(atr15[-1]) if np.isfinite(atr15[-1]) and atr15[-1] > 0 else float(np.median(np.maximum(h15[-20:] - l15[-20:], 1e-12)))
    er15 = _efficiency_ratio(c15, 14)
    rng15 = max(float(h15[-1] - l15[-1]), 1e-12)
    body_pos15 = float((c15[-1] - l15[-1]) / rng15)

    evidence: List[str] = []
    score = 0.0

    # --- 1) 15m EMA 趋势同向 ---
    if d > 0:
        ema_align = bool(ema21_15[-1] >= ema50_15[-1] and c15[-1] >= ema50_15[-1] * 0.999)
    else:
        ema_align = bool(ema21_15[-1] <= ema50_15[-1] and c15[-1] <= ema50_15[-1] * 1.001)
    if ema_align:
        score += 0.16; evidence.append("15mEMA同向")

    # --- 2) 主线：近3根15m内扫掉前期小级别低/高点后，最新一根强势收回 ---
    ref_lo = float(np.min(l15[-13:-3])); ref_hi = float(np.max(h15[-13:-3]))
    if d > 0:
        raided = bool(np.min(l15[-3:]) < ref_lo)
        reclaimed = bool(c15[-1] > ref_lo and body_pos15 >= 0.5)
    else:
        raided = bool(np.max(h15[-3:]) > ref_hi)
        reclaimed = bool(c15[-1] < ref_hi and body_pos15 <= 0.5)
    sweep_ok = bool(raided and reclaimed)
    if sweep_ok:
        score += 0.22; evidence.append("15m顺向扫损收回")

    # --- 3) 5m 精确触发（扫损收回 / 重新站回VWAP / 同向BOS）---
    sw5 = _liquidity_sweep(f5)
    s5 = _structure(f5); bos5 = _bos_choch(f5, s5)
    vw5_now = float(vwap5[-1]); vw5_prev = float(vwap5[-2]) if len(vwap5) > 1 and np.isfinite(vwap5[-2]) else vw5_now
    if d > 0:
        trig_sweep = bool(sw5["score"] >= 0.18)
        trig_vwap = bool(c5[-2] <= vw5_prev and c5[-1] > vw5_now and (c5[-1] - o5[-1]) > 0)
        trig_bos = bool(float(bos5.get("bos", 0)) >= 0.4)
    else:
        trig_sweep = bool(sw5["score"] <= -0.18)
        trig_vwap = bool(c5[-2] >= vw5_prev and c5[-1] < vw5_now and (c5[-1] - o5[-1]) < 0)
        trig_bos = bool(float(bos5.get("bos", 0)) <= -0.4)
    trig_labels = []
    if trig_sweep: trig_labels.append("5m扫损收回")
    if trig_vwap: trig_labels.append("5m站回VWAP")
    if trig_bos: trig_labels.append("5mBOS")
    trigger_ok = bool(trig_sweep or trig_vwap or trig_bos)
    if trigger_ok:
        score += 0.16; evidence.append("/".join(trig_labels))

    # --- 4) 位置：折扣/溢价 或 VWAP 附近（不追高/不杀跌）---
    vw15 = float(vwap15[-1]) if np.isfinite(vwap15[-1]) else float(c15[-1])
    if d > 0:
        near_discount = bool(c15[-1] <= vw15 * 1.002)        # VWAP 下方/附近=好的多头位置
        extension = (c15[-1] - ref_lo) / max(a15, 1e-12)
    else:
        near_discount = bool(c15[-1] >= vw15 * 0.998)
        extension = (ref_hi - c15[-1]) / max(a15, 1e-12)
    if near_discount:
        score += 0.14; evidence.append("折扣/VWAP位")
    chase = bool(extension > 1.6)                            # 已垂直远离回踩位=追价
    if not chase:
        score += 0.05; evidence.append("非追价")
    else:
        # 追价直接否决主线，避免买在脉冲末端（v2亏损主因之一）
        return {"ok": False, "score": round(float(score), 3), "tier": "不做", "path": "追价",
                "evidence": evidence, "location_score": 0.0, "chase": True,
                "entry_size_multiplier": 0.0, "reason": "价格已垂直远离回踩位，追价风险大，等下一次回踩"}

    # --- 5) 量能确认（轻量，避免无量假收回）---
    med_v = float(np.median(v5[-21:-1])) if len(v5) >= 21 else float(np.median(v5))
    vol_ok = bool(med_v > 0 and v5[-1] >= 0.85 * med_v)
    if vol_ok:
        score += 0.05; evidence.append("量能确认")

    # --- 6) 副线：强趋势回踩VWAP后重新站回（不要求15m扫损，增加合格入场）---
    strong_trend = bool(ema_align and er15 >= 0.32)
    if d > 0:
        touched_vwap = bool(np.min(l5[-5:]) <= vw15 * 1.001 and c5[-1] > vw15)
    else:
        touched_vwap = bool(np.max(h5[-5:]) >= vw15 * 0.999 and c5[-1] < vw15)
    continuation = bool(strong_trend and touched_vwap and (trig_vwap or trig_bos))
    if continuation:
        score += 0.10; evidence.append("强趋势VWAP延续")

    # --- 7) 震荡软门：纯死寂震荡且无扫损事件则不做顺势单 ---
    dead_chop = bool(er15 < 0.16 and not sweep_ok)
    if dead_chop:
        return {"ok": False, "score": round(float(score), 3), "tier": "不做", "path": "死寂震荡",
                "evidence": evidence, "location_score": (0.14 if near_discount else 0.0),
                "chase": False, "entry_size_multiplier": 0.0,
                "reason": f"15m效率比过低(ER={er15:.2f})且无流动性扫损，拉锯盘不做顺势单"}

    # --- 8) 1H 大方向分（build_decision 已保证同向，这里计入置信度）---
    score += 0.22; evidence.append("1H方向")

    # 注：真实回测中“无扫损、只靠VWAP延续”的单子胜率显著更低，故只把它作为扫损单的加分项，
    # 不再作为独立进场路径——核心进场始终要求“顺向流动性扫损+收回”。
    core = bool(sweep_ok and trigger_ok)
    score = float(np.clip(score, 0.0, 1.0))
    threshold = 0.50
    if not core or score < threshold:
        return {"ok": False, "score": round(score, 3), "tier": "不做",
                "path": "扫损收回" if sweep_ok else "等待",
                "evidence": evidence, "location_score": (0.14 if near_discount else 0.0),
                "chase": False, "entry_size_multiplier": 0.0,
                "reason": f"裸K共振不足(评分{score:.2f}<{threshold})，核心条件未齐"}
    if score >= 0.78:
        tier, mult, rr = "很强", 1.0, 1.5
    elif score >= 0.62:
        tier, mult, rr = "强", 0.85, 1.35
    else:
        tier, mult, rr = "一般", 0.7, 1.25
    path = "顺向扫损MSS"
    return {"ok": True, "score": round(score, 3), "tier": tier, "path": path, "rr": rr,
            "evidence": evidence, "location_score": (0.14 if near_discount else 0.06),
            "chase": False, "entry_size_multiplier": mult,
            "er15": round(er15, 3), "atr15": a15,
            "reason": f"{path}；" + "、".join(evidence)}


# ============================ v5 自适应（区间回归质量闸门 + 可选趋势回踩） ============================
def _v5_quality_gates(frames: Dict[str, Any], px: float, side: str) -> Tuple[bool, List[str]]:
    """v5 在 v4 区间极值回归成立后，追加三道区间质量闸门 + 实体半区确认。返回 (通过, 拦截原因)。"""
    blockers: List[str] = []
    f5, f1, f4 = frames["5m"], frames["1h"], frames["4h"]
    c1, c4 = f1["close"], f4["close"]
    # 1) 1H 趋势延伸闸门：收盘离 1H EMA21 太远（强趋势延伸）不逆势
    try:
        a1 = _atr_arr(f1, 14); e21_1 = _ema_arr(c1, 21)
        a1v = float(a1[-1]) if np.isfinite(a1[-1]) and a1[-1] > 0 else float(np.median(
            np.maximum(f1["high"][-20:] - f1["low"][-20:], 1e-12)))
        ext = abs(px - float(e21_1[-1])) / max(a1v, 1e-12)
        if ext > FAST_V5_MR_MAX_EXT1H:
            blockers.append(f"1H延伸{ext:.1f}ATR")
    except Exception:
        pass
    # 2) 4H 趋势闸门：4H EMA21/50 已明显张开（4H 已走趋势）不逆势
    try:
        a4 = _atr_arr(f4, 14)
        e21_4, e50_4 = _ema_arr(c4, 21), _ema_arr(c4, 50)
        a4v = float(a4[-1]) if np.isfinite(a4[-1]) and a4[-1] > 0 else 1.0
        gap4 = float((e21_4[-1] - e50_4[-1]) / max(a4v, 1e-12))
        if abs(gap4) > FAST_V5_MR_MAX_4HGAP:
            blockers.append(f"4H已走趋势(gap={gap4:.2f})")
    except Exception:
        pass
    # 3) 布林带爆炸扩张闸门：带宽相对近 50 根均值异常放大（可能是趋势启动而非区间极值）
    try:
        bb = _bollinger(f5, _V4["bb_n"], _V4["bb_k"]); a5 = _atr_arr(f5, 14)
        width = (bb["up"] - bb["dn"]) / np.maximum(a5, 1e-12)
        ref = float(np.nanmean(width[-51:-1])) if len(width) >= 51 else float(np.nanmean(width))
        if np.isfinite(ref) and ref > 0 and float(width[-1]) > FAST_V5_MR_BBW_X * ref:
            blockers.append("布林带爆炸扩张")
    except Exception:
        pass
    # 4) 实体半区强势收回确认
    if FAST_V5_MR_BODY:
        o = float(f5["open"][-1]); h = float(f5["high"][-1])
        l = float(f5["low"][-1]); c = float(f5["close"][-1])
        rng = max(h - l, 1e-12)
        if side == "LONG" and (c - l) / rng < 0.5:
            blockers.append("下轨收回实体偏弱")
        if side == "SHORT" and (h - c) / rng < 0.5:
            blockers.append("上轨收回实体偏弱")
    return (len(blockers) == 0, blockers)


def _v5_meanrev(frames: Dict[str, Any], px: float) -> Dict[str, Any]:
    """v5 区间 sleeve：先过 v4 极值回归，再过 v5 质量闸门；止损/止盈/管理与 v4 完全一致（严格子集）。"""
    base = _v4_meanrev(frames, px)
    if not base.get("ok"):
        return base
    side = str(base.get("side", ""))
    ok, blockers = _v5_quality_gates(frames, px, side)
    if not ok:
        return {"ok": False, "score": 0.0, "tier": "不做", "path": "v5质量闸门拦截", "evidence": blockers,
                "entry_size_multiplier": 0.0,
                "reason": "v5区间质量闸门未过：" + "、".join(blockers)}
    ev = list(base.get("evidence", [])) + ["v5质量闸门"]
    base["evidence"] = ev
    base["path"] = "v5区间极值回归"
    base["reason"] = "v5区间极值回归（建议post-only maker挂单）：" + "、".join(ev)
    return base


def _v5_trend_pullback(frames: Dict[str, Any], px: float, direction: int) -> Dict[str, Any]:
    """v5 趋势 sleeve（默认关闭）：1H+4H 同向强趋势里，回踩 15m EMA21/会话VWAP 后出现重启 K 才做，不追垂直脉冲。

    31 小币 199 天 INS/OOS 事件驱动回测中该 sleeve PF≈0.86-0.97（扣费后不稳健），故默认关闭；
    开启需设 FAST_V5_TREND_SLEEVE=1 并自行模拟验证。
    """
    up = direction > 0
    f5, f15, f4 = frames["5m"], frames["15m"], frames["4h"]
    c5, h5, l5, o5 = f5["close"], f5["high"], f5["low"], f5["open"]
    c15, h15, l15 = f15["close"], f15["high"], f15["low"]; c4 = f4["close"]
    if len(c5) < 30 or len(c15) < 30 or len(c4) < 30 or px <= 0:
        return {"ok": False, "score": 0.0, "tier": "不做", "path": "-", "evidence": [],
                "entry_size_multiplier": 0.0, "reason": "已收盘K不足"}
    # 4H 必须同向（方向更准）
    a4 = _atr_arr(f4, 14); e21_4, e50_4 = _ema_arr(c4, 21), _ema_arr(c4, 50)
    a4v = float(a4[-1]) if np.isfinite(a4[-1]) and a4[-1] > 0 else 1.0
    gap4 = float((e21_4[-1] - e50_4[-1]) / max(a4v, 1e-12))
    if up and not gap4 > 0:
        return {"ok": False, "reason": f"4H未同向(gap={gap4:.2f})，趋势回踩不做", "tier": "不做",
                "path": "-", "evidence": [], "entry_size_multiplier": 0.0, "score": 0.0}
    if (not up) and not gap4 < 0:
        return {"ok": False, "reason": f"4H未同向(gap={gap4:.2f})，趋势回踩不做", "tier": "不做",
                "path": "-", "evidence": [], "entry_size_multiplier": 0.0, "score": 0.0}
    atr15 = _atr_arr(f15, 14)
    au = float(atr15[-1]) if np.isfinite(atr15[-1]) and atr15[-1] > 0 else float(np.median(
        np.maximum(h15[-20:] - l15[-20:], 1e-12)))
    e21g = _ema_arr(c15, 21); ema = float(e21g[-1])
    vw = float(_vwap_arr(f5, 96)[-1])
    if not np.isfinite(ema) or not np.isfinite(vw) or au <= 0:
        return {"ok": False, "reason": "价值区(EMA/VWAP)无效", "tier": "不做", "path": "-",
                "evidence": [], "entry_size_multiplier": 0.0, "score": 0.0}
    rng_bar = max(float(h5[-1] - l5[-1]), 1e-12)
    small_lo = float(np.min(l5[-11:-3])); small_hi = float(np.max(h5[-11:-3]))
    ev: List[str] = []
    go = False
    if up:
        touch = float(np.min(l5[-7:])) <= max(ema, vw) * 1.002
        resume = bool(c5[-1] > o5[-1] and c5[-1] > vw and (c5[-1] - l5[-1]) / rng_bar >= 0.5
                      and (float(l5[-1]) < small_lo or float(l5[-1]) <= ema * 1.001))
        extended = bool(c5[-1] > ema + FAST_V5_TR_EXTEND_ATR * au)
        go = touch and resume and not extended
        if touch and resume and extended:
            return {"ok": False, "reason": "回踩重启但已偏离价值区过远，不追多", "tier": "不做", "path": "-",
                    "evidence": [], "entry_size_multiplier": 0.0, "score": 0.0}
    else:
        touch = float(np.max(h5[-7:])) >= min(ema, vw) * 0.998
        resume = bool(c5[-1] < o5[-1] and c5[-1] < vw and (h5[-1] - c5[-1]) / rng_bar >= 0.5
                      and (float(h5[-1]) > small_hi or float(h5[-1]) >= ema * 0.999))
        extended = bool(c5[-1] < ema - FAST_V5_TR_EXTEND_ATR * au)
        go = touch and resume and not extended
        if touch and resume and extended:
            return {"ok": False, "reason": "回踩重启但已偏离价值区过远，不追空", "tier": "不做", "path": "-",
                    "evidence": [], "entry_size_multiplier": 0.0, "score": 0.0}
    if not go:
        return {"ok": False, "reason": "强趋势中尚未出现回踩价值区后的重启K", "tier": "不做", "path": "-",
                "evidence": [], "entry_size_multiplier": 0.0, "score": 0.0}
    # 结构止损（15m 摆动失效位）+ 固定 RR
    anchor = max(abs(px - (float(np.min(l15[-6:])) if up else float(np.max(h15[-6:])))), 0.0)
    stop = float(np.clip(anchor + 0.4 * au, FAST_V5_TR_STOP_MIN_ATR * au, FAST_V5_TR_STOP_MAX_ATR * au))
    sl_pct = stop / px
    if sl_pct < FAST_V5_TR_FLOOR_PCT:
        return {"ok": False, "reason": f"趋势单结构止损仅{sl_pct:.2%}<{FAST_V5_TR_FLOOR_PCT:.2%}，放弃",
                "tier": "不做", "path": "-", "evidence": [], "entry_size_multiplier": 0.0, "score": 0.0}
    tp_dist = FAST_V5_TR_RR * stop; tp_pct = tp_dist / px
    if sl_pct < 0.0015 or sl_pct > 0.06 or tp_pct <= sl_pct:
        return {"ok": False, "reason": "趋势单 TP/SL 距离异常", "tier": "不做", "path": "-",
                "evidence": [], "entry_size_multiplier": 0.0, "score": 0.0}
    tp_px = px + tp_dist if up else px - tp_dist
    sl_px = px - stop if up else px + stop
    side = "LONG" if up else "SHORT"
    ev = ["1H+4H同向趋势", "回踩价值区重启", "非追价确认", f"固定{FAST_V5_TR_RR:.1f}R"]
    score = 0.62
    return {"ok": True, "side": side, "score": score, "tier": "强", "path": "v5趋势回踩延续",
            "evidence": ev, "entry_size_multiplier": 0.7, "maker_preferred": False,
            "rr": tp_pct / sl_pct, "sl_pct": sl_pct, "tp_pct": tp_pct,
            "tp_price": tp_px, "sl_price": sl_px,
            "reason": "v5趋势回踩延续：" + "、".join(ev)}


def _build_decision_v5(symbol: str, data: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    """v5 双态自适应：区间做高胜率质量闸门回归（maker），强趋势默认空仓、可选回踩延续 sleeve。

    输出 schema 与 v3/v4 完全一致；v4_engine 仍取 RANGE_REVERSION / TREND_PULLBACK，
    使 alpha_engine 既有持仓管理（MR 不做横盘/反转砍仓、趋势单走跟踪）无需改动即可正确接管。
    """
    frames = data["frames"]; px = data["ticker_last"]
    reg = _v4_regime(frames); regime = int(reg["regime"])
    spread_bps = float(data.get("spread_bps", -1.0) or -1.0)
    if spread_bps >= 0 and spread_bps > 20.0:
        return _decision_flat(symbol, f"买卖点差过宽({spread_bps:.1f}bps>20bps)，本轮不做", ctx, px, data,
                              extras={"tier": "不做"})
    engine = ""; entry: Dict[str, Any] = {}; tp_sl: Dict[str, Any] = {}; side = ""; direction = 0
    if regime == 0:
        entry = _v5_meanrev(frames, px)
        if not entry.get("ok"):
            return _decision_flat(symbol, f"[v5区间regime] {entry.get('reason','无合格极值回归')}", ctx, px, data,
                                  extras={"tier": "不做", "entry": entry})
        side = str(entry["side"]); direction = 1 if side == "LONG" else -1
        tp_sl = {"ok": True, "tp": float(entry["tp_pct"]), "sl": float(entry["sl_pct"]),
                 "tp_price": float(entry["tp_price"]), "sl_price": float(entry["sl_price"]),
                 "rr": float(entry["rr"]),
                 "reason": "v5区间回归：15m-ATR失效位止损 + 就近VWAP/中轨目标（建议maker进场）"}
        engine = "RANGE_REVERSION"
    elif regime in (1, -1):
        if not FAST_V5_TREND_SLEEVE:
            return _decision_flat(symbol, f"[v5趋势regime] {reg['label']}：趋势回踩sleeve回测扣费后不稳健，默认空仓等待区间极值"
                                          "（FAST_V5_TREND_SLEEVE=False）", ctx, px, data, extras={"tier": "不做"})
        direction = regime
        entry = _v5_trend_pullback(frames, px, direction)
        if not entry.get("ok"):
            return _decision_flat(symbol, f"[v5趋势regime] {entry.get('reason','无合格回踩重启')}", ctx, px, data,
                                  extras={"tier": "不做", "entry": entry})
        side = "LONG" if direction > 0 else "SHORT"
        tp_sl = {"ok": True, "tp": float(entry["tp_pct"]), "sl": float(entry["sl_pct"]),
                 "tp_price": float(entry["tp_price"]), "sl_price": float(entry["sl_price"]),
                 "rr": float(entry["rr"]),
                 "reason": "v5趋势回踩：结构失效位止损 + 固定2R目标（分批+保本+runner）"}
        engine = "TREND_PULLBACK"
    else:
        return _decision_flat(symbol, f"v5 regime中性过渡（{reg['label']}，ER15={reg.get('er15')}/ER1H={reg.get('er1h')}），趋势与区间均不做",
                              ctx, px, data, extras={"tier": "不做"})
    if not tp_sl.get("ok"):
        return _decision_flat(symbol, "结构成立但 TP/SL 距离异常，等待更好入场", ctx, px, data,
                              extras={"tier": entry.get("tier", "不做"), "entry": entry, "tp_sl": tp_sl})

    sweep15 = _liquidity_sweep(frames["15m"]); pools15 = _liquidity_pools(frames["15m"])
    disp15 = _displacement(frames["15m"]); fvg15 = _fvg(frames["15m"]); ob15 = _order_block(frames["15m"])
    pd15 = _premium_discount(frames["15m"]); loc15 = _price_location(frames["15m"])
    trigger = _trigger_quality(frames["5m"], frames["15m"], direction)
    score = float(entry.get("score", 0.6) or 0.6)
    is_mr = engine == "RANGE_REVERSION"
    maker_preferred = bool(entry.get("maker_preferred", is_mr))
    engine_cn = "区间回归" if is_mr else "趋势回踩"
    reason = (f"[v5 {reg['label']}/{engine_cn}] 路径={entry.get('path')}；"
              f"共振={'、'.join(entry.get('evidence', [])) or '无'}；评分={score:.2f}；"
              f"TP/SL=1:{tp_sl['rr']:.2f}；{'建议maker挂单' if maker_preferred else '市价/回踩进场'}；{tp_sl.get('reason','')}")
    return {
        "signal": side,
        "reason": reason,
        "confidence": score, "raw_confidence": score, "entry_threshold": 0.50,
        "signal_tier": entry.get("tier", "一般"), "directional_margin": score,
        "base_tp": tp_sl["tp"], "base_sl": tp_sl["sl"], "tp": tp_sl["tp"], "sl": tp_sl["sl"],
        "horizon": 16, "strategy_mode": "FAST", "strategy_label": "日内裸K快速实盘(v5自适应)",
        "model_ready": True, "version": "ALPHA-X-FAST-PRICE-ACTION-5.1-REGIME-ADAPTIVE",
        "timeframe": "5m触发/15m结构/1H-4H regime",
        "market_context": {"regime": f"FAST-v5/{reg['label']}", "stress": 0.0,
                           "spread_bps": float(spread_bps), "orderbook_imbalance": 0.0,
                           "funding_rate": 0.0, "open_interest": 0.0,
                           "oi_change_pct": 0.0, "no_trade": bool(data["missing"]), "reasons": data["missing"]},
        "adaptive_context": {"enabled": True, "regime": {"label": f"v5/{reg['label']}", **reg},
                             "multi_timeframe": ctx, "lead_lag": {}, "size_multiplier": 1.0},
        "fast_entry_size_multiplier": float(entry.get("entry_size_multiplier", 1.0) or 1.0),
        "strategy_committee": {"signal": side, "agreement": ctx.get("agreement", 0.0), "committee_score": score},
        "fast_data": data["summary"],
        "entry_price_confirmation": {
            "enabled": True, "decision": "ENTER", "score": score, "entry_price": px,
            "entry_size_multiplier": float(entry.get("entry_size_multiplier", 1.0) or 1.0),
            "entry_quality": entry.get("tier", "一般"),
            "reason": "FAST v5 进场确认：" + entry.get("reason", ""),
            "factors": {"进场评分": score, "regime": reg["label"], "引擎": engine,
                        "5m触发": float(trigger.get("score", 0.0)),
                        "非追价": 1.0 if is_mr else 1.0}},
        "fast_strategy": {
            "version": "5.1-REGIME-ADAPTIVE", "v5_regime": reg, "v4_regime": reg, "v4_engine": engine,
            "v5_engine": engine, "v5_trend_sleeve": bool(FAST_V5_TREND_SLEEVE),
            "maker_preferred": maker_preferred,
            "entry_size_multiplier": float(entry.get("entry_size_multiplier", 1.0) or 1.0),
            "tier": entry.get("tier", "一般"), "path": entry.get("path", engine),
            "evidence": entry.get("evidence", []), "context": ctx, "trigger": trigger,
            "liquidity_sweep": sweep15, "liquidity_pools": pools15, "displacement": disp15,
            "fvg": fvg15, "order_block": ob15, "premium_discount": pd15, "price_location": loc15,
            "entry_quality": entry, "structure_score": score, "context_score": score,
            "trigger_score": float(trigger.get("score", 0.0)), "er15": float(reg.get("er15", 0.0)),
            "adaptive_tp_pct": tp_sl["tp"], "adaptive_sl_pct": tp_sl["sl"],
            "tp_price": tp_sl["tp_price"], "sl_price": tp_sl["sl_price"],
            "rr": tp_sl["rr"], "tp_sl_reason": tp_sl.get("reason", "")},
        "fast_ownership": {"entry_owner": "FAST裸K-v5", "initial_tp_sl_owner": "FAST裸K-v5",
                           "post_entry_owner": "原有执行/持仓管理(分批止盈/趋势跟踪)",
                           "post_entry_tp_sl_recalculation": False}}


def _build_decision(symbol: str, data: Dict[str, Any]) -> Dict[str, Any]:
    if FAST_ACTIVE_VERSION in ("v6","v62"):
        if FAST_ACTIVE_VERSION=="v62":
            from alpha_fast_v62 import decide
        else:
            from alpha_fast_v6 import decide
        return decide(symbol, data)
    frames = data["frames"]; px = data["ticker_last"]
    ctx = _directional_context(frames["4h"], frames["1h"], frames["15m"])
    if FAST_ACTIVE_VERSION == "v5":
        # v5：v4 区间回归 + 三道质量闸门（默认），趋势回踩 sleeve 默认关闭；schema 与 v3/v4 一致。
        return _build_decision_v5(symbol, data, ctx)
    if FAST_V4_ENSEMBLE:
        # v4：定量 regime 闸门 + 区间回归/趋势扫损双引擎；v3 逻辑保留为 flag=False 回退。
        return _build_decision_v4(symbol, data, ctx)
    direction = int(ctx["direction"])
    if direction == 0:
        return _decision_flat(symbol, f"大方向未确认：{ctx.get('direction_source','4H/1H')}", ctx, px, data)

    # 4H 大级别强逆向硬过滤（保留）。
    s4 = ctx.get("4h") or {}
    s4_bias = int(s4.get("bias", 0) or 0); s4_strength = float(s4.get("strength", 0.0) or 0.0)
    if s4_bias and s4_bias != direction and s4_strength >= 0.35:
        return _decision_flat(
            symbol,
            f"4H大级别强逆向（4H={'多头' if s4_bias > 0 else '空头'}，结构力度{s4_strength:.2f}），不做逆大级别单",
            ctx, px, data
        )

    # 点差/流动性过滤（保留）。
    spread_bps = float(data.get("spread_bps", -1.0) or -1.0)
    if spread_bps >= 0 and spread_bps > 20.0:
        return _decision_flat(symbol, f"买卖点差过宽({spread_bps:.1f}bps>20bps)，流动性不足，本轮不做", ctx, px, data)

    s15 = ctx["15m"]
    sweep15 = _liquidity_sweep(frames["15m"]); pools15 = _liquidity_pools(frames["15m"])
    disp15 = _displacement(frames["15m"]); fvg15 = _fvg(frames["15m"]); ob15 = _order_block(frames["15m"])
    loc15 = _price_location(frames["15m"]); pd15 = _premium_discount(frames["15m"])
    trigger = _trigger_quality(frames["5m"], frames["15m"], direction)

    entry = _v3_setup(frames, direction, px)
    if not entry.get("ok"):
        return _decision_flat(
            symbol,
            f"{entry.get('reason','裸K进场质量不足')}（证据={ '/'.join(entry.get('evidence',[])) or '无'}，评分={entry.get('score',0):.2f}）",
            ctx, px, data,
            extras={"structure_score": entry.get("score", 0.0), "trigger": trigger,
                    "tier": "不做", "entry": entry}
        )

    side = "LONG" if direction > 0 else "SHORT"
    target_rr = float(entry.get("rr", 1.4))
    tp_sl = _fast_tp_sl(frames["15m"], frames["5m"], side, px, min_rr=target_rr)
    if not tp_sl.get("ok"):
        return _decision_flat(symbol, "裸K结构成立，但TP/SL距离异常，等待更好的入场", ctx, px, data,
                              extras={"structure_score": entry["score"], "trigger": trigger,
                                      "tier": entry["tier"], "entry": entry, "tp_sl": tp_sl})

    factors = {
        "4H方向": ctx["4h"]["bias"] * direction,
        "1H方向": ctx["1h"]["bias"] * direction,
        "15m结构": s15["bias"] * direction,
        "15m扫损": float(sweep15["score"]) * direction,
        "流动性池": float(pools15["score"]) * direction,
        "位移": float(disp15["score"]) * direction,
        "FVG": float(fvg15["score"]) * direction,
        "OrderBlock": float(ob15["score"]) * direction,
        "折扣溢价": float(pd15["score"]),
        "5m触发": float(trigger["score"]),
        "进场评分": float(entry["score"]),
        "非追价": 0.0 if entry.get("chase") else 1.0,
    }
    reason = "；".join([
        f"4H={ctx['4h']['label']}", f"1H={ctx['1h']['label']}", f"15m={s15['label']}",
        f"路径={entry['path']}", f"共振={'、'.join(entry['evidence'])}",
        f"入场评分={entry['score']:.2f}", f"TP/SL=1:{tp_sl['rr']:.2f}", tp_sl["reason"],
    ])

    return {
        "signal": side,
        "reason": reason,
        "confidence": float(entry["score"]),
        "raw_confidence": float(entry["score"]),
        "entry_threshold": 0.50,
        "signal_tier": entry["tier"],
        "directional_margin": float(entry["score"]),
        "base_tp": tp_sl["tp"], "base_sl": tp_sl["sl"],
        "tp": tp_sl["tp"], "sl": tp_sl["sl"],
        "horizon": 16,
        "strategy_mode": "FAST",
        "strategy_label": "日内裸K快速实盘",
        "model_ready": True,
        "version": "ALPHA-X-FAST-PRICE-ACTION-4.0-NAKED-SWEEP-VWAP",
        "timeframe": "5m触发/15m结构",
        "market_context": {
            "regime": "FAST裸K", "stress": 0.0, "spread_bps": float(spread_bps),
            "orderbook_imbalance": 0.0, "funding_rate": 0.0,
            "open_interest": 0.0, "oi_change_pct": 0.0,
            "no_trade": bool(data["missing"]), "reasons": data["missing"]
        },
        "adaptive_context": {
            "enabled": True, "regime": {"label": "日内裸K"},
            "multi_timeframe": ctx, "lead_lag": {}, "size_multiplier": 1.0
        },
        "fast_entry_size_multiplier": float(entry.get("entry_size_multiplier", 1.0) or 1.0),
        "strategy_committee": {
            "signal": side, "agreement": ctx["agreement"], "committee_score": float(entry["score"])
        },
        "fast_data": data["summary"],
        "entry_price_confirmation": {
            "enabled": True,
            "decision": "ENTER",
            "score": float(entry["score"]),
            "entry_price": px,
            "entry_size_multiplier": float(entry.get("entry_size_multiplier", 1.0) or 1.0),
            "entry_quality": entry["tier"],
            "reason": "FAST v3 裸K进场确认：" + entry["reason"],
            "factors": {
                "进场评分": float(entry["score"]),
                "5m触发": float(trigger["score"]),
                "价格位置": float(entry.get("location_score", 0.0)),
                "非追价": 0.0 if entry.get("chase") else 1.0,
            }
        },
        "fast_strategy": {
            "version": "4.0-NAKED-SWEEP-VWAP",
            "entry_size_multiplier": float(entry.get("entry_size_multiplier", 1.0) or 1.0),
            "tier": entry["tier"], "path": entry["path"], "evidence": entry.get("evidence", []),
            "context": ctx, "trigger": trigger, "liquidity_sweep": sweep15,
            "liquidity_pools": pools15, "displacement": disp15, "fvg": fvg15,
            "order_block": ob15, "premium_discount": pd15, "price_location": loc15,
            "entry_quality": entry, "factors": factors,
            "structure_score": float(entry["score"]), "context_score": float(entry["score"]),
            "trigger_score": float(trigger["score"]),
            "er15": float(entry.get("er15", 0.0)),
            "adaptive_tp_pct": tp_sl["tp"], "adaptive_sl_pct": tp_sl["sl"],
            "tp_price": tp_sl["tp_price"], "sl_price": tp_sl["sl_price"],
            "rr": tp_sl["rr"], "tp_sl_reason": tp_sl["reason"]
        },
        "fast_ownership": {
            "entry_owner": "FAST裸K",
            "initial_tp_sl_owner": "FAST裸K",
            "post_entry_owner": "原有执行/持仓管理",
            "post_entry_tp_sl_recalculation": False
        }
    }


def _decision_flat(symbol: str, reason: str, ctx: Dict[str, Any], px: float, data: Dict[str, Any], extras: Dict[str, Any] | None = None) -> Dict[str, Any]:
    extras=extras or {}
    return {"signal":"FLAT","reason":reason,"confidence":0.0,"raw_confidence":0.0,"entry_threshold":0.50,"signal_tier":"FAST","directional_margin":0.0,"base_tp":0.0,"base_sl":0.0,"tp":0.0,"sl":0.0,"horizon":16,"strategy_mode":"FAST","strategy_label":"日内裸K快速实盘","model_ready":True,"timeframe":"5m触发/15m结构","market_context":{"regime":"FAST裸K","stress":0.0,"spread_bps":0.0,"orderbook_imbalance":0.0,"funding_rate":0.0,"open_interest":0.0,"oi_change_pct":0.0,"no_trade":bool(data["missing"]),"reasons":data["missing"]},"adaptive_context":{"enabled":True,"regime":{"label":"日内裸K"},"multi_timeframe":ctx,"lead_lag":{},"size_multiplier":1.0},"fast_entry_size_multiplier":1.0,"strategy_committee":{"signal":"FLAT","agreement":ctx.get("agreement",0.0),"committee_score":0.0},"fast_data":data["summary"],"entry_price_confirmation":{"enabled":True,"decision":"WAIT","score":0.0,"entry_price":px,"entry_size_multiplier":0.0,"entry_quality":"不做","reason":reason},"fast_strategy":{"tier":extras.get("tier","不做"),"direction_source":ctx.get("direction_source",""),"direction":ctx.get("direction",0),"4h_label":ctx.get("4h",{}).get("label",""),"1h_label":ctx.get("1h",{}).get("label",""),"15m_label":ctx.get("15m",{}).get("label",""),"structure_score":extras.get("structure_score",0.0),"trigger_score":float((extras.get("trigger") or {}).get("score",0.0) or 0.0),"trigger_evidence":list((extras.get("trigger") or {}).get("evidence") or []),"adaptive_tp_pct":float((extras.get("tp_sl") or {}).get("tp",0.0) or 0.0),"adaptive_sl_pct":float((extras.get("tp_sl") or {}).get("sl",0.0) or 0.0),"tp_sl_reason":str((extras.get("tp_sl") or {}).get("reason", ""))},"fast_ownership":{"entry_owner":"FAST裸K","initial_tp_sl_owner":"FAST裸K","post_entry_owner":"原有执行/持仓管理","post_entry_tp_sl_recalculation":False}}


def _fetch_symbol(symbol: str, only_5m: bool = False) -> Dict[str, Any]:
    if not okx_client.is_connected or not getattr(okx_client,"_exchange",None):
        raise RuntimeError("OKX尚未连接")
    exchange=okx_client._exchange; cs=config.trading.get_ccxt_symbol(symbol)
    tasks={
        "ticker": lambda: okx_client.get_ticker(symbol) or {},
        "ohlcv_5m": lambda: exchange.fetch_ohlcv(cs,"5m",limit=300 if only_5m else FRAME_LIMIT["5m"]+10),
        "ohlcv_15m": lambda: exchange.fetch_ohlcv(cs,"15m",limit=FRAME_LIMIT["15m"]+10),
        "ohlcv_1h": lambda: exchange.fetch_ohlcv(cs,"1h",limit=FRAME_LIMIT["1h"]+10),
        "ohlcv_4h": lambda: exchange.fetch_ohlcv(cs,"4h",limit=FRAME_LIMIT["4h"]+10),
    }
    if only_5m:
        tasks = {k: v for k, v in tasks.items() if k in ("ticker", "ohlcv_5m")}
    raw={}; missing=[]
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures={pool.submit(_cached_fetch,f"{symbol}|{k}",FRAME_TTL.get(k.replace("ohlcv_",""),1.0),fn):k for k,fn in tasks.items()}
        for fut in as_completed(futures):
            k=futures[fut]
            try: raw[k]=fut.result()
            except Exception as exc: raw[k]=None; missing.append(f"{k}:{exc}")
    timeframes = ("5m",) if only_5m else ("5m","15m","1h","4h")
    frames={tf:_bar_frame(raw.get(f"ohlcv_{tf}") or [],tf,288 if only_5m else None) for tf in timeframes}
    # 15m 新收盘时，把 5m/15m 一起强制刷新，避免一个扫描周期里混用新旧结构。
    ts15=int(frames.get("15m",{}).get("ts",np.array([]))[-1]) if len(frames.get("15m",{}).get("ts",np.array([]))) else 0
    prev_ts15=int(MARKET_SYNC_STATE.get(symbol,{}).get("ts15",0) or 0)
    if ts15 and prev_ts15 and ts15 != prev_ts15:
        try:
            raw["ohlcv_5m"]=tasks["ohlcv_5m"]()
            raw["ohlcv_15m"]=tasks["ohlcv_15m"]()
            frames["5m"]=_bar_frame(raw.get("ohlcv_5m") or [],"5m")
            frames["15m"]=_bar_frame(raw.get("ohlcv_15m") or [],"15m")
            ts15=int(frames.get("15m",{}).get("ts",np.array([]))[-1]) if len(frames.get("15m",{}).get("ts",np.array([]))) else ts15
        except Exception as exc:
            logger.warning(f"FAST {symbol} 15m新K同步刷新失败，继续使用本轮数据：{exc}")
    if ts15:
        MARKET_SYNC_STATE[symbol]={"ts15":ts15}
    for tf in timeframes:
        if not frames[tf]: missing.append(f"ohlcv_{tf}")
    ticker=raw.get("ticker") or {}; px=_finite(ticker.get("last"),0.0)
    if px<=0: missing.append("ticker")
    # 优化9：信号层提前计算买卖点差(bps)，用于在出信号前就过滤点差异常/流动性枯竭的币，避免信号照出却在下单门被挡、反复刷屏。
    bid=_finite(ticker.get("bid"),0.0); ask=_finite(ticker.get("ask"),0.0)
    spread_bps=float(abs(ask-bid)/max((ask+bid)*0.5,1e-12)*1e4) if bid>0 and ask>0 and ask>=bid else -1.0
    summary={"ready":not missing,"missing":missing,"completed_bars":{tf:int(len(frames[tf].get("close",[]))) for tf in frames},"entry_timeframe":"5m","structure_timeframe":"15m","context_timeframes":["1h","4h"],"spread_bps":spread_bps}
    return {"frames":frames,"ticker_last":px,"spread_bps":spread_bps,"missing":sorted(set(missing)),"summary":summary}



def position_reversal_check(symbol: str, side: str) -> Dict[str, Any]:
    """FAST持仓监控入口：独立抓取已完成K线，返回HOLD/WATCH/CONFIRMED。"""
    try:
        data=_fetch_symbol(symbol)
        frames=data.get("frames") or {}
        if "5m" not in frames or "15m" not in frames:
            return {"action":"HOLD","score":0.0,"reason":"持仓反转监控缺少5m/15m数据","reversal":False,"wash":False}
        return _fast_position_reversal_check(frames["5m"],frames["15m"],side)
    except Exception as exc:
        return {"action":"HOLD","score":0.0,"reason":f"持仓反转监控异常，保持原TP/SL不主动平仓：{exc}","reversal":False,"wash":False,"error":str(exc)}

def predict(symbol: str) -> Dict[str,Any]:
    version=FAST_ACTIVE_VERSION
    bt='5m'
    try:
        if version != "v7":
            data=_fetch_symbol(symbol)
        else:
            data=None
        if version == "v7":
            # V7.6.6 主级别全链路：所有 v7 策略都按 base_tf 经 alpha_v7_feed.bundle 取数。
            # frames 键为主级别 base_tf；仅当 chan_quant 且开启 chan_mtf 时再嵌套两个更高周期。
            from alpha_fast_v7 import decide, get_runtime_params
            v7p=get_runtime_params()
            bt=v7p.get('base_tf','5m')
            now_ms=time.time()*1000.0
            need_mtf=(v7p.get('strategy')=='chan_quant' and bool(v7p.get('chan_mtf')))
            from alpha_v7_feed import bundle
            cs=config.trading.get_ccxt_symbol(symbol)
            frames=bundle(okx_client._exchange,cs,base_tf=bt,multi=need_mtf,now_ms=now_ms)
            # bundle 只返回已收盘、固定锚点 K 线；历史引导后必须重新取报价，
            # 避免用引导前旧价成交（点差也按最新 bid/ask 重算，沿用 _fetch_symbol 算法）。
            fresh_ticker=okx_client.get_ticker(symbol) or {}
            px=_finite(fresh_ticker.get('last'),0.0)
            bid=_finite(fresh_ticker.get('bid'),0.0); ask=_finite(fresh_ticker.get('ask'),0.0)
            spread_bps=float(abs(ask-bid)/max((ask+bid)*0.5,1e-12)*1e4) if bid>0 and ask>0 and ask>=bid else -1.0
            missing=[]
            if px<=0: missing.append('ticker')
            data={'frames':frames,'ticker_last':px,'spread_bps':spread_bps,'missing':missing,
                  'as_of_ms':time.time()*1000.0,
                  'summary':{'ready':not missing,'missing':missing,
                             'completed_bars':{tf:int(len(fr.get('close',[]))) for tf,fr in frames.items()},
                             'entry_timeframe':bt,'spread_bps':spread_bps}}
            if v7p.get('orderflow_mode'):
                from alpha_v7_orderflow import streaming_snapshot as flow_snapshot
                try:data['orderflow']=flow_snapshot(okx_client._exchange,cs)
                except Exception as exc:data['orderflow']={'fresh':False,'missing':[str(exc)]}
            data['as_of_ms']=time.time()*1000.0
            result=decide(symbol,data)
        elif version in ("v6","v62"):
            # V6 严格使用已收盘数据；旧版的1秒容差不带入V6。
            from alpha_fast_v6_data import snapshot
            now_ms=time.time()*1000.0
            for tf, minutes in (("5m",5),("15m",15),("1h",60),("4h",240)):
                frame=data["frames"].get(tf) or {}
                if "ts" in frame:
                    mask=frame["ts"]+minutes*60000<=now_ms
                    data["frames"][tf]={k:v[mask] for k,v in frame.items()}
            data["as_of_ms"]=now_ms
            if version=="v62" and MARKET_BENCHMARK_READY and time.time()-float(MARKET_BENCHMARK.get("updated_at") or 0)<300:
                data["benchmark"]=dict(MARKET_BENCHMARK)
            # 只对已形成结构候选的币抓取附加数据，减少无机会时的网络等待。
            if version=="v62":
                from alpha_fast_v62 import decide
            else:
                from alpha_fast_v6 import decide
            candidate=decide(symbol,data)
            if candidate.get("signal") in ("LONG","SHORT"):
                data["v6_micro"]=snapshot(okx_client._exchange,config.trading.get_ccxt_symbol(symbol))
                # 附加查询结束后重新确认时效/价格，旧触发绝不盲目追价。
                data["as_of_ms"]=time.time()*1000.0
                fresh_ticker=okx_client.get_ticker(symbol) or {}
                data["ticker_last"]=_finite(fresh_ticker.get("last"),0.0)
            result=decide(symbol,data)
        elif data["missing"]:
            result=_decision_flat(symbol,"核心裸K数据不完整，等待下一轮真实数据",_directional_context(data["frames"]["4h"],data["frames"]["1h"],data["frames"]["15m"]),data["ticker_last"],data)
        else:
            result=_build_decision(symbol,data)
        tier=str((result.get("fast_strategy") or {}).get("tier") or result.get("entry_price_confirmation",{}).get("entry_quality") or "不做")
        tf_mid=f"｜{bt}触发｜" if version=="v7" else "｜5m触发｜15m结构｜1H/4H方向｜"
        logger.info(f"[ALPHA-X FAST裸K] {symbol}｜方向={result.get('signal')}｜强度={tier}｜置信度={float(result.get('confidence',0) or 0):.3f}{tf_mid}TP={float(result.get('tp',0) or 0)*100:.2f}%｜SL={float(result.get('sl',0) or 0)*100:.2f}%｜原因={result.get('reason','')}")
        STATE[symbol]={"time":time.time(),"signal":result.get("signal"),"tier":tier,"tp":result.get("tp"),"sl":result.get("sl")}
        return result
    except Exception as exc:
        logger.exception(f"[ALPHA-X FAST裸K] {symbol} 扫描失败: {exc}")
        empty={"frames":{},"ticker_last":0.0,"missing":[str(exc)],"summary":{"ready":False,"missing":[str(exc)]}}
        return _decision_flat(symbol,"FAST裸K扫描异常，等待下一轮重试",{"direction":0,"agreement":0.0},0.0,empty)


def check(symbol: str) -> Dict[str,Any]:
    """真实OKX核心裸K数据连通性检查；只读，不下单。"""
    data=_fetch_symbol(symbol)
    return {"symbol":symbol,"connected":not bool(data["missing"]),"missing":data["missing"],"summary":data["summary"],"entry_timeframe":"5m","structure_timeframe":"15m","context_timeframes":["1h","4h"],"uses_completed_bars_only":True,"primary_logic":"裸K价格行为"}


def status() -> Dict[str,Any]:
    return {"mode":"FAST","label":"日内裸K快速实盘","symbols":STATE,"cache_entries":len(DATA_CACHE),"primary_logic":"4H/1H方向 + 15m结构 + 5m触发","uses_model":False,"uses_indicators_as_primary":False,"tp_sl_owner":"FAST裸K","post_entry_owner":"原有执行/持仓管理"}
