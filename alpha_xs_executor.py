"""
ALPHA-X FAST · v5-XS 横截面中性 —— 实盘下单执行器（独立，不影响 v3/v4/v5 逐币引擎）
====================================================================================
把 alpha_xs_neutral 产出的目标权重篮子，变成对 OKX 永续的【精确换手】真实订单：
  - 复用 okx_client.place_order（已封装合约张数换算/精度/最小量/干跑），不自己造下单轮子；
  - 支持开多/开空、加仓、减仓、多翻空/空翻多、清仓，单向净持仓模式，反手先 reduce_only 平旧仓；
  - 硬风控：总名义 gross ≤ 上限、单币权重 ≤ 上限、权益必须为正，任一不满足则【整批拒绝、一单不下】；
  - 默认 live=False 只返回订单计划（干跑/预览）；只有显式 live=True 且已连接 OKX 才真正下单；
  - 任何单腿失败都被捕获、不影响其它腿，返回成功/失败清单，绝不抛异常打断主循环。

强烈建议：先在 OKX 模拟盘(demo) 用 live=True 跑通 1-3 个月，再切实盘。
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
import math
import time
import numpy as np
import pandas as pd

# XS 进场方式：market=市价taker（默认，行为与旧版一致）；maker_market=先挂 post-only 限价(maker)，
# 超时未成交的余量自动转市价，保证该换的仓一定换完（回测里省下的手续费正是 XS 利润的关键）。
XS_ENTRY_MARKET = "market"
XS_ENTRY_MAKER_MARKET = "maker_market"
DEFAULT_MAKER_TTL = 8.0        # 每条调仓腿的 maker 挂单统一等待秒数（日内一次，可配）
DEFAULT_MAKER_IMPROVE_BPS = 0.5  # 向盘口中间让多少 bps 提高成交率（仍保证不越过对手价、不吃单）

MAX_GROSS = 2.0          # 多空总名义/权益上限（多1倍+空1倍）
MAX_PER_COIN = 0.25      # 单币名义/权益上限
MIN_NOTIONAL_USDT = 6.0  # 小于该名义的变动忽略（避免灰尘单/手续费浪费）
DEFAULT_LEVERAGE = 3     # 对涉及币种设置的保守杠杆（实际占用保证金远低于名义）


def ccxt_symbol(base: str) -> str:
    return f"{str(base).upper().replace('/USDT:USDT', '')}/USDT:USDT"


def positions_to_signed(positions: List[Dict[str, Any]]) -> Dict[str, float]:
    """get_positions() -> {BASE: 带符号币数量}（多+ / 空-）。"""
    cur: Dict[str, float] = {}
    for p in positions or []:
        if str(p.get("type", "swap")) != "swap":
            continue
        sym = str(p.get("symbol", ""))
        base = sym.split("/")[0]
        amt = float(p.get("contracts", 0) or 0)
        if p.get("side") == "short":
            amt = -amt
        cur[base] = cur.get(base, 0.0) + amt
    return cur


def plan_orders(target_w: pd.Series, prices: Dict[str, float], positions: List[Dict[str, Any]],
                equity: float, max_gross: float = MAX_GROSS, max_per_coin: float = MAX_PER_COIN,
                min_notional: float = MIN_NOTIONAL_USDT) -> Dict[str, Any]:
    """纯函数：目标权重 + 现持仓 -> 精确换手订单计划（不联网、不下单），便于离线测试。"""
    if not isinstance(equity, (int, float)) or not math.isfinite(equity) or equity <= 0:
        return {"ok": False, "reason": "权益必须为正", "steps": []}
    w = target_w.dropna()
    gross = float(w.abs().sum())
    big = float(w.abs().max()) if len(w) else 0.0
    if gross > max_gross + 1e-9:
        return {"ok": False, "reason": f"总名义{gross:.2f}倍 > 上限{max_gross}倍，拒绝下单", "steps": []}
    if big > max_per_coin + 1e-9:
        return {"ok": False, "reason": f"单币权重{big:.2f} > 上限{max_per_coin}，拒绝下单", "steps": []}

    cur = positions_to_signed(positions)
    bases = sorted(set(list(w.index) + [b for b, a in cur.items() if abs(a) > 0]))
    steps: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []   # 看门狗兜底：有持仓但无行情、无法平仓的币（只告警，不连累整批）
    for b in bases:
        px = float(prices.get(b, np.nan))
        if not math.isfinite(px) or px <= 0:
            # 无有效价格：
            #   - 无持仓：直接跳过（无法开仓，也无风险敞口）；
            #   - 有持仓：无法平仓/调整（被下架/无行情等极端情况），保留持仓并记入 blocked 告警，
            #             其余币照常调仓——不再因一个坏币整批拒单（看门狗快出的最后兜底）。
            if abs(cur.get(b, 0.0)) > 0:
                blocked.append({"base": b, "side": "long" if cur.get(b, 0.0) > 0 else "short",
                                "amount_coin": round(abs(cur.get(b, 0.0)), 8),
                                "reason": "无有效价格且有持仓，无法平仓/调整，暂时保留持仓并告警，等行情恢复再处理"})
            continue
        tgt_w = float(w.get(b, 0.0))
        tgt_coin = tgt_w * equity / px
        cur_coin = cur.get(b, 0.0)
        delta = tgt_coin - cur_coin
        if abs(delta) * px < min_notional:
            continue
        sym = ccxt_symbol(b)

        def add(side, amt, reduce_only, why):
            steps.append({"symbol": sym, "base": b, "side": side, "amount_coin": round(abs(amt), 8),
                          "reduce_only": bool(reduce_only), "why": why,
                          "notional": round(abs(amt) * px, 2)})

        # 反手：先平掉旧仓（reduce_only），再开新仓
        if cur_coin > 0 and tgt_coin < 0:
            add("sell", cur_coin, True, "多翻空-平多")
            add("sell", abs(tgt_coin), False, "多翻空-开空")
        elif cur_coin < 0 and tgt_coin > 0:
            add("buy", abs(cur_coin), True, "空翻多-平空")
            add("buy", tgt_coin, False, "空翻多-开多")
        # 同向/建仓/清仓
        elif delta > 0:   # 需要净买入
            if cur_coin < 0:    # 空头减仓（含接近清仓）
                add("buy", min(abs(delta), abs(cur_coin)), True, "减空/平空")
                if abs(delta) > abs(cur_coin):
                    add("buy", abs(delta) - abs(cur_coin), False, "反手后开多")
            else:
                add("buy", delta, False, "开多/加多")
        elif delta < 0:   # 需要净卖出
            if cur_coin > 0:
                add("sell", min(abs(delta), cur_coin), True, "减多/平多")
                if abs(delta) > cur_coin:
                    add("sell", abs(delta) - cur_coin, False, "反手后开空")
            else:
                add("sell", abs(delta), False, "开空/加空")
    return {"ok": True, "gross": round(gross, 3), "steps": steps, "blocked": blocked,
            "n_orders": len(steps), "target_long": [b for b in w.index if w[b] > 0],
            "target_short": [b for b in w.index if w[b] < 0]}


def _filled_coin(client, order: Dict[str, Any], sym: str) -> float:
    """订单已成交的【币数量】。合约返回的 filled 通常是张数，优先用客户端封装换算成币数。"""
    fn = getattr(client, "get_filled_amount", None)
    if callable(fn):
        try:
            return float(fn(order, sym) or 0.0)
        except Exception:
            pass
    try:
        return float((order or {}).get("filled", 0) or 0)
    except Exception:
        return 0.0


def maker_limit_price(ticker: Dict[str, Any], side: str, improve_bps: float = DEFAULT_MAKER_IMPROVE_BPS):
    """由盘口算 post-only 限价。买入挂在买一(并向中间让 improve_bps)、卖出挂在卖一(并向中间让)；
    绝不越过对手价，否则 post-only 会被交易所拒单——保证成交即 maker，不会偷偷变 taker。"""
    bid = float((ticker or {}).get("bid") or 0)
    ask = float((ticker or {}).get("ask") or 0)
    last = float((ticker or {}).get("last") or 0)
    k = 1.0 + max(0.0, float(improve_bps)) / 1e4
    if side == "buy":
        ref = bid if bid > 0 else (last if last > 0 else ask)
        if ref <= 0:
            return None
        p = ref * k
        if ask > 0 and p >= ask:    # 让价会越过卖一=可能吃单，则退回买一
            p = bid if bid > 0 else ref
        return p
    else:
        ref = ask if ask > 0 else (last if last > 0 else bid)
        if ref <= 0:
            return None
        p = ref * (2.0 - k)
        if bid > 0 and p <= bid:    # 让价会越过买一=可能吃单，则退回卖一
            p = ask if ask > 0 else ref
        return p


def _set_leverage_for_all(client, steps: List[Dict[str, Any]], leverage: int, failed: List[Dict[str, Any]]):
    for sym in sorted({s["symbol"] for s in steps}):
        try:
            client.set_leverage_for_symbol(sym, leverage)
        except Exception as e:
            failed.append({"symbol": sym, "stage": "set_leverage", "error": str(e)[:120]})


def _market_fill(client, step: Dict[str, Any], amount_coin: float, executed: List[Dict[str, Any]],
                 failed: List[Dict[str, Any]], note: str = ""):
    """市价兜底下单（数量为币数量，内部自动转张数）。"""
    try:
        r = client.place_order(step["side"], "market", amount=amount_coin,
                               symbol=step["symbol"], reduce_only=step["reduce_only"])
        executed.append({**step, "amount_coin": round(amount_coin, 8), "order_id": r.get("id"),
                         "status": r.get("status", "ok"), "via": "market", "note": note})
        return True
    except Exception as e:
        failed.append({**step, "amount_coin": round(amount_coin, 8), "via": "market", "error": str(e)[:160]})
        return False


def execute_plan(plan: Dict[str, Any], client, live: bool = False,
                 leverage: int = DEFAULT_LEVERAGE, entry_mode: str = XS_ENTRY_MARKET,
                 maker_ttl: float = DEFAULT_MAKER_TTL,
                 maker_improve_bps: float = DEFAULT_MAKER_IMPROVE_BPS) -> Dict[str, Any]:
    """按计划下单。live=False 仅干跑；live=True 真实调用 client.place_order。异常逐腿捕获。

    entry_mode:
      - market：全部市价 taker（旧版行为，默认）。
      - maker_market：每条腿先挂 post-only 限价(maker)，统一等待 maker_ttl 秒；
        未成交的余量撤单并转市价兜底，保证该换的仓一定换完。XS 必须每日调仓到位，故只做"保证成交"模式。
    """
    if not plan.get("ok"):
        return {"live": live, "executed": [], "failed": [], "blocked": plan.get("blocked", []), **plan}
    steps = plan["steps"]
    blocked = plan.get("blocked", [])
    mode = str(entry_mode or XS_ENTRY_MARKET).lower()
    # XS 场景不做"纯 maker 不追"（漏单会破坏每日调仓）；任何 maker 入参都走 maker_market 保证成交。
    if mode in ("maker", "post_only", "postonly"):
        mode = XS_ENTRY_MAKER_MARKET
    if mode != XS_ENTRY_MAKER_MARKET:
        mode = XS_ENTRY_MARKET

    if not live:
        return {"live": False, "dry_run": True, "entry_mode": mode,
                "executed": [{**s, "status": "dry"} for s in steps],
                "failed": [], "blocked": blocked, "n_orders": len(steps)}
    if not getattr(client, "is_connected", False):
        return {"live": True, "executed": [], "failed": [{"error": "OKX 未连接，拒绝实盘下单"}],
                "blocked": blocked, **plan}

    executed, failed = [], []
    _set_leverage_for_all(client, steps, leverage, failed)

    # 两阶段换手：先执行全部 reduce_only 平仓单（成交后释放保证金），再执行开仓单。
    # 否则旧仓仍占用保证金时就挂/下开仓单，会因可用保证金不足被拒（总名义 2 倍满仓时尤其明显）。
    closes = [s for s in steps if s.get("reduce_only")]
    opens = [s for s in steps if not s.get("reduce_only")]

    def _market_batch(batch: List[Dict[str, Any]]) -> int:
        n = 0
        for s in batch:
            if _market_fill(client, s, s["amount_coin"], executed, failed):
                n += 1
        return n

    if mode == XS_ENTRY_MARKET:
        mfall = _market_batch(closes) + _market_batch(opens)   # 先平后开
        return {"live": True, "entry_mode": mode, "executed": executed, "failed": failed,
                "blocked": blocked, "n_orders": len(steps), "maker_filled": 0,
                "market_fallback": mfall, "phase": "close_then_open"}

    # ===== maker_market：每批先挂 post-only 限价、统一等待、撤单补市价；两阶段执行 =====
    ttl = max(0.0, float(maker_ttl or 0.0))

    def _maker_batch(batch: List[Dict[str, Any]]) -> Tuple[int, int]:
        """处理一批腿（先传平仓批、后传开仓批），返回 (maker成交条数, 市价兜底条数)。"""
        if not batch:
            return 0, 0
        pending: List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], float]] = []
        market_legs: List[Tuple[Dict[str, Any], float, str]] = []
        for s in batch:
            price = None
            ticker: Dict[str, Any] = {}
            try:
                tk = client.get_ticker(s["symbol"])
                if tk:
                    ticker = tk
                mp = maker_limit_price(ticker, s["side"], maker_improve_bps)
                if mp and mp > 0:
                    pp = client.price_to_precision(s["symbol"], mp) if hasattr(client, "price_to_precision") else None
                    price = pp if pp else mp
            except Exception:
                price = None
            if not price or price <= 0:
                market_legs.append((s, s["amount_coin"], "无盘口，直接市价"))
                continue
            try:
                order = client.place_order(s["side"], "limit", amount=s["amount_coin"], price=price,
                                           symbol=s["symbol"], reduce_only=s["reduce_only"], post_only=True)
                pending.append((s, order or {}, ticker, float(price)))
            except Exception as e:
                # post-only 被拒（会立即吃单）或提交失败：该腿转市价兜底，不阻塞其它腿
                market_legs.append((s, s["amount_coin"], "限价未挂出，转市价:" + str(e)[:60]))

        if pending and ttl > 0:
            time.sleep(ttl)

        maker_n = 0
        for s, order, ticker, price in pending:
            oid = order.get("id")
            latest = order
            filled_coin = 0.0
            try:
                if oid:
                    detail = client.fetch_order(oid, s["symbol"])
                    if detail:
                        latest = detail
                filled_coin = _filled_coin(client, latest, s["symbol"])
                status = str(latest.get("status", "") or "").lower()
                if status not in ("closed", "filled", "canceled", "cancelled", "rejected", "expired"):
                    try:
                        client.cancel_order(oid, s["symbol"])
                    except Exception:
                        pass
                    # 撤单后再查一次，确认最终成交量（撤单瞬间可能又成交一部分）
                    try:
                        detail2 = client.fetch_order(oid, s["symbol"])
                        if detail2:
                            filled_coin = max(filled_coin, _filled_coin(client, detail2, s["symbol"]))
                    except Exception:
                        pass
            except Exception as e:
                # 查询失败时务必先 best-effort 撤掉残留限价单，否则可能已部分成交而我们按 0 处理、再全额转市价，造成超仓
                try:
                    if oid:
                        client.cancel_order(oid, s["symbol"])
                except Exception:
                    pass
                failed.append({**s, "stage": "maker_query", "error": str(e)[:120]})

            if filled_coin > 0:
                executed.append({**s, "order_id": oid, "status": "maker_filled", "via": "maker",
                                 "price": price, "filled_coin": round(filled_coin, 8)})
                maker_n += 1
            ref = float(ticker.get("last") or price or 0)
            remaining = s["amount_coin"] - filled_coin
            if remaining > 0 and remaining * ref > MIN_NOTIONAL_USDT:
                market_legs.append((s, remaining, "maker未全成交，余量转市价"))

        mkt_n = 0
        for s, amt, note in market_legs:
            if amt and amt > 0 and _market_fill(client, s, amt, executed, failed, note=note):
                mkt_n += 1
        return maker_n, mkt_n

    # 阶段1：先平仓（释放保证金）；阶段2：再开新仓。maker 各等 ttl，平仓兜底市价确保资金到位。
    mc_close, mk_close = _maker_batch(closes)
    mc_open, mk_open = _maker_batch(opens)
    return {"live": True, "entry_mode": mode, "executed": executed, "failed": failed,
            "blocked": blocked, "n_orders": len(steps),
            "maker_filled": mc_close + mc_open, "market_fallback": mk_close + mk_open,
            "maker_close": mc_close, "maker_open": mc_open,
            "market_close": mk_close, "market_open": mk_open,
            "phase": "close_then_open",
            "maker_ttl": ttl, "maker_improve_bps": maker_improve_bps}
