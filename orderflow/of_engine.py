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

from of_core import Bar, Detector, SIGNAL_NAMES, LIVE_ONLY, auto_row_size, volume_profile, imbalances, stacked
from of_playbook import Playbook, PB_NAMES

ALL_NAMES = {**SIGNAL_NAMES, **PB_NAMES}     # 形态打法 + 实战打法（关键位 + 订单流确认）
PB_HOLD_MS = 120 * 60_000                    # 实战打法最多拿 120 分钟（和回测一样）
PB_FILL_MS = 4 * 60_000                      # 限价单挂 3~4 分钟没成交就撤

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "of_state.json")
BIG_USD = {"BTC": 200_000, "ETH": 100_000, "SOL": 50_000}     # 单笔（同一毫秒同方向合并）超过这个金额算大单

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
        self.pb = Playbook(row)                       # 实战打法：1 分钟足迹 + 关键位
        self.pb_builder = BarBuilder("1m", row, self._on_pb_bar)
        self.limit_orders: list = []                  # 实战打法的限价单（价格碰到才进场）
        self.signals: list[dict] = []
        self.last = math.nan
        self.pending: list = []       # 收盘出信号，下一笔成交进场
        self.backfilling = False
        self.big_usd = BIG_USD.get(inst.split("-")[0], 50_000)
        self._agg = None              # 正在合并的一笔（同毫秒、同方向）
        self.ext = {"funding": math.nan, "next_funding": 0, "oi": math.nan, "oi_usd": math.nan,
                    "oi_hist": [], "ls": math.nan, "top_ls": math.nan, "liqs": [], "bigs": [], "obi": math.nan}
        self.wall_seen: dict = {}     # (方向, 行) -> 第一次看到的时间；挂够 30 秒才算真墙（防假挂单）


    def _on_bar(self, bar: Bar, seeded=False):
        sigs = self.det.on_bar(bar)
        if seeded:
            return

        for s in sigs:
            d = {"t": bar.t, "kind": s.kind, "name": ALL_NAMES[s.kind], "side": s.side,
                 "stop": s.stop, "target": s.target, "price": bar.c, "note": s.note,
                 "traded": False}
            if self.backfilling:
                d["skip"] = "历史信号"
            self.signals.append(d)
            self.signals = self.signals[-200:]
            if not self.backfilling:
                self.pending.append((s, d))

    def _on_pb_bar(self, bar: Bar, seeded=False):
        for s in self.pb.on_bar(bar):
            d = {"t": bar.t, "kind": s.kind, "name": ALL_NAMES[s.kind], "side": s.side, "stop": s.stop,
                 "target": s.target, "price": s.entry, "note": f"关键位：{s.level}，限价 {s.entry:.6g}", "traded": False}
            if self.backfilling:
                d["skip"] = "历史信号"
            self.signals = (self.signals + [d])[-200:]
            if not self.backfilling:
                self.limit_orders.append((s, d, bar.t + 60_000 + PB_FILL_MS))

    def _check_limits(self, price, ts):
        keep = []
        for s, d, until in self.limit_orders:
            if ts > until:
                d["skip"] = "限价没成交，已撤"
                continue
            if (s.side == 1 and price <= s.entry) or (s.side == -1 and price >= s.entry):
                self.app.try_open(self, s, d, price, ts)
                if not d.get("traded") and not d.get("skip"):
                    d["skip"] = "没开成"
                continue
            keep.append((s, d, until))
        self.limit_orders = keep

    def _cur_bar(self, ts):
        b = self.builder.cur
        if b is not None and b.t <= ts < b.t + self.builder.tf_ms:
            return b
        return None

    def _flush_big(self):
        a = self._agg
        self._agg = None
        if not a or a["usd"] < self.big_usd:
            return
        b = self._cur_bar(a["ts"]) or self.builder.cur
        if b is None:
            return
        side = 1 if a["buy"] else -1
        if a["buy"]:
            b.big_buy += a["q"]
        else:
            b.big_sell += a["q"]
        item = (a["px"], a["q"], side, a["ts"])
        b.bigs.append(item[:3])
        self.ext["bigs"] = (self.ext["bigs"] + [item])[-50:]

    def on_trade(self, price, size, is_buy, ts):
        self.last = price
        qty = size * self.ct
        # 合并大单：同一毫秒、同方向的成交算一笔
        a = self._agg
        if a and a["ts"] == ts and a["buy"] == is_buy:
            a["q"] += qty; a["usd"] += qty * price; a["px"] = price
        else:
            self._flush_big()
            self._agg = {"ts": ts, "buy": is_buy, "q": qty, "usd": qty * price, "px": price}
        self.builder.add(price, qty, is_buy, ts)
        self.pb_builder.add(price, qty, is_buy, ts)
        c = self.builder.cur
        if c is not None and math.isnan(c.oi):        # 新K线：先带上最新的持仓、费率、多空比
            c.oi, c.funding, c.ls = self.ext["oi"], self.ext["funding"], self.ext["ls"]
        if self.backfilling:
            return
        if self.limit_orders:
            self._check_limits(price, ts)
        if self.pending:
            todo, self.pending = self.pending, []
            for s, d in todo:
                self.app.try_open(self, s, d, price, ts)
        self.app.check_exits(self, price, ts)

    def on_liq(self, price, size, side, ts, history=False):
        qty = size * self.ct
        item = {"px": price, "q": qty, "usd": qty * price, "side": side, "ts": ts}
        self.ext["liqs"] = (self.ext["liqs"] + [item])[-100:]
        if history:
            return
        b = self._cur_bar(ts) or self.builder.cur
        if b is not None:
            if side == -1:
                b.liq_long += qty
            else:
                b.liq_short += qty
            b.liqs.append((price, qty, side))

    def on_oi(self, oi_coin, oi_usd, ts):
        self.ext["oi"], self.ext["oi_usd"] = oi_coin, oi_usd
        h = self.ext["oi_hist"]
        h.append((ts, oi_coin))
        self.ext["oi_hist"] = h[-2000:]
        if self.builder.cur is not None:
            self.builder.cur.oi = oi_coin

    def on_funding(self, rate, next_ts):
        self.ext["funding"], self.ext["next_funding"] = rate, next_ts
        if self.builder.cur is not None:
            self.builder.cur.funding = rate

    def on_ratio(self, ls, top_ls):
        self.ext["ls"], self.ext["top_ls"] = ls, top_ls
        if self.builder.cur is not None:
            self.builder.cur.ls = ls

    def sample_book(self, ts):
        """每 2 秒采样一次盘口：热力图、盘口失衡、大单墙"""
        b = self.builder.cur
        if b is None or math.isnan(self.last):
            return
        bids, asks = self.app.feeds[self.inst].book.levels()
        if not bids or not asks:
            return
        row, px = self.row, self.last
        hb, ha = {}, {}
        for p_, q in bids.items():
            r = int(math.floor(p_ / row))
            hb[r] = hb.get(r, 0.0) + q * self.ct
        for p_, q in asks.items():
            r = int(math.floor(p_ / row))
            ha[r] = ha.get(r, 0.0) + q * self.ct
        r0 = int(math.floor(px / row))
        for r in range(r0 - 80, r0 + 81):
            if r in hb or r in ha:
                cell = b.heat.setdefault(r, [0.0, 0.0, 0])
                cell[0] += hb.get(r, 0.0); cell[1] += ha.get(r, 0.0); cell[2] += 1
        # 盘口失衡：价格上下 0.3% 以内
        lo, hi = px * 0.997, px * 1.003
        sb = sum(q for p_, q in bids.items() if p_ >= lo)
        sa = sum(q for p_, q in asks.items() if p_ <= hi)
        obi = (sb - sa) / (sb + sa) if sb + sa > 0 else 0.0
        n = getattr(b, "_obi_n", 0)
        b.obi = obi if n == 0 or math.isnan(b.obi) else (b.obi * n + obi) / (n + 1)
        b._obi_n = n + 1
        self.ext["obi"] = obi
        # 大单墙：附近几格里挂单 ≥ 平均每格 5 倍，并且已经挂了 30 秒以上
        near = [hb.get(r, 0) for r in range(r0 - 40, r0 + 1)] + [ha.get(r, 0) for r in range(r0, r0 + 41)]
        avg = sum(near) / max(1, len(near))
        p = self.det.p
        now = time.time()
        live_walls = set()
        wb = wa = math.nan
        for k in range(0, p["wall_rows"] + 1):
            rb, ra = r0 - k, r0 + k
            if hb.get(rb, 0) >= p["wall_mult"] * avg > 0:
                live_walls.add((1, rb))
                if now - self.wall_seen.setdefault((1, rb), now) >= 30 and math.isnan(wb):
                    wb = rb * row
            if ha.get(ra, 0) >= p["wall_mult"] * avg > 0:
                live_walls.add((-1, ra))
                if now - self.wall_seen.setdefault((-1, ra), now) >= 30 and math.isnan(wa):
                    wa = (ra + 1) * row
        self.wall_seen = {k: v for k, v in self.wall_seen.items() if k in live_walls}
        b.wall_bid, b.wall_ask = wb, wa

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
                        "delta": b.delta, "vol": b.vol, "poc": b.poc_row(),
                        "heat": [[r, round(x / c, 4), round(y / c, 4)] for r, (x, y, c) in b.heat.items() if c],
                        "liq_long": b.liq_long, "liq_short": b.liq_short, "liqs": b.liqs[-60:],
                        "big_buy": b.big_buy, "big_sell": b.big_sell, "bigs": b.bigs[-60:],
                        "oi": b.oi, "obi": b.obi, "wall_bid": b.wall_bid, "wall_ask": b.wall_ask})
        real = [b for b in self.builder.bars if not getattr(b, "seeded", False)][-288:]
        vp = volume_profile(real + ([self.builder.cur] if self.builder.cur else []), self.row)
        bids, asks = self.app.feeds[self.inst].book.top(80)
        return {"inst": self.inst, "tf": self.tf, "row": self.row, "last": self.last, "bars": out,
                "profile": None if not vp else {"poc": vp["poc"], "vah": vp["vah"], "val": vp["val"],
                                                "rows": sorted(vp["profile"].items())},
                "prev_day": self.det.prev_day_profile and {k: self.det.prev_day_profile[k] for k in ("poc", "vah", "val")},
                "dom": {"bids": [[p, s * self.ct] for p, s in bids], "asks": [[p, s * self.ct] for p, s in asks]},
                "zones": [z for z in self.det.zones if not z["used"]],
                "signals": self.signals[-30:],
                "ext": {k: v for k, v in self.ext.items() if k != "oi_hist"},
                "levels": [] if math.isnan(self.last) else [[x, n] for x, n in self.pb.levels(self.last)],
                "oi_1h": self._oi_change(3600_000),
                "extras_status": getattr(self.app.extras.get(self.inst), "status", "")}

    def _oi_change(self, ms):
        h = self.ext["oi_hist"]
        if len(h) < 2:
            return None
        t1, v1 = h[-1]
        old = [v for t, v in h if t <= t1 - ms]
        v0 = old[-1] if old else h[0][1]
        return (v1 / v0 - 1) if v0 else None


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
        self.extras = {}
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
        if s.kind in PB_NAMES:
            eng.pb.trades_today += 1
        pos = Position(eng.inst, s.kind, s.side, qty, fill, s.stop, s.target, ts,
                       ts + (PB_HOLD_MS if s.kind in PB_NAMES else self.cfg.get("max_hold_bars", 48) * TF_MS[eng.tf]),
                       oid, br is not self.paper)
        self.acct.positions.append(pos)
        d["traded"] = True
        self.say(f"[{br.name}] 开{'多' if s.side == 1 else '空'} {eng.inst} {ALL_NAMES[s.kind]} 价 {fill:.6g} 数量 {qty:.6g} 止损 {s.stop:.6g} 止盈 {s.target:.6g}")
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
            rec = {"sym": pos.sym, "kind": ALL_NAMES[pos.kind], "side": pos.side, "entry": pos.entry,
                   "exit": exit_px, "pnl": round(pnl, 4), "why": why, "t_open": pos.t_open, "t_close": ts,
                   "live": pos.live}
            self.history.append(rec)
            with open(TRADE_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if pos.kind in PB_NAMES and eng is not None:
                eng.pb.record_result(pnl > 0)
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
                "symbols": [x for x in self.cfg.get("symbols_live", self.cfg["symbols"]) if x in self.engines],
                "feed": {k: f.status for k, f in self.feeds.items()},
                "cfg": self.cfg, "live_ok": self.live_confirmed, "allow_live": self.allow_live,
                "signal_names": ALL_NAMES, "live_only": sorted(LIVE_ONLY), "pb_kinds": sorted(PB_NAMES),
                "acct": {**asdict(self.acct), "positions": [asdict(p) for p in self.acct.positions]},
                "last": {k: e.last for k, e in self.engines.items()},
                "history": self.history[-50:], "log": self.log[-60:]}
