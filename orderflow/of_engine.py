"""订单流实盘引擎：逐笔成交 → 足迹K线 → 形态识别 → 下单（模拟 / 实盘）+ 风控。

安全规则：
  - 默认模拟盘。实盘要同时满足：.env 里 OF_ALLOW_LIVE=1、配置 mode=live、网页上手动输入确认口令。
  - 每单带止损止盈；单日亏损到上限自动停止开新单；同时持仓数有上限。
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import threading
import time
from dataclasses import asdict, dataclass, field

from of_core import Bar, Detector, Signal, SIGNAL_NAMES, LIVE_ONLY, auto_row_size, volume_profile, imbalances, stacked
from of_playbook import Playbook, PB_NAMES
import of_notify
from of_live import OkxLive, LiveError

# 全网组合打法（2024-01 ~ 2026-03、104 个币长数据检验过，见 分析/长数据2024-2026/）
FLUSH_NAMES = {"flush_spot": "清洗接盘（现货）", "squeeze_long": "轧空追多"}
# 清洗接盘（严格版，拿 12 小时，挂单进场）：104 个币 1314 笔，胜率 53%，每笔 +0.89%，PF 1.40，2024/2025/2026 都赚；
# 新加的 58 个币（没用来定参数）上每笔 +0.62%，PF 1.26
FLUSH = {"drop": 0.02,          # 1 小时跌超 2%
         "oi_drop": 0.05,       # 持仓量 1 小时降超 5%
         "spot_flow": 0.05,     # 币安现货 1 小时主动买比卖多 5%
         "stop_x": 3.0,         # 紧急止损 = 这次跌幅的 3 倍
         "hold_h": 12,          # 拿 12 小时
         "size_pct": 10.0,      # 每笔用账户权益的 10% 开仓（不按止损算仓位）
         "fill_min": 5}         # 在信号K线收盘价挂买单，5 分钟内价格回到这里才成交，否则撤单
# 轧空追多（拿 12 小时，下一笔成交市价进场）：104 个币 824 笔，胜率 46%，每笔 +1.98%，PF 1.98，三年都赚；
# 新加的 58 个币上每笔 +1.74%，PF 1.79。大部分单子小亏，靠少数大涨赚钱
SQUEEZE = {"rise": 0.03,        # 1 小时涨超 3%
           "oi_drop": 0.02,     # 持仓量 1 小时降超 2%（空单被强平）
           "stop_x": 1.0,       # 止损 = 进场价下方"这次涨幅"那么远
           "hold_h": 12,
           "size_pct": 10.0}


def squeeze_cfg(cfg):
    return {**SQUEEZE, **(cfg.get("squeeze") or {})}


def flush_cfg(cfg):
    return {**FLUSH, **(cfg.get("flush") or {})}
ALL_NAMES = {**SIGNAL_NAMES, **PB_NAMES, **FLUSH_NAMES}     # 形态打法 + 实战打法 + 全网组合打法
PB_HOLD_MS = 120 * 60_000                    # 实战打法最多拿 120 分钟（和回测一样）
PB_FILL_MS = 4 * 60_000                      # 限价单挂 3~4 分钟没成交就撤

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "of_state.json")
BIG_USD = {"BTC": 200_000, "ETH": 100_000, "SOL": 50_000}     # 单笔（同一毫秒同方向合并）超过这个金额算大单

TRADE_LOG = os.path.join(HERE, "of_trades.jsonl")
LOG_FILE = os.path.join(HERE, "of_log.txt")
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
    contracts: float = 0.0  # 实盘：合约张数
    algo_id: str = ""       # 实盘：交易所上这笔的止盈止损单号
    risk: float = 0.0       # 开仓价到止损的距离（1R）
    half_done: bool = False  # 实战打法：到 1R 已平一半、止损移到保本
    busy: bool = False      # 实盘：正在向交易所操作，别重复下指令
    mgn: str = "isolated"   # 实盘：这笔用的保证金模式（平仓、改止损要用同一个）


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

    def size(self, acct: Account, entry: float, stop: float, equity: float | None = None):
        """按止损金额定仓位：每单最多亏权益的 risk_pct%，同时杠杆不超过 max_leverage（实盘用账户真实权益）"""
        eq = equity if equity else acct.equity
        risk_amt = eq * self.cfg["risk_pct"] / 100
        qty = risk_amt / max(abs(entry - stop), 1e-12)
        cap = eq * self.cfg["max_leverage"] / entry
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


# ====================================================================== 引擎
DEFAULT_CFG = {
    "symbols": ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"],
    "tf": "5m",
    "enabled": [],              # 自动交易用哪些打法；回测不过关的默认不开
    "auto": False,              # 自动交易总开关
    "mode": "paper",            # paper / live
    "risk_pct": 0.5,            # 每单最多亏权益的 0.5%
    "max_leverage": 3,
    "margin_mode": "isolated",  # 实盘保证金模式：isolated 逐仓 / cross 全仓（网页上选）
    "max_positions": 2,
    "guard_n": 20,              # 自动刹车：看每个打法最近多少笔
    "guard_pf": 0.8,            # 最近这些笔的盈亏比低于这个就自动暂停该打法
    "daily_loss_pct": 3,
    "paper_equity": 1000,
    "top_n": 20,                # symbols="auto" 时按 24 小时成交额自动选几个币
    "flush": dict(FLUSH),       # 清洗接盘的参数（网页上可以改）
    "squeeze": dict(SQUEEZE),   # 轧空追多的参数（网页上可以改）
}


VIEW_TFS = ["1m", "5m", "15m", "1h"]          # 每个币同时维护这几个周期的足迹，网页上随时切换


class SymbolEngine:
    """一个币：多周期足迹K线 + 信号（信号周期由配置 tf 决定；实战打法固定用 1 分钟）"""

    def __init__(self, app, inst, tf, rows: dict, ct_val):
        self.app, self.inst, self.tf, self.ct = app, inst, tf, ct_val
        self.rows = rows                              # 每个周期的格子大小
        self.row = rows[tf]
        self.det = Detector(self.row, enabled=list(SIGNAL_NAMES))
        self.builders = {t: BarBuilder(t, rows[t], None)
                         for t in sorted(set(VIEW_TFS + [tf]), key=lambda x: TF_MS[x])}
        self._wire()
        self.pb = Playbook(rows["1m"])                # 实战打法：1 分钟足迹 + 关键位
        self.limit_orders: list = []                  # 实战打法的限价单（价格碰到才进场）
        self.signals: list[dict] = []
        self.last = math.nan
        self.pending: list = []       # 收盘出信号，下一笔成交进场
        self.backfilling = False
        self.big_usd = BIG_USD.get(inst.split("-")[0], 50_000)   # 先用默认值，攒够成交后按这个币自己的分布算
        self._sizes: list = []        # 最近合并后每笔成交的金额，用来算"大单"标准（最大的 0.5%）
        self._agg = None              # 正在合并的一笔（同毫秒、同方向）
        self._n_agg = 0               # 合并后的成交笔数（每 300 笔重算一次大单标准）
        self.ext = {"funding": math.nan, "next_funding": 0, "oi": math.nan, "oi_usd": math.nan,
                    "oi_hist": [], "ls": math.nan, "top_ls": math.nan, "liqs": [], "bigs": [], "obi": math.nan}
        self.wall_seen: dict = {}     # (方向, 行) -> 第一次看到的时间；挂够 30 秒才算真墙（防假挂单）
        self.okx_min: dict = {}       # 分钟 -> [欧易主动买$, 主动卖$]，和币安、Bybit 合起来算全网
        self._last5 = 0               # 上一根已检查的 5 分钟K线
        self._flush_t = 0             # 上次"清洗接盘"信号的时间（同一个币持有期内只做一次）
        self._squeeze_t = 0           # 上次"轧空追多"信号的时间

    def _wire(self):
        """把信号周期的K线接到形态识别器，1 分钟K线接到实战打法"""
        for t, b in self.builders.items():
            b.on_close = self._on_bar if t == self.tf else (self._on_pb_bar if t == "1m" else (lambda b, seeded=False: None))
        if self.tf == "1m":                           # 信号周期就是 1 分钟时，两种打法都挂在同一个构建器上
            self.builders["1m"].on_close = lambda b, seeded=False: (self._on_bar(b, seeded), self._on_pb_bar(b, seeded))
        self.builder = self.builders[self.tf]

    def set_signal_tf(self, tf):
        """马上换信号周期：每个周期的足迹本来就一直在算，只要把识别器换到新周期、用已有K线重新跑一遍"""
        self.tf, self.row = tf, self.rows[tf]
        self._wire()
        self.pending = []
        self.det = Detector(self.row, enabled=list(self.det.enabled))
        for b in self.builder.bars:
            self.det.on_bar(b)

    # ------------------------------------------------------------ 信号
    def _on_bar(self, bar: Bar, seeded=False):
        sigs = self.det.on_bar(bar)
        if seeded:
            return
        for s in sigs:
            d = {"t": bar.t, "tf": self.tf, "kind": s.kind, "name": ALL_NAMES[s.kind], "side": s.side,
                 "stop": s.stop, "target": s.target, "price": bar.c, "note": s.note, "traded": False}
            if self.backfilling:
                d["skip"] = "历史信号"
            self.signals = (self.signals + [d])[-200:]
            self.app.on_signal(self, d)
            if not self.backfilling:
                self.pending.append((s, d))

    def flush_state(self, b5=None):
        """清洗接盘的三个条件现在各是多少：1 小时涨跌、1 小时持仓量变化、币安现货 1 小时主动买卖"""
        b5 = b5 if b5 is not None else self.builders["5m"].bars
        r60 = (b5[-1].c / b5[-13].c - 1) if len(b5) >= 13 and b5[-13].c else math.nan
        oi = self._oi_change(3600_000)
        sf = self.xstats().get("sf_60", math.nan)
        return r60, (math.nan if oi is None else oi), sf

    def _check_flush(self, b5):
        """多头清洗 + 现货接盘 → 做多：1 小时跌超 2%、持仓量 1 小时降超 3%（多单被清）、币安现货 1 小时主动买 > 卖。
        在信号K线收盘价挂买单，拿 12 小时，紧急止损 3 倍跌幅。参数在网页上可以改"""
        self._check_squeeze(b5)
        fc = flush_cfg(self.app.cfg)
        bar = b5[-1]
        if bar.t - self._flush_t < fc["hold_h"] * 3600_000:
            return
        r60, oi, sf = self.flush_state(b5)
        if any(math.isnan(x) for x in (r60, oi, sf)):
            return
        if r60 < -fc["drop"] and oi < -fc["oi_drop"] and sf > fc["spot_flow"]:
            self._flush_t = bar.t
            drop = abs(r60)
            s = Signal("flush_spot", 1, bar.c * (1 - fc["stop_x"] * drop), bar.c * (1 + 2 * fc["stop_x"] * drop), bar.t,
                       f"1小时 {r60:+.1%}，持仓 {oi:+.1%}，现货主动 {sf:+.2f}，挂单 {bar.c:.6g}")
            s.entry = bar.c
            d = {"t": bar.t, "tf": "5m", "kind": s.kind, "name": ALL_NAMES[s.kind], "side": 1, "stop": s.stop,
                 "target": s.target, "price": bar.c, "note": s.note, "traded": False}
            self.signals = (self.signals + [d])[-200:]
            self.app.on_signal(self, d)
            # 收盘时刻 = 这根K线开始 + 5 分钟；挂单从那时起 fill_min 分钟内有效
            self.limit_orders.append((s, d, bar.t + 300_000 + fc["fill_min"] * 60_000))

    def _check_squeeze(self, b5):
        """轧空追多：1 小时涨超 3%、持仓量 1 小时降超 2%（空单被强平，被迫买回）→ 下一笔成交做多，拿 12 小时。
        止损 = 进场价下方这次涨幅那么远；止盈放得很远（+200%），主要靠到时间平仓"""
        qc = squeeze_cfg(self.app.cfg)
        bar = b5[-1]
        if bar.t - self._squeeze_t < qc["hold_h"] * 3600_000:
            return
        r60 = (b5[-1].c / b5[-13].c - 1) if len(b5) >= 13 and b5[-13].c else math.nan
        oi = self._oi_change(3600_000)
        if math.isnan(r60) or oi is None:
            return
        if r60 > qc["rise"] and oi < -qc["oi_drop"]:
            self._squeeze_t = bar.t
            s = Signal("squeeze_long", 1, bar.c * (1 - qc["stop_x"] * r60), bar.c * 3.0, bar.t,
                       f"1小时 {r60:+.1%}，持仓 {oi:+.1%}（空单被强平）")
            d = {"t": bar.t, "tf": "5m", "kind": s.kind, "name": ALL_NAMES[s.kind], "side": 1, "stop": s.stop,
                 "target": s.target, "price": bar.c, "note": s.note, "traded": False}
            self.signals = (self.signals + [d])[-200:]
            self.app.on_signal(self, d)
            self.pending.append((s, d))

    def _on_pb_bar(self, bar: Bar, seeded=False):
        if seeded:
            return
        for s in self.pb.on_bar(bar):
            d = {"t": bar.t, "tf": "1m", "kind": s.kind, "name": ALL_NAMES[s.kind], "side": s.side, "stop": s.stop,
                 "target": s.target, "price": s.entry, "note": f"关键位：{s.level}，限价 {s.entry:.6g}", "traded": False}
            if self.backfilling:
                d["skip"] = "历史信号"
            self.signals = (self.signals + [d])[-200:]
            self.app.on_signal(self, d)
            if not self.backfilling:
                self.limit_orders.append((s, d, bar.t + 60_000 + PB_FILL_MS))

    def _check_limits(self, price, ts):
        keep = []
        for s, d, until in self.limit_orders:
            if ts > until:
                d["skip"] = "限价没成交，已撤"
                continue
            if (s.side == 1 and price <= s.entry) or (s.side == -1 and price >= s.entry):
                self.app.try_open(self, s, d, s.entry, ts)   # 挂着的限价单按挂单价成交
                if not d.get("traded") and not d.get("skip"):
                    d["skip"] = "没开成"
                continue
            keep.append((s, d, until))
        self.limit_orders = keep

    # ------------------------------------------------------------ 当前K线（所有周期）
    def _curs(self):
        return [b.cur for b in self.builders.values() if b.cur is not None]

    def _flush_big(self):
        a = self._agg
        self._agg = None
        if not a:
            return
        self._sizes.append(a["usd"])
        self._n_agg += 1
        if len(self._sizes) > 3000:
            del self._sizes[:-3000]
        if self._n_agg % 300 == 0 and len(self._sizes) >= 600:      # 每 300 笔重算一次（不能用列表长度判断：满 3000 后会每笔都算）
            # 大单标准跟着这个币走：最近成交里金额最大的 0.5%，至少 1 万美元
            self.big_usd = max(10_000.0, sorted(self._sizes)[int(len(self._sizes) * 0.995)])
        if a["usd"] < self.big_usd:
            return
        side = 1 if a["buy"] else -1
        for b in self._curs():
            if a["buy"]:
                b.big_buy += a["q"]
            else:
                b.big_sell += a["q"]
            b.bigs.append((a["px"], a["q"], side))
        self.ext["bigs"] = (self.ext["bigs"] + [(a["px"], a["q"], side, a["ts"])])[-50:]

    def on_trade(self, price, size, is_buy, ts):
        self.last = price
        qty = size * self.ct
        a = self._agg                 # 合并大单：同一毫秒、同方向的成交算一笔
        if a and a["ts"] == ts and a["buy"] == is_buy:
            a["q"] += qty; a["usd"] += qty * price; a["px"] = price
        else:
            self._flush_big()
            self._agg = {"ts": ts, "buy": is_buy, "q": qty, "usd": qty * price, "px": price}
        for b in self.builders.values():
            b.add(price, qty, is_buy, ts)
        b5 = self.builders["5m"].bars
        if b5 and b5[-1].t != self._last5:
            self._last5 = b5[-1].t
            if not self.backfilling:
                self._check_flush(b5)
        mrow = self.okx_min.setdefault(ts // 60000, [0.0, 0.0])
        mrow[0 if is_buy else 1] += qty * price
        if len(self.okx_min) > 400:
            for k in sorted(self.okx_min)[:-300]:
                self.okx_min.pop(k, None)
        for c in self._curs():
            if math.isnan(c.oi):        # 新K线：先带上最新的持仓、费率、多空比
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
        for b in self._curs():
            if side == -1:
                b.liq_long += qty
            else:
                b.liq_short += qty
            b.liqs.append((price, qty, side))

    def on_liq_usd(self, price, usd, side, ts, venue):
        """币安、Bybit 的爆仓（金额是美元）"""
        if price <= 0:
            return
        qty = usd / price
        item = {"px": price, "q": qty, "usd": usd, "side": side, "ts": ts, "venue": venue}
        self.ext["liqs"] = (self.ext["liqs"] + [item])[-150:]
        for b in self._curs():
            if side == -1:
                b.liq_long += qty
            else:
                b.liq_short += qty
            b.liqs.append((price, qty, side))

    def xstats(self):
        """全网数据（币安合约/现货、Bybit、Coinbase、大背景）"""
        x = getattr(self.app, "xx", None)
        return x.stats(self.inst, self.okx_min) if x is not None else {}

    def on_oi(self, oi_coin, oi_usd, ts):
        self.ext["oi"], self.ext["oi_usd"] = oi_coin, oi_usd
        h = self.ext["oi_hist"]
        if not h or ts > h[-1][0]:
            h.append((ts, oi_usd))       # 记美元持仓价值：回测用的币安数据就是持仓价值（价格跌它也跟着降），口径要一致
        self.ext["oi_hist"] = h[-2000:]
        for b in self._curs():
            b.oi = oi_coin

    def on_funding(self, rate, next_ts):
        self.ext["funding"], self.ext["next_funding"] = rate, next_ts
        for b in self._curs():
            b.funding = rate

    def on_ratio(self, ls, top_ls):
        self.ext["ls"], self.ext["top_ls"] = ls, top_ls
        for b in self._curs():
            b.ls = ls

    def sample_book(self, ts):
        """每 2 秒采样一次盘口：热力图（每个周期按自己的格子）、盘口失衡、大单墙"""
        if math.isnan(self.last):
            return
        bids, asks = self.app.hub.book(self.inst).levels()
        if not bids or not asks:
            return
        px = self.last
        for t, bld in self.builders.items():
            b = bld.cur
            if b is None:
                continue
            row = bld.row
            hb, ha = {}, {}
            for p_, q in bids.items():
                r = int(math.floor(p_ / row)); hb[r] = hb.get(r, 0.0) + q * self.ct
            for p_, q in asks.items():
                r = int(math.floor(p_ / row)); ha[r] = ha.get(r, 0.0) + q * self.ct
            r0 = int(math.floor(px / row))
            for r in range(r0 - 80, r0 + 81):
                if r in hb or r in ha:
                    cell = b.heat.setdefault(r, [0.0, 0.0, 0])
                    cell[0] += hb.get(r, 0.0); cell[1] += ha.get(r, 0.0); cell[2] += 1
            if t == self.tf:
                self._walls(b, hb, ha, r0, row)
        lo, hi = px * 0.997, px * 1.003             # 盘口失衡：价格上下 0.3% 以内
        sb = sum(q for p_, q in bids.items() if p_ >= lo)
        sa = sum(q for p_, q in asks.items() if p_ <= hi)
        obi = (sb - sa) / (sb + sa) if sb + sa > 0 else 0.0
        self.ext["obi"] = obi
        for b in self._curs():
            n = getattr(b, "_obi_n", 0)
            b.obi = obi if n == 0 or math.isnan(b.obi) else (b.obi * n + obi) / (n + 1)
            b._obi_n = n + 1

    def _walls(self, b, hb, ha, r0, row):
        """大单墙：附近几格里挂单 ≥ 平均每格 5 倍，并且已经挂了 30 秒以上（防假挂单）"""
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

    # ------------------------------------------------------------ 给网页
    def view(self, tf=None, n=40):
        tf = tf if tf in self.builders else self.tf
        bld = self.builders[tf]
        bars = bld.bars[-(n - 1):] + ([bld.cur] if bld.cur else [])
        out = []
        for b in bars:
            if b is None:
                continue
            seeded = bool(getattr(b, "seeded", False))
            bi, si = imbalances(b) if not seeded else (set(), set())
            out.append({"t": b.t, "o": b.o, "h": b.h, "l": b.l, "c": b.c, "seeded": seeded,
                        "rows": [[r, round(x, 4), round(y, 4)] for r, (x, y) in sorted(b.rows.items())] if not seeded else [],
                        "buy_imb": sorted(bi), "sell_imb": sorted(si),
                        "delta": b.delta, "vol": b.vol, "poc": b.poc_row(),
                        "heat": [[r, round(x / c, 4), round(y / c, 4)] for r, (x, y, c) in b.heat.items() if c],
                        "liq_long": b.liq_long, "liq_short": b.liq_short, "liqs": b.liqs[-60:],
                        "big_buy": b.big_buy, "big_sell": b.big_sell, "bigs": b.bigs[-60:],
                        "oi": b.oi, "obi": b.obi, "wall_bid": b.wall_bid, "wall_ask": b.wall_ask})
        day0 = (int(time.time() * 1000) // 86_400_000) * 86_400_000
        real = [b for b in bld.bars if not getattr(b, "seeded", False) and b.t >= day0]
        vp = volume_profile(real + ([bld.cur] if bld.cur else []), bld.row)
        bids, asks = self.app.hub.book(self.inst).top(120)
        return {"inst": self.inst, "tf": tf, "signal_tf": self.tf, "row": bld.row, "last": self.last, "bars": out,
                "profile": None if not vp else {"poc": vp["poc"], "vah": vp["vah"], "val": vp["val"],
                                                "rows": sorted(vp["profile"].items())},
                "prev_day": self.det.prev_day_profile and {k: self.det.prev_day_profile[k] for k in ("poc", "vah", "val")},
                "dom": {"bids": [[p, s * self.ct] for p, s in bids], "asks": [[p, s * self.ct] for p, s in asks]},
                "zones": [z for z in self.det.zones if not z["used"]] if tf == self.tf else [],
                "signals": [s for s in self.signals[-60:] if s.get("tf") == tf][-30:],
                "ext": {k: v for k, v in self.ext.items() if k != "oi_hist"},
                "levels": [] if math.isnan(self.last) else [[x, n] for x, n in self.pb.levels(self.last)],
                "oi_1h": self._oi_change(3600_000), "big_usd": self.big_usd, "x": self.xstats(),
                "flush": dict(zip(("r60", "oi", "sf"), self.flush_state())), "flush_cfg": flush_cfg(self.app.cfg), "squeeze_cfg": squeeze_cfg(self.app.cfg)}

    def summary(self):
        """扫描表一行"""
        b1 = self.builders["1m"]
        hist = [b for b in b1.bars[-60:] if not getattr(b, "seeded", False)] + ([b1.cur] if b1.cur else [])
        chg = None
        if b1.bars and not math.isnan(self.last):
            ref = b1.bars[-60].c if len(b1.bars) >= 60 else b1.bars[0].c
            chg = self.last / ref - 1 if ref else None
        now = int(time.time() * 1000)
        liq5 = sum(l["usd"] * (-1 if l["side"] < 0 else 1) for l in self.ext["liqs"] if now - l["ts"] <= 300_000)
        liq5_tot = sum(l["usd"] for l in self.ext["liqs"] if now - l["ts"] <= 300_000)
        sig = self.signals[-1] if self.signals else None
        x = self.xstats()
        return {"all_pf_60": x.get("all_pf_60"), "div_60": x.get("div_60"), "trend": (x.get("ctx") or {}).get("trend"),
                "inst": self.inst, "last": self.last, "chg_1h": chg, "funding": self.ext["funding"],
                "oi_1h": self._oi_change(3600_000), "liq_5m": liq5_tot, "liq_net_5m": liq5, "obi": self.ext["obi"],
                "delta_15m": sum(b.delta for b in hist[-15:]) * (self.last if not math.isnan(self.last) else 0),
                "sig": None if not sig else {"name": sig["name"], "side": sig["side"], "t": sig["t"], "tf": sig.get("tf")}}

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
        self.live: OkxLive | None = None
        self.live_confirmed = False
        self.engines: dict[str, SymbolEngine] = {}
        self.hub = None               # 行情（of_feed.OkxHub），由 of_app 设置
        self.xhub = None
        self.external: set = set()    # 实盘账户里别的程序 / 手动开的仓位（这些币不再开新单）
        self.live_equity = 0.0
        self.live_avail = 0.0
        self.live_day, self.live_day_start = "", 0.0
        self._acct_ts = 0.0
        self.opening: set = set()     # 实盘：正在下单的币
        self.recent: list = []        # 所有币最近的信号（扫描表用）
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

    save_cfg_cb = None          # 网页那边设置的保存配置函数（自动刹车改了打法勾选后要存下来）

    def say(self, msg):
        line = time.strftime("%H:%M:%S ") + msg
        self.log.append(line)
        self.log = self.log[-200:]
        print(line, flush=True)
        try:                                   # 同时存到 of_log.txt（出问题时把这个文件发过来）
            if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 5_000_000:
                os.replace(LOG_FILE, LOG_FILE + ".old")
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d ") + line + "\n")
        except OSError:
            pass

    @property
    def broker(self):
        return "live" if (self.cfg["mode"] == "live" and self.live_confirmed and self.live is not None) else "paper"

    def enable_live(self, phrase: str):
        if not self.allow_live:
            return False, "没有开启实盘权限：需要在 .env 里加 OF_ALLOW_LIVE=1 并重启"
        if not self.keys or not all(self.keys.values()):
            return False, ".env 里没有填欧易 API 密钥"
        if phrase.strip() != "我确认实盘":
            return False, "确认口令不对"
        try:
            self.live = OkxLive(self.keys, self.proxy)
        except Exception as e:  # noqa: BLE001
            return False, f"连接欧易失败：{e}"
        self.live_confirmed = True
        self.cfg["mode"] = "live"
        self.refresh_live(force=True)
        mode = "双向持仓（开多开空分开）" if self.live.hedged else "单向持仓"
        msg = (f"已切换到实盘：账户权益 {self.live_equity:.2f}U，可用 {self.live_avail:.2f}U，{mode}；"
               f"账户里已有仓位的币：{', '.join(x.split('-')[0] for x in sorted(self.external)) or '无'}（这些币不再开新单）")
        self.say(msg)
        of_notify.push("订单流：已切换到实盘", msg)
        return True, msg

    def disable_live(self):
        self.live_confirmed = False
        self.cfg["mode"] = "paper"
        self.say("已切回模拟盘（实盘已有的持仓不受影响，止盈止损在交易所照常有效）")

    # ---------------------------------------------------------- 实盘：账户核对（后台每 5 秒）
    def refresh_live(self, force=False):
        """（同步）读账户权益、可用保证金、别的仓位。切换实盘时用"""
        if not (self.live_confirmed and self.live):
            return
        try:
            self.live_equity, self.live_avail = self.live.account()
            self._acct_ts = time.time()
            d = time.strftime("%Y-%m-%d", time.gmtime())
            if self.live_day != d or not self.live_day_start:
                self.live_day, self.live_day_start = d, self.live_equity
            mine = {p.sym for p in self.acct.positions if p.live}
            self.external = {i for i in self.live.positions() if i not in mine}
        except Exception as e:  # noqa: BLE001
            self.say(f"读取实盘账户失败：{e}")

    async def reconcile_live(self):
        """（后台每 5 秒）核对实盘：交易所已经平掉的单（止盈/止损触发）读真实盈亏记账；每 30 秒读一次权益"""
        if not (self.live_confirmed and self.live):
            return
        now = time.time()
        try:
            if now - self._acct_ts >= 30:
                self.live_equity, self.live_avail = await asyncio.to_thread(self.live.account)
                self._acct_ts = now
                d = time.strftime("%Y-%m-%d", time.gmtime())
                if self.live_day != d or not self.live_day_start:
                    self.live_day, self.live_day_start = d, self.live_equity
            ex_pos = await asyncio.to_thread(self.live.positions)
            mine = {p.sym for p in self.acct.positions if p.live}
            self.external = {i for i in ex_pos if i not in mine and i not in self.opening}
            for pos in list(self.acct.positions):
                if not pos.live or pos.busy or now * 1000 - pos.t_open < 15_000 or pos.sym in ex_pos:
                    continue
                # 交易所那边已经没有这个仓位：止盈或止损触发了（或你在 App 里手动平了）
                r = await asyncio.to_thread(self.live.closed_pnl, pos.sym, pos.t_open)
                if r is None:
                    if not pos.checked:
                        pos.checked = now
                    if now - pos.checked < 60:            # 历史持仓还没出来，等一下再记
                        continue
                exit_px = (r or {}).get("exit") or pos.stop
                why = "止盈" if (exit_px - pos.entry) * pos.side > 0 else "止损"
                self._record_close(pos, exit_px, why, int(now * 1000), real=r)
        except Exception as e:  # noqa: BLE001
            self.say(f"核对实盘账户失败：{e}")

    def on_signal(self, eng, d):
        self.recent = (self.recent + [{**d, "inst": eng.inst}])[-100:]
        if d.get("kind") in self.cfg.get("enabled", []) and not d.get("skip"):
            self.say(f"信号：{eng.inst.split('-')[0]} {d['name']} {'做多' if d['side'] > 0 else '做空'} "
                     f"价格 {d['price']:.6g}（{d.get('note', '')}）" + ("" if self.cfg.get("auto") else "——自动交易没开，不下单"))

    # ---------------------------------------------------------- 交易
    def _roll_day(self):
        d = time.strftime("%Y-%m-%d", time.gmtime())
        if d != self.acct.day:
            self.acct.day, self.acct.day_pnl, self.acct.start_equity = d, 0.0, self.acct.equity

    def _day_loss_hit(self):
        lim = self.cfg["daily_loss_pct"] / 100
        if self.broker == "live":
            return self.live_day_start > 0 and (self.live_equity - self.live_day_start) <= -lim * self.live_day_start
        return self.acct.day_pnl <= -lim * self.acct.start_equity

    def try_open(self, eng: SymbolEngine, s, d, price, ts):
        self._try_open(eng, s, d, price, ts)
        why = d.get("skip")
        if why and why != "正在下单…" and self.cfg.get("auto") and s.kind in self.cfg.get("enabled", []):
            self.say(f"没开：{eng.inst.split('-')[0]} {ALL_NAMES.get(s.kind, s.kind)}——{why}")

    def _try_open(self, eng: SymbolEngine, s, d, price, ts):
        """出信号后的下一笔成交时调用。检查都过了：模拟盘直接成交；实盘丢到后台线程去下单，不卡行情"""
        self._roll_day()
        if not self.cfg["auto"] or s.kind not in self.cfg["enabled"]:
            return
        if (s.side == 1 and not (s.stop < price < s.target)) or (s.side == -1 and not (s.target < price < s.stop)):
            d["skip"] = "价格已越过止损或止盈"
            return
        if s.kind in FLUSH_NAMES and getattr(eng, "category", "1") != "1":
            d["skip"] = "这是股票等非加密币合约，回测只测过加密币，不做"
            return
        if self._day_loss_hit():
            d["skip"] = "今天亏损到上限，停止开新单"
            return
        if len(self.acct.positions) + len(self.opening) >= self.cfg["max_positions"]:
            d["skip"] = "持仓数已满"
            return
        if any(p.sym == eng.inst for p in self.acct.positions) or eng.inst in self.opening:
            d["skip"] = "这个币已有持仓"
            return
        live = self.broker == "live"
        if live and eng.inst in self.external:
            d["skip"] = "账户里这个币已经有别的仓位"
            return
        if live and not self.live_equity:
            d["skip"] = "还没读到实盘账户权益"
            return
        qty = self.risk.size(self.acct, price, s.stop, self.live_equity if live else None)
        if s.kind in FLUSH_NAMES:              # 清洗接盘 / 轧空追多：固定用权益的 size_pct% 开仓
            fc = flush_cfg(self.cfg) if s.kind == "flush_spot" else squeeze_cfg(self.cfg)
            eq = self.live_equity if live else self.acct.equity
            qty = min(eq * fc["size_pct"] / 100 / price, eq * self.cfg["max_leverage"] / price)
        if s.kind in PB_NAMES:
            hold = PB_HOLD_MS
        elif s.kind == "flush_spot":
            hold = flush_cfg(self.cfg)["hold_h"] * 3600_000
        elif s.kind == "squeeze_long":
            hold = squeeze_cfg(self.cfg)["hold_h"] * 3600_000
        else:
            hold = self.cfg.get("max_hold_bars", 48) * TF_MS[eng.tf]
        if not live:
            fill = price * (1 + s.side * PaperBroker.slip)
            self._add_position(eng, s, d, Position(eng.inst, s.kind, s.side, qty, fill, s.stop, s.target, ts, ts + hold,
                                                   "paper", False, risk=abs(fill - s.stop)))
            return
        self.opening.add(eng.inst)
        d["skip"] = "正在下单…"
        asyncio.get_event_loop().create_task(self._open_live(eng, s, d, qty, price, ts, hold))

    async def _open_live(self, eng, s, d, qty, price, ts, hold):
        try:
            lev = int(self.cfg["max_leverage"])
            n, nmin, cs = self.live.contracts_for(eng.inst, qty)
            if n < nmin or n <= 0:
                d["skip"] = f"仓位太小（{n:g} 张 < 最少 {nmin:g} 张），资金不够开这个币"
                return
            mgn = "cross" if self.cfg.get("margin_mode") == "cross" else "isolated"
            margin = n * cs * price / lev
            if margin > self.live_avail * 0.95:
                d["skip"] = f"可用保证金不够（要 {margin:.2f}U，可用 {self.live_avail:.2f}U）"
                return
            await asyncio.to_thread(self.live.prepare, eng.inst, lev, mgn)
            r = await asyncio.to_thread(self.live.open, eng.inst, s.side, n, s.stop, s.target, mgn)
            fill = r["fill"] or price
            pos = Position(eng.inst, s.kind, s.side, r["contracts"] * cs, fill, s.stop, s.target, ts, ts + hold,
                           r["order_id"], True, contracts=r["contracts"], algo_id=r["algo_id"], risk=abs(fill - s.stop), mgn=mgn)
            d.pop("skip", None)
            self._add_position(eng, s, d, pos)
            if not r["algo_id"]:
                msg = f"{eng.inst} 开仓了，但没查到止盈止损单！请马上到欧易 App 检查这笔仓位"
                self.say(msg)
                of_notify.push("订单流：止盈止损可能没挂上", msg)
            self.live_avail -= margin
        except Exception as e:  # noqa: BLE001
            d["skip"] = f"下单失败：{e}"
            self.say(f"{eng.inst} 实盘下单失败：{e}")
            of_notify.push("订单流：下单失败", f"{eng.inst} {e}")
        finally:
            self.opening.discard(eng.inst)

    def _add_position(self, eng, s, d, pos):
        if s.kind in PB_NAMES:
            eng.pb.trades_today += 1
        self.acct.positions.append(pos)
        d["traded"] = True
        tag = "实盘" if pos.live else "模拟盘"
        msg = (f"[{tag}] 开{'多' if s.side == 1 else '空'} {eng.inst} {ALL_NAMES[s.kind]} 价 {pos.entry:.6g} "
               f"数量 {pos.qty:.6g} 止损 {s.stop:.6g} 止盈 {s.target:.6g}")
        self.say(msg)
        of_notify.push(f"订单流：开{'多' if s.side == 1 else '空'} {eng.inst.split('-')[0]}", msg)
        self.save()

    def check_exits(self, eng: SymbolEngine, price, ts):
        """每笔成交都会调用。模拟盘：止盈止损、到时间、实战打法 1R 平一半都在这里判断。
        实盘：止盈止损在交易所执行（后台核对时记账），这里只处理到时间平仓和 1R 平一半"""
        for pos in list(self.acct.positions):
            if pos.sym != eng.inst or pos.busy:
                continue
            # 实战打法：到 1R 先平一半，剩下的止损移到保本
            if pos.kind in PB_NAMES and not pos.half_done and pos.risk > 0:
                one_r = pos.entry + pos.side * pos.risk
                if (price >= one_r) if pos.side == 1 else (price <= one_r):
                    if pos.live:
                        pos.busy = True
                        asyncio.get_event_loop().create_task(self._half_live(pos, price, ts))
                    else:
                        self._half_paper(pos, price * (1 - pos.side * PaperBroker.slip))
                    continue
            if pos.live:
                if ts >= pos.max_until and not (self.live_confirmed and self.live):
                    # 重启后实盘还没重新打开：不在本地假装平仓，提醒你（每 10 分钟一次）
                    if ts - getattr(pos, "_warned", 0) >= 600_000:
                        pos._warned = ts
                        msg = (f"{pos.sym} 这笔实盘单已经到时间该平了，但实盘还没打开。"
                               f"请在网页右边切到实盘（程序会马上去平），或者自己在欧易 App 里平掉")
                        self.say(msg)
                        of_notify.push("订单流：有实盘单该平仓了", msg)
                    continue
                if ts >= pos.max_until:
                    pos.busy = True
                    asyncio.get_event_loop().create_task(self._close_live(pos, "到时间", ts))
                continue
            why = self.paper.check_exit(pos, price)
            if why is None and ts >= pos.max_until:
                why = "到时间"
            if why is None:
                continue
            exit_px = {"止损": pos.stop, "止盈": pos.target}.get(why, price)
            exit_px = exit_px * (1 - pos.side * PaperBroker.slip) if why != "止盈" else exit_px
            self._record_close(pos, exit_px, why, ts)

    def _half_paper(self, pos, px):
        half = pos.qty / 2
        pnl = pos.side * (px - pos.entry) * half - PaperBroker.fee * (pos.entry + px) * half
        self.acct.equity += pnl
        self.acct.day_pnl += pnl
        pos.qty -= half
        pos.half_done = True
        pos.stop = pos.entry
        self.say(f"[模拟盘] {pos.sym} 到 1R 平一半 盈亏 {pnl:+.2f}U，剩下的止损移到保本 {pos.entry:.6g}")
        self.save()

    async def _half_live(self, pos, price, ts):
        try:
            cs = pos.qty / pos.contracts if pos.contracts else 1
            n_half, nmin, _ = self.live.contracts_for(pos.sym, pos.qty / 2)
            if n_half < nmin or n_half >= pos.contracts:       # 太小分不了：只把止损移到保本
                await asyncio.to_thread(self.live.cancel_algo, pos.sym, pos.algo_id)
                pos.algo_id = await asyncio.to_thread(self.live.place_oco, pos.sym, pos.side, pos.contracts, pos.entry, pos.target, pos.mgn)
            else:
                await asyncio.to_thread(self.live.cancel_algo, pos.sym, pos.algo_id)
                px = await asyncio.to_thread(self.live.close, pos.sym, pos.side, n_half, "", pos.mgn)
                pos.contracts -= n_half
                pos.qty = pos.contracts * cs
                pos.algo_id = await asyncio.to_thread(self.live.place_oco, pos.sym, pos.side, pos.contracts, pos.entry, pos.target, pos.mgn)
                self.say(f"[实盘] {pos.sym} 到 1R 平一半（{n_half:g} 张，均价 {px:.6g}），剩下的止损移到保本")
            pos.stop = pos.entry
            pos.half_done = True
            self.save()
        except Exception as e:  # noqa: BLE001
            self.say(f"{pos.sym} 平一半/移止损失败：{e}（原来的止盈止损还在）")
            of_notify.push("订单流：移止损失败", f"{pos.sym} {e}")
            pos.half_done = True
        finally:
            pos.busy = False

    async def _close_live(self, pos, why, ts):
        try:
            ex_pos = await asyncio.to_thread(self.live.positions)
            if pos.sym in ex_pos:
                await asyncio.to_thread(self.live.close, pos.sym, pos.side, pos.contracts, "", pos.mgn)
            await asyncio.to_thread(self.live.cancel_algo, pos.sym, pos.algo_id)
            await asyncio.sleep(1.5)
            r = await asyncio.to_thread(self.live.closed_pnl, pos.sym, pos.t_open)
            self._record_close(pos, (r or {}).get("exit") or self.engines[pos.sym].last, why, ts, real=r)
        except Exception as e:  # noqa: BLE001
            self.say(f"{pos.sym} 实盘平仓失败：{e}，30 秒后再试")
            of_notify.push("订单流：平仓失败", f"{pos.sym} {e}")
            pos.max_until = ts + 30_000
        finally:
            pos.busy = False

    def _record_close(self, pos, exit_px, why, ts, real=None):
        if real:                       # 实盘：用欧易历史持仓里的真实盈亏（含手续费、资金费）
            pnl = real["pnl"]
        else:
            fee = PaperBroker.fee * (pos.entry + exit_px) * pos.qty
            pnl = pos.side * (exit_px - pos.entry) * pos.qty - fee
        if pos in self.acct.positions:
            self.acct.positions.remove(pos)
        if not pos.live:
            self.acct.equity += pnl
        self.acct.day_pnl += pnl
        self.acct.closed += 1
        self.acct.wins += pnl > 0
        rec = {"sym": pos.sym, "kind": ALL_NAMES[pos.kind], "side": pos.side, "entry": pos.entry,
               "exit": exit_px, "pnl": round(pnl, 4), "why": why, "t_open": pos.t_open, "t_close": ts,
               "live": pos.live, "real": bool(real), "k": pos.kind,
               "ret": round(pnl / (pos.entry * pos.qty), 6) if pos.entry and pos.qty else 0.0}
        self.history.append(rec)
        with open(TRADE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        eng = self.engines.get(pos.sym)
        if pos.kind in PB_NAMES and eng is not None:
            eng.pb.record_result(pnl > 0)
        tag = "实盘" if pos.live else "模拟盘"
        msg = f"[{tag}] 平仓 {pos.sym} {why} 盈亏 {pnl:+.2f}U" + ("（欧易真实盈亏）" if real else "")
        self.say(msg)
        of_notify.push(f"订单流：{why} {pos.sym.split('-')[0]} {pnl:+.2f}U", msg)
        self._guard(pos.kind)
        self.save()

    def _guard(self, kind):
        """自动刹车：这个打法最近 guard_n 笔的盈亏比（PF）低于 guard_pf，就自动取消勾选，提醒你"""
        n, lim = int(self.cfg.get("guard_n", 20)), float(self.cfg.get("guard_pf", 0.8))
        rs = [h.get("ret", 0.0) for h in self.history if h.get("k") == kind][-n:]
        if n <= 0 or len(rs) < n or kind not in self.cfg.get("enabled", []):
            return
        win, loss = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
        pf = win / loss if loss > 0 else float("inf")
        if pf < lim:
            self.cfg["enabled"] = [k for k in self.cfg["enabled"] if k != kind]
            if self.save_cfg_cb:
                self.save_cfg_cb()
            msg = (f"自动刹车：{ALL_NAMES.get(kind, kind)} 最近 {n} 笔盈亏比只有 {pf:.2f}（低于 {lim}），已自动暂停。"
                   f"行情可能变了；想继续用，在网页上重新勾选")
            self.say(msg)
            of_notify.push("订单流：打法已自动暂停", msg)

    def close_all(self):
        for pos in list(self.acct.positions):
            pos.max_until = 0
            eng = self.engines.get(pos.sym)
            if eng and not math.isnan(eng.last):
                self.check_exits(eng, eng.last, int(time.time() * 1000))

    def heartbeat(self):
        """每 5 分钟在黑窗口打一行运行状态"""
        live = self.broker == "live"
        eq = self.live_equity if live else self.acct.equity
        day = (self.live_equity - self.live_day_start) if (live and self.live_day_start) else self.acct.day_pnl
        xs = getattr(getattr(self, "xx", None), "status", {}) or {}
        nm = {"binance": "币安", "bybit": "Bybit", "bn_liq": "币安爆仓", "coinbase": "Coinbase"}
        src = " ".join(f"{nm.get(k, k)}{'✓' if str(v).startswith('正常') else '✗'}" for k, v in xs.items())
        pos = "、".join(f"{p.sym.split('-')[0]}({ALL_NAMES.get(p.kind, p.kind)})" for p in self.acct.positions) or "无"
        self.say(f"运行中｜{len(self.engines)} 个币｜{getattr(self.hub, 'status', '')}｜{'实盘' if live else '模拟'} 权益 {eq:.2f}"
                 f"｜今日 {day:+.2f}｜持仓 {len(self.acct.positions)} 单：{pos}｜自动交易{'开' if self.cfg.get('auto') else '关'}｜{src}")

    def state(self, inst, tf=None):
        eng = self.engines.get(inst)
        return {"view": eng.view(tf) if eng else None,
                "symbols": list(self.engines),
                "scan": [e.summary() for e in self.engines.values()],
                "recent": self.recent[-40:],
                "feed_status": getattr(self.hub, "status", ""), "extras_status": getattr(self.xhub, "status", ""), "x_status": getattr(getattr(self, "xx", None), "status", {}),
                "external": sorted(self.external), "live_equity": self.live_equity, "live_avail": self.live_avail,
                "live_day_pnl": (self.live_equity - self.live_day_start) if self.live_day_start else 0.0,
                "hedged": bool(self.live and self.live.hedged), "notify": of_notify.enabled(),
                "cfg": self.cfg, "live_ok": self.live_confirmed, "allow_live": self.allow_live,
                "signal_names": ALL_NAMES, "live_only": sorted(LIVE_ONLY), "pb_kinds": sorted(PB_NAMES),
                "acct": {**asdict(self.acct), "positions": [asdict(p) for p in self.acct.positions]},
                "last": {k: e.last for k, e in self.engines.items()},
                "history": self.history[-50:], "log": self.log[-60:]}
