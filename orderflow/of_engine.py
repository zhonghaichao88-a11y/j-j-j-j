"""订单流实盘引擎：逐笔成交 → 足迹K线 → 形态识别 → 下单（模拟 / 实盘）+ 风控。

安全规则：
  - 默认模拟盘。实盘要同时满足：.env 里 OF_ALLOW_LIVE=1、配置 mode=live、网页上手动输入确认口令。
  - 每单带止损止盈；单日亏损到上限自动停止开新单；同时持仓数有上限。
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import asdict, dataclass, field

from of_core import Bar, Detector, SIGNAL_NAMES, auto_row_size, volume_profile, imbalances, stacked

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "of_state.json")
TRADE_LOG = os.path.join(HERE, "of_trades.jsonl")
TF_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000}


# ====================================================================== 足迹K线
class BarBuilder:
    def __init__(self, tf: str, row_size: float, on_close):
        self.tf_ms = TF_MS[tf]
        self.row = row_size
        self.on_close = on_close
        self.bars: list[Bar] = []      # 已收盘
        self.cur: Bar | None = None

    def seed(self, candles):
        """用历史K线垫底（没有足迹明细，只有成交量），让平均量、新高新低能马上算"""
        for t, o, h, l, c, vol in candles:
            b = Bar(t=t, o=o, h=h, l=l, c=c, closed=True)
            r = int(math.floor(c / self.row))
            b.rows[r] = [vol / 2, vol / 2]
            b.seeded = True
            self.bars.append(b)
            self.on_close(b, seeded=True)

    def add(self, price, qty, is_buy, ts):
        t0 = ts - ts % self.tf_ms
        if self.cur is None or t0 > self.cur.t:
            if self.cur is not None:
                self.cur.closed = True
                self.bars.append(self.cur)
                self.bars = self.bars[-300:]
                self.on_close(self.cur)
            self.cur = Bar(t=t0)
        self.cur.add(price, qty, is_buy, self.row)


# ====================================================================== 持仓
@dataclass
class Position:
    sym: str
    kind: str
    side: int
    qty: float            # 币数量
    entry: float
    stop: float
    target: float
    t_open: int
    max_until: int
    order_id: str = ""
    live: bool = False
    checked: float = 0.0  # 实盘：上次向交易所核对的时间


@dataclass
class Account:
    equity: float = 1000.0
    start_equity: float = 1000.0
    day: str = ""
    day_pnl: float = 0.0
    positions: list = field(default_factory=list)
    closed: int = 0
    wins: int = 0


class Risk:
    def __init__(self, cfg):
        self.cfg = cfg

    def can_open(self, acct: Account, sym: str, side: int):
        c = self.cfg
        if acct.day_pnl <= -c["daily_loss_pct"] / 100 * acct.start_equity:
            return False, "今天亏损到上限，停止开新单"
        if len(acct.positions) >= c["max_positions"]:
            return False, "持仓数已满"
        if any(p.sym == sym for p in acct.positions):
            return False, "这个币已有持仓"
        return True, ""

    def size(self, acct: Account, entry: float, stop: float):
        """按止损金额定仓位：每单最多亏权益的 risk_pct%，同时杠杆不超过 max_leverage"""
        risk_amt = acct.equity * self.cfg["risk_pct"] / 100
        qty = risk_amt / max(abs(entry - stop), 1e-12)
        cap = acct.equity * self.cfg["max_leverage"] / entry
        return min(qty, cap)


# ====================================================================== 下单通道
class PaperBroker:
    name = "模拟盘"
    fee, slip = 0.0005, 0.0002

    def open(self, sym, side, qty, price, stop, target):
        return price * (1 + side * self.slip), "paper"

    def close(self, pos: Position, price):
        return price * (1 - pos.side * self.slip)

    def check_exit(self, pos: Position, price):
        if pos.side == 1:
            if price <= pos.stop:
                return "止损"
            if price >= pos.target:
                return "止盈"
        else:
            if price >= pos.stop:
                return "止损"
            if price <= pos.target:
                return "止盈"
        return None


class OkxLiveBroker(PaperBroker):
    """欧易实盘：市价开仓，同时挂好止损止盈（交易所端执行，程序断线也有效）。"""
    name = "实盘"

    def __init__(self, keys: dict, proxy: str | None, leverage: int):
        import ccxt
        cfg = {"apiKey": keys["key"], "secret": keys["secret"], "password": keys["passphrase"],
               "options": {"defaultType": "swap"}, "enableRateLimit": True}
        if proxy:
            cfg["httpsProxy"] = proxy
        self.ex = ccxt.okx(cfg)
        self.ex.load_markets()
        self.leverage = leverage
        self.lock = threading.Lock()

    def _sym(self, inst):            # BTC-USDT-SWAP -> BTC/USDT:USDT
        base = inst.split("-")[0]
        return f"{base}/USDT:USDT"

    def open(self, inst, side, qty, price, stop, target):
        with self.lock:
            s = self._sym(inst)
            m = self.ex.market(s)
            contracts = float(self.ex.amount_to_precision(s, qty / m["contractSize"]))
            if contracts < (m["limits"]["amount"]["min"] or 0):
                raise RuntimeError("仓位太小，低于欧易最小下单量")
            try:
                self.ex.set_leverage(self.leverage, s, params={"mgnMode": "isolated"})
            except Exception:  # noqa: BLE001
                pass
            o = self.ex.create_order(s, "market", "buy" if side == 1 else "sell", contracts, params={
                "tdMode": "isolated",
                "stopLoss": {"triggerPrice": self.ex.price_to_precision(s, stop), "type": "market"},
                "takeProfit": {"triggerPrice": self.ex.price_to_precision(s, target), "type": "market"},
            })
            fill = float(o.get("average") or price)
            return fill, o.get("id", "")

    def close(self, pos: Position, price):
        with self.lock:
            s = self._sym(pos.sym)
            m = self.ex.market(s)
            contracts = float(self.ex.amount_to_precision(s, pos.qty / m["contractSize"]))
            o = self.ex.create_order(s, "market", "sell" if pos.side == 1 else "buy", contracts,
                                     params={"tdMode": "isolated", "reduceOnly": True})
            try:   # 撤掉还挂着的止损止盈
                for a in self.ex.fetch_open_orders(s, params={"stop": True, "ordType": "oco"}):
                    self.ex.cancel_order(a["id"], s, params={"stop": True})
            except Exception:  # noqa: BLE001
                pass
            return float(o.get("average") or price)

    def has_position(self, inst) -> bool:
        with self.lock:
            ps = self.ex.fetch_positions([self._sym(inst)])
            return any(abs(float(p.get("contracts") or 0)) > 0 for p in ps)


# ====================================================================== 引擎
DEFAULT_CFG = {
    "symbols": ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"],
    "tf": "5m",
    "enabled": [],              # 自动交易用哪些打法；回测不过关的默认不开
    "auto": False,              # 自动交易总开关
    "mode": "paper",            # paper / live
    "risk_pct": 0.5,            # 每单最多亏权益的 0.5%
    "max_leverage": 3,
    "max_positions": 2,
    "daily_loss_pct": 3,
    "paper_equity": 1000,
}


class SymbolEngine:
    def __init__(self, app, inst, tf, row, ct_val):
        self.app, self.inst, self.tf, self.row, self.ct = app, inst, tf, row, ct_val
        self.det = Detector(row, enabled=list(SIGNAL_NAMES))
        self.builder = BarBuilder(tf, row, self._on_bar)
        self.signals: list[dict] = []
        self.last = math.nan
        self.pending: list = []       # 收盘出信号，下一笔成交进场
        self.backfilling = False

    def _on_bar(self, bar: Bar, seeded=False):
        sigs = self.det.on_bar(bar)
        if seeded:
            return
        for s in sigs:
            d = {"t": bar.t, "kind": s.kind, "name": SIGNAL_NAMES[s.kind], "side": s.side,
                 "stop": s.stop, "target": s.target, "price": bar.c, "note": s.note,
                 "traded": False}
            if self.backfilling:
                d["skip"] = "历史信号"
            self.signals.append(d)
            self.signals = self.signals[-200:]
            if not self.backfilling:
                self.pending.append((s, d))

    def on_trade(self, price, size, is_buy, ts):
        self.last = price
        self.builder.add(price, size * self.ct, is_buy, ts)
        if self.backfilling:
            return
        if self.pending:
            todo, self.pending = self.pending, []
            for s, d in todo:
                self.app.try_open(self, s, d, price, ts)
        self.app.check_exits(self, price, ts)

    def view(self, n=40):
        bars = self.builder.bars[-(n - 1):] + ([self.builder.cur] if self.builder.cur else [])
        out = []
        for b in bars:
            if b is None:
                continue
            bi, si = imbalances(b) if not getattr(b, "seeded", False) else (set(), set())
            out.append({"t": b.t, "o": b.o, "h": b.h, "l": b.l, "c": b.c,
                        "seeded": bool(getattr(b, "seeded", False)),
                        "rows": [[r, round(x, 4), round(y, 4)] for r, (x, y) in sorted(b.rows.items())],
                        "buy_imb": sorted(bi), "sell_imb": sorted(si),
                        "stack_buy": stacked(bi, 3), "stack_sell": stacked(si, 3),
                        "delta": b.delta, "vol": b.vol, "poc": b.poc_row()})
        real = [b for b in self.builder.bars if not getattr(b, "seeded", False)][-288:]
        vp = volume_profile(real + ([self.builder.cur] if self.builder.cur else []), self.row)
        bids, asks = self.app.feeds[self.inst].book.top(80)
        return {"inst": self.inst, "tf": self.tf, "row": self.row, "last": self.last, "bars": out,
                "profile": None if not vp else {"poc": vp["poc"], "vah": vp["vah"], "val": vp["val"],
                                                "rows": sorted(vp["profile"].items())},
                "prev_day": self.det.prev_day_profile and {k: self.det.prev_day_profile[k] for k in ("poc", "vah", "val")},
                "dom": {"bids": [[p, s * self.ct] for p, s in bids], "asks": [[p, s * self.ct] for p, s in asks]},
                "zones": [z for z in self.det.zones if not z["used"]],
                "signals": self.signals[-30:]}


class OrderFlowApp:
    def __init__(self, cfg: dict, proxy: str | None, keys: dict | None, allow_live: bool):
        self.cfg = dict(DEFAULT_CFG, **cfg)
        self.proxy, self.keys, self.allow_live = proxy, keys, allow_live
        self.risk = Risk(self.cfg)
        self.acct = Account(equity=self.cfg["paper_equity"], start_equity=self.cfg["paper_equity"])
        self.paper = PaperBroker()
        self.live: OkxLiveBroker | None = None
        self.live_confirmed = False
        self.engines: dict[str, SymbolEngine] = {}
        self.feeds = {}
        self.log: list[str] = []
        self.history: list[dict] = []
        self._load()

    # ---------------------------------------------------------- 状态
    def _load(self):
        if os.path.exists(STATE_FILE):
            try:
                st = json.load(open(STATE_FILE, encoding="utf-8"))
                self.acct = Account(**{k: v for k, v in st["acct"].items() if k != "positions"})
                self.acct.positions = [Position(**p) for p in st["acct"].get("positions", [])]
                self.history = st.get("history", [])[-200:]
            except Exception as e:  # noqa: BLE001
                self.say(f"读取状态失败，重新开始：{e}")

    def save(self):
        st = {"acct": {**{k: v for k, v in asdict(self.acct).items() if k != "positions"},
                       "positions": [asdict(p) for p in self.acct.positions]},
              "history": self.history[-200:]}
        tmp = STATE_FILE + ".tmp"
        json.dump(st, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, STATE_FILE)

    def say(self, msg):
        line = time.strftime("%H:%M:%S ") + msg
        self.log.append(line)
        self.log = self.log[-200:]
        print(line, flush=True)

    @property
    def broker(self):
        if self.cfg["mode"] == "live" and self.live_confirmed and self.live is not None:
            return self.live
        return self.paper

    def enable_live(self, phrase: str):
        if not self.allow_live:
            return False, "没有开启实盘权限：需要在 .env 里加 OF_ALLOW_LIVE=1 并重启"
        if not self.keys or not all(self.keys.values()):
            return False, ".env 里没有填欧易 API 密钥"
        if phrase.strip() != "我确认实盘":
            return False, "确认口令不对"
        try:
            self.live = OkxLiveBroker(self.keys, self.proxy, int(self.cfg["max_leverage"]))
        except Exception as e:  # noqa: BLE001
            return False, f"连接欧易失败：{e}"
        self.live_confirmed = True
        self.cfg["mode"] = "live"
        self.say("已切换到实盘")
        return True, "已切换到实盘"

    def disable_live(self):
        self.live_confirmed = False
        self.cfg["mode"] = "paper"
        self.say("已切回模拟盘")

    # ---------------------------------------------------------- 交易
    def _roll_day(self):
        d = time.strftime("%Y-%m-%d", time.gmtime())
        if d != self.acct.day:
            self.acct.day, self.acct.day_pnl, self.acct.start_equity = d, 0.0, self.acct.equity

    def try_open(self, eng: SymbolEngine, s, d, price, ts):
        self._roll_day()
        if not self.cfg["auto"] or s.kind not in self.cfg["enabled"]:
            return
        if (s.side == 1 and not (s.stop < price < s.target)) or (s.side == -1 and not (s.target < price < s.stop)):
            d["skip"] = "价格已越过止损或止盈"
            return
        ok, why = self.risk.can_open(self.acct, eng.inst, s.side)
        if not ok:
            d["skip"] = why
            return
        qty = self.risk.size(self.acct, price, s.stop)
        br = self.broker
        try:
            fill, oid = br.open(eng.inst, s.side, qty, price, s.stop, s.target)
        except Exception as e:  # noqa: BLE001
            d["skip"] = f"下单失败：{e}"
            self.say(f"{eng.inst} 下单失败：{e}")
            return
        pos = Position(eng.inst, s.kind, s.side, qty, fill, s.stop, s.target, ts,
                       ts + self.cfg.get("max_hold_bars", 48) * TF_MS[eng.tf], oid, br is not self.paper)
        self.acct.positions.append(pos)
        d["traded"] = True
        self.say(f"[{br.name}] 开{'多' if s.side == 1 else '空'} {eng.inst} {SIGNAL_NAMES[s.kind]} 价 {fill:.6g} 数量 {qty:.6g} 止损 {s.stop:.6g} 止盈 {s.target:.6g}")
        self.save()

    def check_exits(self, eng: SymbolEngine, price, ts):
        for pos in list(self.acct.positions):
            if pos.sym != eng.inst:
                continue
            why = self.paper.check_exit(pos, price)
            if why is None and ts >= pos.max_until:
                why = "到时间"
            if why is None:
                continue
            br = self.live if (pos.live and self.live) else self.paper
            try:
                if pos.live and why in ("止损", "止盈"):
                    # 实盘的止损止盈挂在交易所，这里只核对交易所那边是不是已经平掉（最多 3 秒问一次）
                    if time.time() - pos.checked < 3:
                        continue
                    pos.checked = time.time()
                    if self.live.has_position(pos.sym):
                        continue
                    exit_px = pos.stop if why == "止损" else pos.target
                else:
                    exit_px = br.close(pos, price)
            except Exception as e:  # noqa: BLE001
                self.say(f"{pos.sym} 平仓出错：{e}")
                continue
            fee = PaperBroker.fee * (pos.entry + exit_px) * pos.qty
            pnl = pos.side * (exit_px - pos.entry) * pos.qty - fee
            self.acct.positions.remove(pos)
            self.acct.equity += pnl
            self.acct.day_pnl += pnl
            self.acct.closed += 1
            self.acct.wins += pnl > 0
            rec = {"sym": pos.sym, "kind": SIGNAL_NAMES[pos.kind], "side": pos.side, "entry": pos.entry,
                   "exit": exit_px, "pnl": round(pnl, 4), "why": why, "t_open": pos.t_open, "t_close": ts,
                   "live": pos.live}
            self.history.append(rec)
            with open(TRADE_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self.say(f"平仓 {pos.sym} {why} 盈亏 {pnl:+.2f}U")
            self.save()

    def close_all(self):
        for pos in list(self.acct.positions):
            eng = self.engines.get(pos.sym)
            if eng and not math.isnan(eng.last):
                pos.max_until = 0
                self.check_exits(eng, eng.last, int(time.time() * 1000))

    def state(self, inst):
        eng = self.engines.get(inst)
        return {"view": eng.view() if eng else None,
                "symbols": [x for x in self.cfg["symbols"] if x in self.engines],
                "feed": {k: f.status for k, f in self.feeds.items()},
                "cfg": self.cfg, "live_ok": self.live_confirmed, "allow_live": self.allow_live,
                "signal_names": SIGNAL_NAMES,
                "acct": {**asdict(self.acct), "positions": [asdict(p) for p in self.acct.positions]},
                "last": {k: e.last for k, e in self.engines.items()},
                "history": self.history[-50:], "log": self.log[-60:]}
