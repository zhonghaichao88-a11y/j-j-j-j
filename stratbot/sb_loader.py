"""把 freqtrade 写的开源策略文件原样加载进来算信号（不装 freqtrade）。

做法：在加载策略文件的那一刻，临时塞一套"假的 freqtrade 接口"（IStrategy、参数类、merge_informative_pair 等），
策略文件一行不改；加载完就把假模块撤掉。只用到策略的三个函数：算指标、算进场信号、算出场信号，
外加它的设置（ROI 表、止损、移动止盈、出场信号规则）。下单、止损、移动止盈全部由 sb_engine 按 freqtrade 回测的规则执行。

老写法（V2：populate_buy_trend / 'buy' 列 / use_sell_signal ...）和新写法（V3：populate_entry_trend / enter_long ...）都支持。
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
import types
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
STRAT_DIR = os.path.join(HERE, "strategies")
TF_MIN = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480,
          "12h": 720, "1d": 1440}


# ---------------------------------------------------------------- 假的 freqtrade 接口
class _Param:
    """IntParameter / DecimalParameter / ... ：实盘和回测时只用默认值（.value），.range 也只有这一个值（和 freqtrade 一样）"""

    def __init__(self, *args, default=None, space=None, optimize=True, load=True, decimals=None, **kw):
        if default is None and args and not isinstance(args[0], (int, float)):
            default = args[0][0] if isinstance(args[0], (list, tuple)) and args[0] else None
        self.value = default
        self.space = space
        self.optimize = optimize
        self.load = load

    @property
    def range(self):
        return [self.value]

    def __repr__(self):
        return f"{type(self).__name__}({self.value})"


class IntParameter(_Param): pass
class DecimalParameter(_Param): pass
class RealParameter(_Param): pass
class BooleanParameter(_Param): pass


class CategoricalParameter(_Param):
    def __init__(self, categories, default=None, **kw):
        super().__init__(default=categories[0] if default is None else default, **kw)


def timeframe_to_minutes(tf):
    return TF_MIN[tf]


def timeframe_to_seconds(tf):
    return TF_MIN[tf] * 60


def timeframe_to_prev_date(tf, date=None):
    date = date or datetime.now(timezone.utc)
    m = TF_MIN[tf]
    ts = int(date.timestamp()) // (m * 60) * (m * 60)
    return datetime.fromtimestamp(ts, timezone.utc)


def timeframe_to_next_date(tf, date=None):
    return timeframe_to_prev_date(tf, date) + timedelta(minutes=TF_MIN[tf])


def merge_informative_pair(dataframe, informative, timeframe, timeframe_inf, ffill=True,
                           append_timeframe=True, date_column="date", suffix=None):
    """和 freqtrade 2026 版一样：大周期K线时间往后挪（大周期 - 小周期），只合并已经收盘的大周期K线"""
    import pandas as pd
    informative = informative.copy()
    m_inf, m = TF_MIN[timeframe_inf], TF_MIN[timeframe]
    if m == m_inf or informative.empty:
        informative["date_merge"] = informative[date_column]
    else:
        informative["date_merge"] = informative[date_column] + pd.to_timedelta(m_inf, "m") - pd.to_timedelta(m, "m")
    date_merge = "date_merge"
    if append_timeframe:
        date_merge = f"date_merge_{timeframe_inf}"
        informative.columns = [f"{c}_{timeframe_inf}" for c in informative.columns]
    elif suffix:
        date_merge = f"date_merge_{suffix}"
        informative.columns = [f"{c}_{suffix}" for c in informative.columns]
    if ffill:
        dataframe = pd.merge_ordered(dataframe, informative, fill_method="ffill", left_on="date",
                                     right_on=date_merge, how="left")
    else:
        dataframe = pd.merge(dataframe, informative, left_on="date", right_on=date_merge, how="left")
    return dataframe.drop(date_merge, axis=1)


def stoploss_from_open(open_relative_stop, current_profit, is_short=False, leverage=1.0):
    _current_profit = current_profit / leverage
    if (_current_profit == -1 and not is_short) or (is_short and _current_profit == 1):
        return 1
    if is_short:
        stoploss = -1 + ((1 - open_relative_stop / leverage) / (1 - _current_profit))
    else:
        stoploss = 1 - ((1 + open_relative_stop / leverage) / (1 + _current_profit))
    return max(stoploss * leverage, 0.0)


def stoploss_from_absolute(stop_rate, current_rate, is_short=False, leverage=1.0):
    if current_rate == 0:
        return 1
    stoploss = 1 - (stop_rate / current_rate)
    if is_short:
        stoploss = -stoploss
    return max(min(stoploss, 1.0), 0.0) * leverage


def informative(*a, **k):          # @informative 装饰器：本程序不支持（用到它的策略不收录），原样返回函数
    return lambda f: f


class IStrategy:
    INTERFACE_VERSION = 2
    minimal_roi = {}
    stoploss = -0.99
    trailing_stop = False
    trailing_stop_positive = None
    trailing_stop_positive_offset = 0.0
    trailing_only_offset_is_reached = False
    use_custom_stoploss = False
    timeframe = "5m"
    startup_candle_count = 0
    can_short = False
    position_adjustment_enable = False
    process_only_new_candles = True
    order_types = {"entry": "market", "exit": "market", "stoploss": "market", "stoploss_on_exchange": False}

    def __init__(self, config=None):
        self.config = config or {}

    def informative_pairs(self):
        return []


class _Trade:
    """策略里偶尔会查 Trade（比如 custom_exit 里看持仓）。本程序把这些查询都当成没有持仓"""

    @staticmethod
    def get_trades_proxy(**kw):
        return []

    @staticmethod
    def get_open_trade_count():
        return 0

    @staticmethod
    def get_trades(*a, **k):
        return []


class _RunMode:
    value = "dry_run"


class _DP:
    """假的 DataProvider：策略要别的币/别的周期的K线，从 frames 里给（本程序只喂主周期）"""
    runmode = _RunMode()

    def __init__(self):
        self.frames = {}

    def get_pair_dataframe(self, pair, timeframe=None, candle_type=""):
        import pandas as pd
        d = self.frames.get((pair, timeframe))
        return d.copy() if d is not None else pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    historic_ohlcv = get_pair_dataframe

    def get_analyzed_dataframe(self, pair, timeframe):
        import pandas as pd
        return pd.DataFrame(), None

    def current_whitelist(self):
        return sorted({p for p, _ in self.frames})


def _fake_modules():
    import technical.qtpylib as qtpylib
    m = {}

    def mod(name, **attrs):
        x = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(x, k, v)
        m[name] = x
        return x

    params = dict(IntParameter=IntParameter, DecimalParameter=DecimalParameter, RealParameter=RealParameter,
                  BooleanParameter=BooleanParameter, CategoricalParameter=CategoricalParameter)
    helpers = dict(merge_informative_pair=merge_informative_pair, stoploss_from_open=stoploss_from_open,
                   stoploss_from_absolute=stoploss_from_absolute, timeframe_to_minutes=timeframe_to_minutes,
                   timeframe_to_seconds=timeframe_to_seconds, timeframe_to_prev_date=timeframe_to_prev_date,
                   timeframe_to_next_date=timeframe_to_next_date, informative=informative)
    mod("freqtrade")
    mod("freqtrade.strategy", IStrategy=IStrategy, **params, **helpers, Trade=_Trade)
    mod("freqtrade.strategy.interface", IStrategy=IStrategy)
    mod("freqtrade.strategy.hyper", **params)
    mod("freqtrade.strategy.parameters", **params)
    mod("freqtrade.strategy.strategy_helper", **helpers)
    mod("freqtrade.persistence", Trade=_Trade, Order=type("Order", (), {}), LocalTrade=_Trade)
    mod("freqtrade.exchange", timeframe_to_minutes=timeframe_to_minutes, timeframe_to_seconds=timeframe_to_seconds,
        timeframe_to_prev_date=timeframe_to_prev_date, timeframe_to_next_date=timeframe_to_next_date)
    mod("freqtrade.vendor")
    mod("freqtrade.vendor.qtpylib")
    m["freqtrade.vendor.qtpylib.indicators"] = qtpylib
    m["freqtrade.vendor"].qtpylib = m["freqtrade.vendor.qtpylib"]
    m["freqtrade.vendor.qtpylib"].indicators = qtpylib
    try:
        import rapidjson  # noqa: F401
    except ImportError:
        mod("rapidjson", load=json.load, loads=json.loads, dump=json.dump, dumps=json.dumps,
            JSONDecodeError=json.JSONDecodeError, NM_NATIVE=0, PM_COMMENTS=0, PM_TRAILING_COMMAS=0)
    return m


def _np_compat():
    """numpy 2 删掉的旧名字（np.NaN 等），老策略还在用：补回来（不改策略逻辑）"""
    import numpy as np
    for a, v in (("NaN", float("nan")), ("NAN", float("nan")), ("Inf", float("inf")), ("float", float),
                 ("int", int), ("bool", bool), ("object", object)):
        if a not in np.__dict__:
            try:
                setattr(np, a, v)
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------- 加载
V2_ATTRS = {"use_sell_signal": "use_exit_signal", "sell_profit_only": "exit_profit_only",
            "sell_profit_offset": "exit_profit_offset", "ignore_roi_if_buy_signal": "ignore_roi_if_entry_signal"}


class Strategy:
    """一个加载好的策略。analyze(df) 返回带 enter_long / exit_long 两列的K线表（这两列是那根K线收盘时的信号）"""

    def __init__(self, cls_name: str, path: str | None = None):
        path = path or os.path.join(STRAT_DIR, cls_name + ".py")
        _np_compat()
        fake = _fake_modules()
        saved = {k: sys.modules.get(k) for k in fake}
        try:
            sys.modules.update(fake)
            spec = importlib.util.spec_from_file_location(f"sb_strat_{cls_name}", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        finally:
            for k, v in saved.items():          # 别把假的 freqtrade 留在全局
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
        cls = getattr(mod, cls_name)
        self.name = cls_name
        self.s = cls({"stake_currency": "USDT", "runmode": _RunMode(), "exchange": {"name": "okx"},
                      "max_open_trades": 10, "dry_run": True})
        self.s.dp = _DP()
        self._apply_params(cls)
        s = self.s
        g = lambda a, d=None: getattr(s, a, d)
        # V2 老名字 → V3（两个都写了以新名字为准）
        self.use_exit_signal = bool(g("use_exit_signal", g("use_sell_signal", True)))
        self.exit_profit_only = bool(g("exit_profit_only", g("sell_profit_only", False)))
        self.exit_profit_offset = float(g("exit_profit_offset", g("sell_profit_offset", 0.0)) or 0.0)
        self.ignore_roi_if_entry_signal = bool(g("ignore_roi_if_entry_signal", g("ignore_roi_if_buy_signal", False)))
        self.minimal_roi = {int(k): float(v) for k, v in (g("minimal_roi") or {}).items()}
        self.stoploss = float(g("stoploss"))
        self.trailing_stop = bool(g("trailing_stop", False))
        self.trailing_stop_positive = g("trailing_stop_positive")
        self.trailing_stop_positive_offset = float(g("trailing_stop_positive_offset", 0.0) or 0.0)
        self.trailing_only_offset_is_reached = bool(g("trailing_only_offset_is_reached", False))
        self.timeframe = g("timeframe", g("ticker_interval", "5m"))
        self.tf_min = TF_MIN[self.timeframe]
        self.startup = int(g("startup_candle_count", 0) or 0)
        self.v3 = hasattr(cls, "populate_entry_trend") and "populate_entry_trend" in cls.__dict__
        # 本程序不支持的功能：用到就拒绝加载（宁可不跑，也不能跑出和回测不一样的结果）
        bad = []
        if g("use_custom_stoploss", False):
            bad.append("自定义止损 custom_stoploss")
        if g("position_adjustment_enable", False):
            bad.append("加仓 position_adjustment")
        if g("can_short", False):
            bad.append("做空")
        for meth in ("custom_exit", "custom_sell", "custom_entry_price", "custom_exit_price", "confirm_trade_entry",
                     "confirm_trade_exit", "custom_stake_amount", "leverage", "custom_roi"):
            if meth in cls.__dict__ or any(meth in b.__dict__ for b in cls.__mro__[1:-1] if b is not IStrategy):
                bad.append(meth)
        if g("informative_pairs") and cls.informative_pairs is not IStrategy.informative_pairs:
            try:
                if s.informative_pairs():
                    bad.append("别的周期/别的币的K线 informative_pairs")
            except Exception:  # noqa: BLE001
                bad.append("informative_pairs")
        if self.trailing_stop and self.trailing_stop_positive is None and self.trailing_only_offset_is_reached:
            pass
        self.unsupported = bad

    def _apply_params(self, cls):
        """策略类里写的 buy_params / sell_params 覆盖参数默认值（freqtrade 也是这么做的）"""
        for space in ("buy", "sell", "protection", "entry", "exit"):
            vals = getattr(cls, f"{space}_params", None)
            if not isinstance(vals, dict):
                continue
            for k, v in vals.items():
                p = getattr(cls, k, None)
                if isinstance(p, _Param):
                    p.value = v

    def analyze(self, df, pair="BTC/USDT:USDT"):
        """df: date(UTC)/open/high/low/close/volume，按时间排好、没有缺K线。返回加了 enter_long / exit_long 的表"""
        import pandas as pd
        meta = {"pair": pair}
        d = df.copy()
        d = self.s.populate_indicators(d, meta)
        if self.v3:
            d = self.s.populate_entry_trend(d, meta)
            d = self.s.populate_exit_trend(d, meta)
            el = d["enter_long"] if "enter_long" in d else pd.Series(0, index=d.index)
            xl = d["exit_long"] if "exit_long" in d else pd.Series(0, index=d.index)
        else:
            d = self.s.populate_buy_trend(d, meta)
            d = self.s.populate_sell_trend(d, meta)
            el = d["buy"] if "buy" in d else pd.Series(0, index=d.index)
            xl = d["sell"] if "sell" in d else pd.Series(0, index=d.index)
        out = df[["date", "open", "high", "low", "close", "volume"]].copy()
        out["enter_long"] = (pd.to_numeric(el, errors="coerce").fillna(0) == 1).astype(int).values
        out["exit_long"] = (pd.to_numeric(xl, errors="coerce").fillna(0) == 1).astype(int).values
        return out

    def settings_text(self):
        roi = "，".join(f"{k} 分钟后 {'不管盈亏都平' if v <= -1 else f'赚 {v * 100:g}% 就平'}" for k, v in sorted(self.minimal_roi.items()))
        tr = ""
        if self.trailing_stop:
            tr = (f"移动止盈：赚到 {self.trailing_stop_positive_offset * 100:g}% 以后，从最高点回落 "
                  f"{(self.trailing_stop_positive or 0) * 100:g}% 就平")
        ex = ""
        if self.use_exit_signal:
            ex = "卖出信号出现时平仓" + (f"（只在赚超过 {self.exit_profit_offset * 100:g}% 时）" if self.exit_profit_only else "")
        return {"roi": roi, "stoploss": f"止损 {self.stoploss * 100:g}%", "trailing": tr, "exit": ex,
                "timeframe": self.timeframe}


def fill_missing(df, tf_min=5):
    """和 freqtrade 一样补齐缺的K线：开高低收都用上一根收盘价，成交量 0"""
    import pandas as pd
    if df.empty:
        return df
    df = df.sort_values("date").drop_duplicates("date", keep="last")
    idx = pd.date_range(df["date"].iloc[0], df["date"].iloc[-1], freq=f"{tf_min}min")
    if len(idx) == len(df):
        return df.reset_index(drop=True)
    d = df.set_index("date").reindex(idx)
    d["close"] = d["close"].ffill()
    for c in ("open", "high", "low"):
        d[c] = d[c].fillna(d["close"])
    d["volume"] = d["volume"].fillna(0)
    return d.rename_axis("date").reset_index()
