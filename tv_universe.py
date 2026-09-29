"""V7 自动选币：USDT 永续 24h 涨幅榜前 N（默认 20）。

设计原则（与旧选币层一致）：
  1. 只用 1 个公共请求（fetch_tickers 一次拿全市场快照），带时间缓存，不打爆限频；
  2. 已持仓币强制保留在交易池，选币绝不能漏管真实仓位；
  3. 任何异常自吞、返回上一次结果，绝不让选币层影响主循环；
  4. 只决定“看哪些币”，绝不绕过任何现有开仓硬门与风控。
"""
from __future__ import annotations

import time
import threading
from typing import Any, Dict, List, Optional

from loguru import logger

from okx_client import okx_client

LOCK = threading.RLock()

DEFAULT_CFG: Dict[str, Any] = {
    "top": 20,                    # 涨幅榜取前 20
    "basis": "24h",               # 涨跌幅口径：24h(过去24小时滚动) / utc8(今日北京0点) / utc0(今日UTC0点)
    "min_turnover_usdt": 10e6,    # 24h 成交额下限 1000 万 U，保证流动性
    "interval": 600,              # 榜单刷新间隔（秒），10 分钟
    "blacklist": [],              # 永不参与
}

BASIS_FIELD = {"24h": "pct", "utc8": "pct_utc8", "utc0": "pct_utc0"}
BASIS_LABEL = {"24h": "24h", "utc8": "今日(北京)", "utc0": "今日(UTC)"}

S: Dict[str, Any] = {
    "cfg": dict(DEFAULT_CFG),
    "pool": [],          # 明细 [{symbol,last,pct,quote_volume}]
    "symbols": [],       # 内部符号列表
    "last": 0.0,
    "last_msg": "V7 涨幅榜尚未刷新",
}


def configure(opts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """写入配置并可强制下次刷新。"""
    with LOCK:
        cfg = {**DEFAULT_CFG, **S["cfg"]}
        if opts:
            for k, v in opts.items():
                if k in cfg and v is not None:
                    cfg[k] = v
        cfg["blacklist"] = [str(s).strip().upper() for s in (cfg.get("blacklist") or [])]
        if cfg.get("basis") not in BASIS_FIELD:
            raise ValueError("涨跌幅口径无效（可选 24h / utc8 / utc0）")
        if not 1 <= int(cfg["top"]) <= 100:
            raise ValueError("选币数量须在 1~100 之间")
        S["cfg"] = cfg
        S["last"] = 0.0
        return dict(cfg)


def _refresh() -> None:
    cfg = S["cfg"]
    black = set(cfg.get("blacklist") or [])
    tickers = okx_client.fetch_swap_tickers()
    if not tickers:
        raise RuntimeError("全市场快照为空")
    pct_key = BASIS_FIELD.get(cfg.get("basis", "24h"), "pct")
    pool: List[Dict[str, Any]] = []
    for t in tickers:
        sym = str(t.get("symbol") or "")
        if sym in black:
            continue
        raw_pct = t.get(pct_key)
        if raw_pct is None:
            continue
        pct = float(raw_pct or 0)
        qv = float(t.get("quote_volume") or 0)
        last = float(t.get("last") or 0)
        # 涨幅榜：只要上涨的；并要求最低成交额，剔除妖币/死水
        if pct <= 0:
            continue
        if qv < float(cfg["min_turnover_usdt"]):
            continue
        pool.append({"symbol": sym, "last": last, "pct": pct, "quote_volume": qv})
    pool.sort(key=lambda x: x["pct"], reverse=True)
    pool = pool[: int(cfg.get("top", 20) or 20)]
    S["pool"] = pool
    S["symbols"] = [x["symbol"] for x in pool]
    S["last"] = time.time()
    S["last_msg"] = (f"{BASIS_LABEL.get(cfg.get('basis'), '24h')}涨幅榜："
                     f"扫描 {len(tickers)} 个 USDT 永续，前 {len(pool)} 名")
    logger.info(f"[V7 选币] {S['last_msg']}")


def top_gainers(held_symbols: Optional[List[str]] = None, force: bool = False) -> List[str]:
    """返回本轮应扫描的内部符号：已持仓币（强制在前）＋ 涨幅榜前 N。"""
    cfg = S["cfg"]
    ordered: List[str] = []
    seen = set()

    def add(sym: str) -> None:
        s = str(sym or "").strip().upper()
        if s and s not in seen:
            seen.add(s)
            ordered.append(s)

    for h in (held_symbols or []):
        add(h)
    try:
        stale = (time.time() - S["last"]) >= float(cfg.get("interval", 600) or 600)
        if force or stale:
            _refresh()
    except Exception as exc:
        logger.warning(f"[V7 选币] 刷新失败，沿用上一次榜单：{exc}")
    for s in S["symbols"]:
        add(s)
    return ordered


def snapshot() -> Dict[str, Any]:
    """给前端：榜单明细 + 刷新时间。"""
    with LOCK:
        return {
            "label": f"{BASIS_LABEL.get(S['cfg'].get('basis'), '24h')} 涨幅榜前 {S['cfg']['top']}",
            "basis": S["cfg"].get("basis", "24h"),
            "pool": [dict(x) for x in S["pool"]],
            "symbols": list(S["symbols"]),
            "last": S["last"],
            "last_msg": S["last_msg"],
            "cfg": {k: v for k, v in S["cfg"].items() if k != "blacklist"},
        }
