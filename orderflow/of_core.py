"""订单流核心：足迹K线、成交量分布、8种订单流形态识别。
回测和实盘用同一份代码，信号只用已经收盘的K线，不偷看未来。

术语：
  bid = 主动卖出的量（砸在买单上）  ask = 主动买入的量（吃掉卖单）
  delta = ask - bid                   行（row）= 足迹图上的一个价位格子
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# ---------------------------------------------------------------- 参数
DEFAULT_PARAMS = {
    "imb_ratio": 3.0,        # 斜向失衡倍数：一边 ≥ 另一边斜对角的 3 倍（行业默认 300%）
    "imb_min_frac": 0.5,     # 失衡格子的量至少是本根K线平均每格量的 0.5 倍，太小的不算
    "stack_rows": 3,         # 连续 3 格同向失衡 = 堆叠失衡
    "zone_life": 24,         # 堆叠失衡区有效多少根K线
    "vol_avg_n": 20,         # 平均成交量用多少根K线
    "absorb_vol_mult": 2.0,  # 吸收：成交量 ≥ 平均的 2 倍
    "absorb_delta_frac": 0.25,  # 吸收：delta 占成交量 ≥ 25%（大量主动卖/买）
    "extreme_n": 20,         # 新高/新低看过去多少根
    "exhaust_frac": 0.15,    # 衰竭：最高(低)那一格的量 ≤ 本根平均每格量的 15%
    "trap_delta_frac": 0.3,  # 被套：前一根 delta 占量 ≥ 30%
    "trap_vol_mult": 1.5,
    "rr": 2.0,               # 止盈 = 2 倍止损距离
    "max_hold": 48,          # 最多拿多少根K线
    "min_stop_rows": 2,      # 止损至少离开几格
}


@dataclass
class Bar:
    t: int                    # 开始时间（毫秒）
    o: float = math.nan
    h: float = -math.inf
    l: float = math.inf
    c: float = math.nan
    rows: dict = field(default_factory=dict)   # 行号 -> [bid, ask]
    closed: bool = False

    @property
    def vol(self) -> float:
        return sum(b + a for b, a in self.rows.values())

    @property
    def delta(self) -> float:
        return sum(a - b for b, a in self.rows.values())

    def add(self, price: float, qty: float, is_buy: bool, row_size: float):
        if math.isnan(self.o):
            self.o = price
        self.h = max(self.h, price)
        self.l = min(self.l, price)
        self.c = price
        r = int(math.floor(price / row_size + 1e-9))
        cell = self.rows.setdefault(r, [0.0, 0.0])
        cell[1 if is_buy else 0] += qty

    def poc_row(self):
        if not self.rows:
            return None
        return max(self.rows.items(), key=lambda kv: kv[1][0] + kv[1][1])[0]


# ---------------------------------------------------------------- 足迹细节
def imbalances(bar: Bar, p=DEFAULT_PARAMS):
    """斜向失衡：某价位的主动买 vs 下一格的主动卖；某价位的主动卖 vs 上一格的主动买。
    返回 (买方失衡行集合, 卖方失衡行集合)。"""
    if not bar.rows:
        return set(), set()
    vals = [b + a for b, a in bar.rows.values()]
    avg = sum(vals) / max(1, len(vals))
    floor = p["imb_min_frac"] * avg
    buy, sell = set(), set()
    for r, (b, a) in bar.rows.items():
        below_bid = bar.rows.get(r - 1, [0.0, 0.0])[0]
        above_ask = bar.rows.get(r + 1, [0.0, 0.0])[1]
        if a >= floor and a >= p["imb_ratio"] * max(below_bid, 1e-12):
            buy.add(r)
        if b >= floor and b >= p["imb_ratio"] * max(above_ask, 1e-12):
            sell.add(r)
    return buy, sell


def stacked(rows: set, n: int):
    """找连续 n 行以上的失衡，返回 [(最低行, 最高行), ...]"""
    out, run = [], []
    for r in sorted(rows):
        if run and r == run[-1] + 1:
            run.append(r)
        else:
            if len(run) >= n:
                out.append((run[0], run[-1]))
            run = [r]
    if len(run) >= n:
        out.append((run[0], run[-1]))
    return out


def volume_profile(bars: list[Bar], row_size: float, va_frac=0.70):
    """成交量分布：POC（成交最多的价）、VAH/VAL（包含 70% 成交量的区间上下沿）"""
    prof: dict[int, float] = {}
    for b in bars:
        for r, (x, y) in b.rows.items():
            prof[r] = prof.get(r, 0.0) + x + y
    if not prof:
        return None
    rows = sorted(prof)
    poc = max(rows, key=lambda r: prof[r])
    total = sum(prof.values())
    lo = hi = poc
    acc = prof[poc]
    while acc < va_frac * total and (lo > rows[0] or hi < rows[-1]):
        up = prof.get(hi + 1, 0.0) if hi < rows[-1] else -1
        dn = prof.get(lo - 1, 0.0) if lo > rows[0] else -1
        if up >= dn:
            hi += 1
            acc += max(up, 0)
        else:
            lo -= 1
            acc += max(dn, 0)
    return {"poc": (poc + 0.5) * row_size, "vah": (hi + 1) * row_size, "val": lo * row_size,
            "profile": {r: v for r, v in prof.items()}}


# ---------------------------------------------------------------- 形态识别
SIGNAL_NAMES = {
    "stacked": "堆叠失衡回踩",
    "absorption": "吸收",
    "exhaustion": "衰竭",
    "divergence": "Delta背离",
    "trapped": "被套反向",
    "va_reentry": "价值区回归",
}


@dataclass
class Signal:
    kind: str
    side: int          # 1 做多, -1 做空
    stop: float
    target: float
    bar_t: int
    note: str = ""


class Detector:
    """喂入收盘K线，吐出信号。内部保存堆叠失衡区、前一日成交量分布。"""

    def __init__(self, row_size: float, params: dict | None = None, enabled=None):
        self.p = dict(DEFAULT_PARAMS, **(params or {}))
        self.row = row_size
        self.bars: list[Bar] = []
        self.zones: list[dict] = []        # 有效的堆叠失衡区
        self.enabled = set(enabled or SIGNAL_NAMES)
        self.prev_day_profile = None
        self._day = None
        self._day_bars: list[Bar] = []
        self.n = 0                         # 已处理K线总数（bars 列表会截断，不能拿它的长度算年龄）

    def _mk(self, kind, side, entry, stop, bar, note=""):
        risk = abs(entry - stop)
        risk = max(risk, self.p["min_stop_rows"] * self.row)
        stop = entry - side * risk
        return Signal(kind, side, stop, entry + side * self.p["rr"] * risk, bar.t, note)

    def on_bar(self, bar: Bar) -> list[Signal]:
        p, sigs = self.p, []
        # 换日：前一日成交量分布
        day = bar.t // 86_400_000
        if self._day is None:
            self._day = day
        if day != self._day:
            self.prev_day_profile = volume_profile(self._day_bars, self.row)
            self._day_bars, self._day = [], day
        self._day_bars.append(bar)

        hist = self.bars[-p["vol_avg_n"]:]
        self.bars.append(bar)
        self.n += 1
        if len(self.bars) > 500:
            self.bars = self.bars[-500:]
        if len(hist) < p["vol_avg_n"]:
            return []
        avg_vol = sum(b.vol for b in hist) / len(hist)
        vol, delta = bar.vol, bar.delta
        prior = self.bars[-p["extreme_n"] - 1:-1]
        new_high = bar.h > max(b.h for b in prior)
        new_low = bar.l < min(b.l for b in prior)
        mid = (bar.h + bar.l) / 2
        nrows = max(1, len(bar.rows))
        avg_cell = vol / nrows

        # 1 堆叠失衡：记下区域，之后回踩进场
        buy_imb, sell_imb = imbalances(bar, p)
        for lo, hi in stacked(buy_imb, p["stack_rows"]):
            self.zones.append({"side": 1, "lo": lo * self.row, "hi": (hi + 1) * self.row, "born": self.n, "used": False})
        for lo, hi in stacked(sell_imb, p["stack_rows"]):
            self.zones.append({"side": -1, "lo": lo * self.row, "hi": (hi + 1) * self.row, "born": self.n, "used": False})
        alive = []
        for z in self.zones:
            age = self.n - z["born"]
            if age > p["zone_life"] or z["used"]:
                continue
            alive.append(z)
            if age == 0 or "stacked" not in self.enabled:
                continue
            if z["side"] == 1 and bar.l <= z["hi"] and bar.c > z["lo"]:
                z["used"] = True
                sigs.append(self._mk("stacked", 1, bar.c, z["lo"] - self.row, bar, f"区间 {z['lo']:.6g}-{z['hi']:.6g}"))
            elif z["side"] == -1 and bar.h >= z["lo"] and bar.c < z["hi"]:
                z["used"] = True
                sigs.append(self._mk("stacked", -1, bar.c, z["hi"] + self.row, bar, f"区间 {z['lo']:.6g}-{z['hi']:.6g}"))
            elif (z["side"] == 1 and bar.c < z["lo"]) or (z["side"] == -1 and bar.c > z["hi"]):
                z["used"] = True        # 区域被打穿，作废
        self.zones = alive

        # 2 吸收：放量大砸却砸不下去（收在上半截、在近期低位）→ 多；镜像 → 空
        if "absorption" in self.enabled and vol >= p["absorb_vol_mult"] * avg_vol:
            if delta <= -p["absorb_delta_frac"] * vol and bar.c >= mid and bar.l <= min(b.l for b in prior[-10:]):
                sigs.append(self._mk("absorption", 1, bar.c, bar.l - self.row, bar, f"delta {delta:.0f}"))
            elif delta >= p["absorb_delta_frac"] * vol and bar.c <= mid and bar.h >= max(b.h for b in prior[-10:]):
                sigs.append(self._mk("absorption", -1, bar.c, bar.h + self.row, bar, f"delta {delta:.0f}"))

        # 3 衰竭：创新高，但最高那一格几乎没人买，收在下半截 → 空；镜像 → 多
        if "exhaustion" in self.enabled and bar.rows:
            top, bot = max(bar.rows), min(bar.rows)
            if new_high and bar.rows[top][1] <= p["exhaust_frac"] * avg_cell and bar.c < mid:
                sigs.append(self._mk("exhaustion", -1, bar.c, bar.h + self.row, bar))
            elif new_low and bar.rows[bot][0] <= p["exhaust_frac"] * avg_cell and bar.c > mid:
                sigs.append(self._mk("exhaustion", 1, bar.c, bar.l - self.row, bar))

        # 4 Delta 背离：创新高但 delta 为负、收阴 → 空；镜像 → 多
        if "divergence" in self.enabled:
            if new_high and delta < 0 and bar.c < bar.o:
                sigs.append(self._mk("divergence", -1, bar.c, bar.h + self.row, bar, f"delta {delta:.0f}"))
            elif new_low and delta > 0 and bar.c > bar.o:
                sigs.append(self._mk("divergence", 1, bar.c, bar.l - self.row, bar, f"delta {delta:.0f}"))

        # 5 被套：上一根在高位大量主动买，这一根收到它最低价下面 → 追高的人被套 → 空；镜像 → 多
        if "trapped" in self.enabled and len(self.bars) >= p["extreme_n"] + 2:
            pb = self.bars[-2]
            prior2 = self.bars[-p["extreme_n"] - 2:-2]
            pv = pb.vol
            if pv >= p["trap_vol_mult"] * avg_vol:
                if pb.delta >= p["trap_delta_frac"] * pv and pb.h > max(b.h for b in prior2) and bar.c < pb.l:
                    sigs.append(self._mk("trapped", -1, bar.c, max(pb.h, bar.h) + self.row, bar))
                elif pb.delta <= -p["trap_delta_frac"] * pv and pb.l < min(b.l for b in prior2) and bar.c > pb.h:
                    sigs.append(self._mk("trapped", 1, bar.c, min(pb.l, bar.l) - self.row, bar))

        # 6 价值区回归（80% 法则）：前一日价值区外收回到价值区内 → 目标前一日 POC
        vp = self.prev_day_profile
        if "va_reentry" in self.enabled and vp and len(self.bars) >= 2:
            pc = self.bars[-2].c
            lo3 = min(b.l for b in self.bars[-3:])
            hi3 = max(b.h for b in self.bars[-3:])
            if pc < vp["val"] <= bar.c < vp["poc"]:
                s = self._mk("va_reentry", 1, bar.c, lo3 - self.row, bar)
                s.target = vp["poc"]
                if s.target - bar.c > 0.5 * (bar.c - s.stop):
                    sigs.append(s)
            elif pc > vp["vah"] >= bar.c > vp["poc"]:
                s = self._mk("va_reentry", -1, bar.c, hi3 + self.row, bar)
                s.target = vp["poc"]
                if bar.c - s.target > 0.5 * (s.stop - bar.c):
                    sigs.append(s)
        return sigs


def auto_row_size(ranges: list[float], fine_tick: float, target_rows=20) -> float:
    """按最近K线的平均波动定格子大小，大约每根K线 20 格"""
    if not ranges:
        return fine_tick
    med = float(np.median(ranges))
    raw = med / target_rows
    # 取整到好看的数：1、2、2.5、5 乘 10 的几次方，并且是最小价格单位的整数倍
    mag = 10 ** math.floor(math.log10(max(raw, fine_tick)))
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if step >= raw:
            break
    k = max(1, round(step / fine_tick))
    return round(k * fine_tick, 10)
