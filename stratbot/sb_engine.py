"""持仓和出场规则：照着 freqtrade 2026.9 的代码一条条搬过来（止损、移动止盈、ROI 时间表、卖出信号），
两种用法：
  - 回测 / 回放（Backtest）：一根根K线走，用K线的开高低价判断，规则和 freqtrade 回测完全一样（用来和 freqtrade 逐单对账）
  - 实盘 / 模拟盘（check_exit_live）：每几秒拿最新价判断，规则和 freqtrade 实盘一样
"""
from __future__ import annotations

import math
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

ROUND_UP_STOP = "up"


def to_tick(price: float, tick: float | None, mode: str = "nearest") -> float:
    """价格对齐到最小变动价位。止损价向上取（和 freqtrade 做多一样），其它四舍五入"""
    if not tick or price is None or math.isnan(price):
        return price
    p, t = Decimal(repr(float(price))), Decimal(repr(float(tick)))
    q = p / t
    q = q.to_integral_value(rounding=ROUND_CEILING if mode == ROUND_UP_STOP else ROUND_HALF_UP)
    return float(q * t)


def to_ms(col):
    """K线时间列 → 毫秒整数（不管原来是纳秒还是毫秒精度、带不带时区）"""
    import numpy as np
    if str(col.dtype).startswith("datetime"):
        v = col.dt.tz_convert(None) if getattr(col.dt, "tz", None) is not None else col
        return v.to_numpy().astype("datetime64[ms]").astype(np.int64)
    return col.to_numpy().astype(np.int64)


class Trade:
    def __init__(self, pair, strat, open_time, open_rate, amount, fee_open, fee_close, tick=None, stake=0.0, leverage=1.0):
        self.pair, self.strat = pair, strat
        self.open_time = open_time              # 毫秒时间戳
        self.open_rate = float(open_rate)
        self.amount = float(amount)
        self.stake = float(stake)
        self.fee_open, self.fee_close = fee_open, fee_close
        self.tick = tick
        self.leverage = leverage
        self.stop_loss = None
        self.stop_loss_pct = None
        self.initial_stop_loss = None
        self.initial_stop_loss_pct = None
        self.is_stop_loss_trailing = False
        self.max_rate = None
        self.min_rate = None
        # 实盘用
        self.order_id = ""
        self.algo_id = ""
        self.contracts = 0.0
        self.note = ""

    # ---------------- 盈亏（含手续费，和 freqtrade 一样保留 8 位）
    def open_value(self):
        return self.amount * self.open_rate * (1 + self.fee_open)

    def close_value(self, rate):
        return self.amount * rate * (1 - self.fee_close)

    def ratio(self, rate):
        ov = self.open_value()
        return 0.0 if ov == 0 else round((self.close_value(rate) / ov - 1) * self.leverage, 8)

    def profit_abs(self, rate):
        return self.close_value(rate) - self.open_value()

    def close_rate_for_roi(self, roi):
        a = self.close_value(1.0) - self.close_value(0.0)
        b = self.close_value(0.0)
        return ((1.0 + roi / self.leverage) * self.open_value() - b) / a

    # ---------------- 止损
    def adjust_min_max(self, hi, lo):
        self.max_rate = max(hi, self.max_rate or self.open_rate)
        self.min_rate = min(lo, self.min_rate or self.open_rate)

    def adjust_stop_loss(self, price, stoploss, initial=False):
        if stoploss is None or (initial and not (self.stop_loss is None or self.stop_loss == 0)):
            return
        new = to_tick(price * (1 - abs(stoploss / self.leverage)), self.tick, ROUND_UP_STOP)
        if self.initial_stop_loss_pct is None:
            self._set(new, stoploss)
            self.initial_stop_loss = to_tick(new, self.tick, ROUND_UP_STOP)
            self.initial_stop_loss_pct = -abs(stoploss)
        elif new > self.stop_loss:
            self.is_stop_loss_trailing = True
            self._set(new, stoploss)

    def _set(self, stop, pct):
        if not self.stop_loss:
            self.initial_stop_loss = stop
        self.stop_loss = stop
        self.stop_loss_pct = -abs(pct)

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d):
        t = cls.__new__(cls)
        t.__dict__.update(d)
        return t


# ---------------------------------------------------------------- 出场判断（strategy = sb_loader.Strategy）
def _stoploss_check(st, t: Trade, rate, low=None, high=None, bound_profit=None):
    """ft_stoploss_adjust + ft_stoploss_reached。返回 'stop_loss' / 'trailing_stop_loss' / None"""
    t.adjust_stop_loss(t.open_rate, st.stoploss, initial=True)
    dir_correct = t.stop_loss < (low or rate)
    bound = high
    if bound_profit is None:
        bound_profit = t.ratio(rate) if not bound else t.ratio(bound)
    if st.trailing_stop and dir_correct:
        off = st.trailing_stop_positive_offset
        if not (st.trailing_only_offset_is_reached and bound_profit < off):
            val = st.stoploss
            if st.trailing_stop_positive is not None and bound_profit > off:
                val = st.trailing_stop_positive
            t.adjust_stop_loss(bound or rate, val)
    if t.stop_loss >= (low or rate):
        return "trailing_stop_loss" if t.is_stop_loss_trailing else "stop_loss"
    return None


def roi_entry(st, dur_min):
    keys = [k for k in st.minimal_roi if k <= dur_min]
    if not keys:
        return None, None
    k = max(keys)
    return k, st.minimal_roi[k]


def should_exit(st, t: Trade, rate, dur_min, enter, exit_, low=None, high=None):
    """freqtrade IStrategy.should_exit：返回按顺序排好的出场原因列表"""
    cp = t.ratio(rate)
    best = cp
    if low is not None or high is not None:
        best = t.ratio(high or rate)
    t.adjust_min_max(high or rate, low or rate)
    sl = _stoploss_check(st, t, rate, low, high, bound_profit=best)
    roi_hit = False
    if not (enter and st.ignore_roi_if_entry_signal):
        _, roi = roi_entry(st, dur_min)
        roi_hit = roi is not None and best > roi
    out = []
    if st.use_exit_signal and exit_ and not enter:
        if not st.exit_profit_only or cp > st.exit_profit_offset:
            out.append("exit_signal")
    if sl == "stop_loss":
        out.append("stop_loss")
    if roi_hit:
        out.append("roi")
    if sl == "trailing_stop_loss":
        out.append("trailing_stop_loss")
    return out


class OpeningCandleRoi(ValueError):
    pass


def close_rate_backtest(st, t: Trade, reason, row, dur_min):
    """freqtrade 回测的平仓价（_get_close_rate）。row = (open, high, low, close)"""
    o, h, lo, c = row
    if reason in ("stop_loss", "trailing_stop_loss"):
        if t.stop_loss > h:
            return o
        if reason == "trailing_stop_loss" and dur_min == 0:
            if st.trailing_stop and st.trailing_only_offset_is_reached and st.trailing_stop_positive_offset is not None \
                    and st.trailing_stop_positive:
                r = o * (1 + abs(st.trailing_stop_positive_offset) - abs(st.trailing_stop_positive / t.leverage))
            else:
                r = o * (1 - abs((t.stop_loss_pct or 0.0) / t.leverage))
            return max(lo, r)
        return t.stop_loss
    if reason == "roi":
        k, roi = roi_entry(st, dur_min)
        if roi is not None and k is not None:
            if roi == -1 and k % st.tf_min == 0:
                return o
            cr = t.close_rate_for_roi(roi)
            if dur_min > 0 and dur_min == k and k % st.tf_min == 0 and o > cr:
                return o
            if dur_min == 0 and o > c and t.open_rate < o and cr > c:
                raise OpeningCandleRoi()
            return min(max(cr, lo), h)
        return o
    return o


class Backtest:
    """按 freqtrade 回测的循环走K线（不开保护、不加仓、不做空、单币同时一单）。

    data: {pair: DataFrame(date, open, high, low, close, volume, enter_long, exit_long)}，信号是那根K线收盘时的；
    这里会自动往后挪一根（第 t 根K线开盘时用第 t-1 根的信号），和 freqtrade 一样。
    返回平仓列表（字典），字段和 freqtrade 导出的交易记录对得上。
    """

    def __init__(self, st, max_open=10, fee=0.0007, ticks=None, wallet=1000.0, markets=None, tradable_ratio=0.99):
        self.st, self.max_open, self.fee, self.ticks = st, max_open, fee, ticks or {}
        self.wallet = wallet
        self.markets = markets              # {pair: {amin, amax, cmin, cmax, aprec}}：交易所最小/最大下单量（没有就不检查）
        self.ratio = tradable_ratio

    def stake_for(self, pair, price, tied, closed_profit):
        """freqtrade 回测的每单资金（stake_amount = unlimited）：可用资金 ÷ 剩余空位，再按交易所最小/最大下单量检查。
        返回 (资金, 数量)；资金为 0 表示这单开不了"""
        free = self.wallet + closed_profit - tied
        available = min((tied + free) * self.ratio - tied, free)
        stake = min((available + tied) / self.max_open, available)
        if stake <= 0:
            return 0.0, 0.0
        m = (self.markets or {}).get(pair)
        if m:
            reserve = max(min(1.05 / (1 - 0.05), 1.5), 1)            # freqtrade: 留 5% + 按 -5% 止损算的余量
            lims = []
            if m.get("cmin") is not None:
                lims.append(m["cmin"] * reserve)
            if m.get("amin") is not None:
                lims.append(m["amin"] * price * 1.05)
            mn = max(lims) if lims else None
            mx_l = [x for x in ((m.get("cmax")), (m["amax"] * price if m.get("amax") is not None else None)) if x is not None]
            mx = min(mx_l) if mx_l else float("inf")
            max_allowed = min(mx, available)
            if mn is not None and mn > max_allowed:
                return 0.0, 0.0
            if mn is not None and stake < mn:
                if stake * 1.3 < mn:
                    return 0.0, 0.0
                stake = mn
            stake = min(stake, max_allowed)
            amount = stake / price
            if m.get("aprec"):
                amount = math.floor(amount / m["aprec"] + 1e-9) * m["aprec"]
            if amount <= 0:
                return 0.0, 0.0
            return amount * price, amount
        return stake, stake / price

    def run(self, data, pairs=None):
        import numpy as np
        st = self.st
        pairs = [p for p in (pairs or list(data)) if p in data]
        arr, idx = {}, {}
        for p in pairs:
            d = data[p]
            ts = to_ms(d["date"])
            # 信号往后挪一根，然后去掉第一行（和 freqtrade 一样）
            el = np.r_[0, d["enter_long"].to_numpy()[:-1]]
            xl = np.r_[0, d["exit_long"].to_numpy()[:-1]]
            arr[p] = (ts[1:], d["open"].to_numpy()[1:], d["high"].to_numpy()[1:], d["low"].to_numpy()[1:],
                      d["close"].to_numpy()[1:], el[1:], xl[1:])
            idx[p] = 0
        if not arr:
            return []
        step = st.tf_min * 60_000
        start = min(a[0][0] for a in arr.values()) - step     # freqtrade: 从 start_date + 一根K线开始
        end = max(a[0][-1] for a in arr.values())
        open_trades: list[Trade] = []
        by_pair: dict[str, Trade] = {}
        closed = []
        closed_profit = 0.0
        cur = start + step
        last_row = {}
        while cur <= end:
            at_start = [t.pair for t in open_trades]
            order = list(dict.fromkeys(at_start + pairs))
            for p in order:
                a = arr[p]; i = idx[p]
                if i >= len(a[0]) or a[0][i] > cur:
                    continue
                row_i = i
                idx[p] = i + 1
                o, h, lo, c, en, ex = a[1][row_i], a[2][row_i], a[3][row_i], a[4][row_i], a[5][row_i], a[6][row_i]
                last_row[p] = (a[0][row_i], o)
                has = p in by_pair
                if p in at_start and not has:
                    continue
                trade_dir = en == 1 and not ex == 1
                if trade_dir and not has and cur != end and len(open_trades) < self.max_open:   # 最后一根K线不开新单
                    stake, amount = self.stake_for(p, o, sum(x.stake for x in open_trades), closed_profit)
                    if stake > 0:
                        t = Trade(p, st.name, int(a[0][row_i]), o, amount, self.fee, self.fee,
                                  tick=self._tick(p, int(a[0][row_i])), stake=stake)
                        t.adjust_stop_loss(t.open_rate, st.stoploss, initial=True)
                        open_trades.append(t); by_pair[p] = t; has = True
                if has:
                    t = by_pair[p]
                    dur = int((cur - t.open_time) // 60_000)
                    for reason in should_exit(st, t, o, dur, en == 1, ex == 1, low=lo, high=h):
                        try:
                            rate = close_rate_backtest(st, t, reason, (o, h, lo, c), dur)
                        except OpeningCandleRoi:
                            continue
                        t.pending = (to_tick(rate, t.tick), reason)   # 新的平仓单（替换掉没成交的旧单）
                        break
                    pend = getattr(t, "pending", None)
                    # 平仓单价格在这根K线范围内才算成交；不在（比如价格取整后比最低价还低一点点）就挂着，下根K线再看
                    if pend and lo <= pend[0] <= h:
                        rate, reason = pend
                        closed.append(self._close(t, cur, rate, reason))
                        closed_profit += t.profit_abs(rate)
                        open_trades.remove(t); del by_pair[p]
            cur += step
        for t in list(open_trades):                    # 回测结束还没平的：按最后一根K线开盘价强平（freqtrade: force_exit）
            ts, o_last = last_row[t.pair]
            closed.append(self._close(t, int(ts), to_tick(o_last, t.tick), "force_exit"))
        return closed

    def _tick(self, p, ts_ms):
        """价格精度：数字 = 固定；pandas Series（按月）= 取开仓那个月的（freqtrade 回测按K线数据推算每月精度）"""
        v = self.ticks.get(p)
        fallback = None
        if isinstance(v, tuple):          # (每月精度, 交易所精度)：推算不出来（比如价格全是整数）就用交易所的
            v, fallback = v
        if v is None or isinstance(v, (int, float)):
            return v if v is not None else fallback
        import pandas as pd
        x = v.asof(pd.Timestamp(ts_ms, unit="ms", tz="UTC"))
        return fallback if x != x else float(x)

    @staticmethod
    def _close(t: Trade, close_time, rate, reason):
        return {"pair": t.pair, "open_ts": int(t.open_time), "close_ts": int(close_time), "open_rate": t.open_rate,
                "close_rate": rate, "profit_ratio": t.ratio(rate), "exit_reason": reason,
                "trade_duration": int((close_time - t.open_time) // 60_000), "stake": t.stake,
                "profit_abs": t.profit_abs(rate)}


def check_exit_live(st, t: Trade, price, now_ms, enter, exit_):
    """实盘/模拟盘：拿最新价判断要不要平（freqtrade 实盘的规则：没有K线高低价，只看当前价）。返回原因或 None"""
    dur = int((now_ms - t.open_time) // 60_000)
    ex = should_exit(st, t, price, dur, bool(enter), bool(exit_))
    return ex[0] if ex else None
