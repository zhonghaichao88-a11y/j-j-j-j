"""实战版订单流打法（贴近职业交易员的用法）：先有关键位，价格到了关键位，再用订单流确认，挂单进场。

参考的公开做法（Jigsaw / Axia / Fabio Valentini / Trader Dale 等教的思路）：
  1. 关键位才做：前一天高低点、前一天价值区上下沿和 POC、当天 VWAP、整数关口、开盘区间高低点
  2. 关键位上出现确认才进场：
     A 吸收：砸到支撑，主动卖很多（成交量 ≥ 平均 3 倍，delta 占量 ≥ 30%）却砸不破，收回关键位上方 → 多
     B 扫止损收回（Delta 背离）：刺破关键位创新低，但累计 delta 没创新低，收回关键位上方 → 多
     C 回踩 VWAP 失衡续涨：上涨趋势里回踩 VWAP，出现 3 格以上买方堆叠失衡、delta 为正 → 多
     （做空全部镜像）
  3. 只在活跃时段做（伦敦 07-11 点、纽约 13:30-17 点，UTC），每个币每天最多 3 单，亏 2 单当天停
  4. 进场用限价单（挂在确认K线收盘价，3 分钟内没成交就撤），止损放在这根K线极值外一点，
     止盈：到 1R 先平一半、剩下的止损移到保本，目标是下一个关键位（至少 2R）
所有判断只用已经收盘的 1 分钟足迹K线和之前的数据。
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

PB_PARAMS = {
    "level_tol_rows": 2,      # 价格离关键位几格以内算"到了"
    "vol_n": 30,              # 平均成交量看最近 30 根 1 分钟K线
    "absorb_vol": 3.0,
    "absorb_delta": 0.30,
    "imb_ratio": 3.0,
    "stack": 3,
    "stop_buf_rows": 2,       # 止损放在K线极值外面几格
    "min_stop_pct": 0.0012,   # 止损至少 0.12%（太近会被噪音扫掉）
    "max_stop_pct": 0.006,
    "min_rr": 2.0,
    "max_trades_day": 3,
    "max_losses_day": 2,
    "sessions": ((7 * 60, 11 * 60), (13 * 60 + 30, 17 * 60)),   # UTC 分钟
    "round_step": None,       # 整数关口间隔（BTC 1000，ETH 50，SOL 5），None=自动
    "or_minutes": 30,         # 纽约开盘区间：13:30 起 30 分钟
    "or_start": 13 * 60 + 30,
}

PB_NAMES = {"pb_absorb": "关键位吸收", "pb_sweep": "扫止损收回", "pb_vwap": "回踩VWAP失衡"}


@dataclass
class PbSignal:
    kind: str
    side: int
    entry: float       # 限价
    stop: float
    target: float
    level: str
    t: int


def _round_step(px):
    if px > 20000:
        return 1000.0
    if px > 1000:
        return 50.0
    if px > 50:
        return 5.0
    return 10 ** math.floor(math.log10(px))


class Playbook:
    def __init__(self, row: float, params: dict | None = None, enabled=None):
        self.p = dict(PB_PARAMS, **(params or {}))
        self.row = row
        self.enabled = set(enabled or PB_NAMES)
        self.day = None
        self.prev = None                     # 前一天：high, low, poc, vah, val
        self.d_hi = -math.inf; self.d_lo = math.inf
        self.d_prof: dict[int, float] = {}
        self.vwap_pv = 0.0; self.vwap_v = 0.0; self.vwap_hist = deque(maxlen=30)
        self.or_hi = self.or_lo = math.nan
        self.vols = deque(maxlen=self.p["vol_n"])
        self.cvd = 0.0
        self.swing = deque(maxlen=60)        # (low, high, cvd) 最近 60 分钟
        self.trades_today = 0
        self.losses_today = 0

    # ------------------------------------------------------------ 关键位
    def levels(self, px):
        out = []
        if self.prev:
            for k in ("high", "low", "poc", "vah", "val"):
                out.append((self.prev[k], "昨日" + {"high": "高点", "low": "低点", "poc": "POC", "vah": "VAH", "val": "VAL"}[k]))
        if self.vwap_v > 0:
            out.append((self.vwap_pv / self.vwap_v, "VWAP"))
        if not math.isnan(self.or_hi):
            out += [(self.or_hi, "开盘区间高"), (self.or_lo, "开盘区间低")]
        step = self.p["round_step"] or _round_step(px)
        base = math.floor(px / step) * step
        out += [(base, "整数关口"), (base + step, "整数关口")]
        return out

    def _new_day(self, day):
        if self.d_prof:
            rows = sorted(self.d_prof)
            poc = max(rows, key=lambda r: self.d_prof[r])
            tot = sum(self.d_prof.values()); lo = hi = poc; acc = self.d_prof[poc]
            while acc < 0.7 * tot and (lo > rows[0] or hi < rows[-1]):
                up = self.d_prof.get(hi + 1, 0) if hi < rows[-1] else -1
                dn = self.d_prof.get(lo - 1, 0) if lo > rows[0] else -1
                if up >= dn:
                    hi += 1; acc += max(up, 0)
                else:
                    lo -= 1; acc += max(dn, 0)
            self.prev = {"high": self.d_hi, "low": self.d_lo, "poc": (poc + 0.5) * self.row,
                         "vah": (hi + 1) * self.row, "val": lo * self.row}
        self.day = day
        self.d_hi, self.d_lo, self.d_prof = -math.inf, math.inf, {}
        self.vwap_pv = self.vwap_v = 0.0
        self.or_hi = self.or_lo = math.nan
        self.trades_today = self.losses_today = 0

    def record_result(self, win: bool):
        if not win:
            self.losses_today += 1

    # ------------------------------------------------------------ 每根 1 分钟足迹K线收盘调用
    def on_bar(self, b) -> list[PbSignal]:
        p, row = self.p, self.row
        day = b.t // 86_400_000
        if day != self.day:
            self._new_day(day)
        mod = (b.t // 60_000) % 1440
        vol, delta = b.vol, b.delta
        avg = sum(self.vols) / len(self.vols) if self.vols else 0.0
        lv = self.levels(b.c)
        prev_swing = list(self.swing)
        vwap_before = self.vwap_pv / self.vwap_v if self.vwap_v else math.nan
        # ---- 更新状态（放在判断之后用的都是"截至上一根"的值，这根自己的值单独用）
        self.vols.append(vol)
        self.cvd += delta
        self.swing.append((b.l, b.h, self.cvd))
        self.d_hi, self.d_lo = max(self.d_hi, b.h), min(self.d_lo, b.l)
        for r, (x, y) in b.rows.items():
            self.d_prof[r] = self.d_prof.get(r, 0.0) + x + y
            mid_px = (r + 0.5) * row
            self.vwap_pv += mid_px * (x + y); self.vwap_v += x + y
        self.vwap_hist.append(self.vwap_pv / self.vwap_v if self.vwap_v else math.nan)
        o0 = p["or_start"]
        if o0 <= mod < o0 + p["or_minutes"]:
            self.or_hi = b.h if math.isnan(self.or_hi) else max(self.or_hi, b.h)
            self.or_lo = b.l if math.isnan(self.or_lo) else min(self.or_lo, b.l)

        if len(prev_swing) < 20 or avg <= 0:
            return []
        if not any(a <= mod < z for a, z in p["sessions"]):
            return []
        if self.trades_today >= p["max_trades_day"] or self.losses_today >= p["max_losses_day"]:
            return []
        tol = p["level_tol_rows"] * row
        rng = max(b.h - b.l, 1e-12)
        sigs = []

        def mk(kind, side, stop_px, lvl_name):
            entry = b.c
            risk = abs(entry - stop_px)
            risk = min(max(risk, p["min_stop_pct"] * entry), p["max_stop_pct"] * entry)
            stop = entry - side * risk
            # 目标：下一个关键位，至少 2R
            cands = [x for x, _ in lv if (x - entry) * side > p["min_rr"] * risk]
            tgt = min(cands, key=lambda x: abs(x - entry)) if cands else entry + side * p["min_rr"] * risk
            tgt = min(tgt, entry + 4 * risk) if side == 1 else max(tgt, entry - 4 * risk)
            return PbSignal(kind, side, entry, stop, tgt, lvl_name, b.t)

        for lvl, name in lv:
            # A 关键位吸收
            if "pb_absorb" in self.enabled and vol >= p["absorb_vol"] * avg:
                if b.l <= lvl + tol and b.c > lvl and delta <= -p["absorb_delta"] * vol and b.c >= b.l + 0.5 * rng:
                    sigs.append(mk("pb_absorb", 1, b.l - p["stop_buf_rows"] * row, name)); break
                if b.h >= lvl - tol and b.c < lvl and delta >= p["absorb_delta"] * vol and b.c <= b.h - 0.5 * rng:
                    sigs.append(mk("pb_absorb", -1, b.h + p["stop_buf_rows"] * row, name)); break
            # B 扫止损收回 + CVD 背离：刺破关键位创 60 分钟新低，CVD 比之前低点时高，收回关键位上方
            if "pb_sweep" in self.enabled:
                lows = [x[0] for x in prev_swing]; highs = [x[1] for x in prev_swing]
                if b.l < lvl - tol and b.c > lvl and b.l < min(lows):
                    i = lows.index(min(lows))
                    if self.cvd > prev_swing[i][2]:
                        sigs.append(mk("pb_sweep", 1, b.l - p["stop_buf_rows"] * row, name)); break
                if b.h > lvl + tol and b.c < lvl and b.h > max(highs):
                    i = highs.index(max(highs))
                    if self.cvd < prev_swing[i][2]:
                        sigs.append(mk("pb_sweep", -1, b.h + p["stop_buf_rows"] * row, name)); break
        # C 回踩 VWAP 失衡续涨（趋势：VWAP 30 分钟内在涨，价格在 VWAP 上方）
        if "pb_vwap" in self.enabled and not math.isnan(vwap_before) and len(self.vwap_hist) >= 30:
            v_up = self.vwap_hist[-1] > self.vwap_hist[0]
            from of_core import imbalances, stacked
            bi, si = imbalances(b, {"imb_ratio": p["imb_ratio"], "imb_min_frac": 0.5})
            if v_up and b.l <= vwap_before + tol and b.c > vwap_before and delta > 0 and stacked(bi, p["stack"]):
                sigs.append(mk("pb_vwap", 1, min(b.l, vwap_before) - p["stop_buf_rows"] * row, "VWAP"))
            elif (not v_up) and b.h >= vwap_before - tol and b.c < vwap_before and delta < 0 and stacked(si, p["stack"]):
                sigs.append(mk("pb_vwap", -1, max(b.h, vwap_before) + p["stop_buf_rows"] * row, "VWAP"))
        return sigs[:1]
