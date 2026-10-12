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


def _merge_informative(dataframe, informative, date_merge, ffill=True):
    """freqtrade _merge_prepared_informative_pair"""
    import pandas as pd
    if ffill:
        dataframe = pd.merge_ordered(dataframe, informative, fill_method="ffill", left_on="date",
                                     right_on=date_merge, how="left")
        if len(dataframe) > 1 and len(informative) > 0 and pd.isnull(dataframe.at[0, date_merge]):
            first_valid_idx = dataframe[date_merge].first_valid_index()
            if first_valid_idx:
                first = dataframe.at[first_valid_idx, date_merge]
                before = informative[informative[date_merge] < first]
                if not before.empty:
                    dataframe.loc[: first_valid_idx - 1] = dataframe.loc[: first_valid_idx - 1].fillna(before.iloc[-1])
    else:
        dataframe = pd.merge(dataframe, informative, left_on="date", right_on=date_merge, how="left")
    return dataframe.drop(date_merge, axis=1)


def _prepare_informative(informative, timeframe, timeframe_inf, append_timeframe=True, date_column="date", suffix=None,
                         date_merge_column="date_merge"):
    """freqtrade _prepare_informative_pair：大周期K线时间往后挪（大周期 - 小周期），只合并已经收盘的大周期K线"""
    import pandas as pd
    informative = informative.copy()
    m_inf, m = TF_MIN[timeframe_inf], TF_MIN[timeframe]
    if m == m_inf or informative.empty:
        informative[date_merge_column] = informative[date_column]
    elif m < m_inf:
        informative[date_merge_column] = informative[date_column] + pd.to_timedelta(m_inf, "m") - pd.to_timedelta(m, "m")
    else:
        raise ValueError("Tried to merge a faster timeframe to a slower timeframe.")
    date_merge = date_merge_column
    if suffix and append_timeframe:
        raise ValueError("You can not specify `append_timeframe` as True and a `suffix`.")
    if append_timeframe:
        date_merge = f"{date_merge_column}_{timeframe_inf}"
        informative.columns = [f"{c}_{timeframe_inf}" for c in informative.columns]
    elif suffix:
        date_merge = f"{date_merge_column}_{suffix}"
        informative.columns = [f"{c}_{suffix}" for c in informative.columns]
    return informative, date_merge


def merge_informative_pair(dataframe, informative, timeframe, timeframe_inf, ffill=True,
                           append_timeframe=True, date_column="date", suffix=None):
    """和 freqtrade 2026 版一样（大周期只合并已经收盘的K线，不偷看未来）"""
    inf, dm = _prepare_informative(informative, timeframe, timeframe_inf, append_timeframe, date_column, suffix)
    return _merge_informative(dataframe, inf, dm, ffill)


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


def informative(timeframe, asset="", fmt=None, *, candle_type=None, ffill=True, cache=True):
    """@informative 装饰器（和 freqtrade 一样）：populate_indicators_1h 这类函数算大周期/别的币的指标，合并进主K线"""
    def deco(fn):
        lst = getattr(fn, "_ft_informative", [])
        lst.append({"asset": asset, "timeframe": timeframe, "fmt": fmt, "ffill": ffill, "candle_type": candle_type})
        fn._ft_informative = lst
        return fn
    return deco


def _split_pair(pair):
    base, _, rest = pair.partition("/")
    quote = rest.split(":")[0] if rest else "USDT"
    return base, quote


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
    max_entry_position_adjustment = -1
    process_only_new_candles = True
    order_types = {"entry": "limit", "exit": "limit", "stoploss": "limit", "stoploss_on_exchange": False,
                   "stoploss_on_exchange_interval": 60}           # freqtrade 默认值
    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    def __init__(self, config=None):
        self.config = config or {}

    def informative_pairs(self):
        return []

    def bot_loop_start(self, current_time=None, **kwargs):
        return None

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake, min_stake, max_stake, leverage=1.0,
                            entry_tag=None, side="long", **kwargs):
        return proposed_stake

    def adjust_trade_position(self, trade, current_time, current_rate, current_profit, min_stake, max_stake, **kwargs):
        return None

    # freqtrade 的默认回调（策略没写就用这些）
    def custom_stoploss(self, pair, trade, current_time, current_rate, current_profit, after_fill=False, **kwargs):
        return self.stoploss

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        return self.custom_sell(pair, trade, current_time, current_rate, current_profit, **kwargs)

    def custom_sell(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        return None

    def confirm_trade_entry(self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs):
        return True

    def confirm_trade_exit(self, pair, trade, order_type, amount, rate, time_in_force, exit_reason, current_time, **kwargs):
        return True


class _Trade:
    """策略里查持仓用的 Trade（Trade.get_trades_proxy(is_open=True) 等）。每个策略一份（加载时生成子类），
    回测引擎 / 实盘程序把 provider 设成"返回当前持仓列表"的函数；没设就当没有持仓"""
    provider = None

    @classmethod
    def get_trades_proxy(cls, pair=None, is_open=None, **kw):
        if cls.provider is None:
            return []
        out = cls.provider()
        if pair is not None:
            out = [t for t in out if t.pair == pair]
        if is_open is False:
            return []
        return out

    @classmethod
    def get_open_trade_count(cls):
        return len(cls.get_trades_proxy(is_open=True))

    @classmethod
    def get_trades(cls, *a, **k):
        return cls.get_trades_proxy(is_open=True)

    @classmethod
    def get_open_trades(cls):
        return cls.get_trades_proxy(is_open=True)


class _Wallets:
    """策略里用的 self.wallets（freqtrade Wallets 的回测/模拟算法）。state() 返回 (起始资金, 已实现盈亏, 持仓占用资金, 可交易比例)"""

    def __init__(self, state=None):
        self.state = state

    def _s(self):
        return self.state() if self.state else (0.0, 0.0, 0.0, 0.99)

    def get_free(self, currency="USDT"):
        start, profit, tied, _ = self._s()
        return start + profit - tied

    def get_used(self, currency="USDT"):
        return 0.0

    def get_total(self, currency="USDT"):
        start, profit, tied, _ = self._s()
        return start + profit

    def get_starting_balance(self):
        start, profit, tied, ratio = self._s()
        return (self.get_free() - profit + tied) * ratio

    def get_total_stake_amount(self):
        start, profit, tied, ratio = self._s()
        return (tied + self.get_free()) * ratio

    def get_available_stake_amount(self):
        start, profit, tied, ratio = self._s()
        return min(self.get_total_stake_amount() - tied, self.get_free())


class _RunMode:
    """freqtrade 的 RunMode：回测时 value = backtest，实盘/模拟盘 = live / dry_run（有的策略两种情况算法不一样）"""
    value = "dry_run"

    def __init__(self, value="dry_run"):
        self.value = value
        self.name = value.upper()

    def __eq__(self, other):
        return getattr(other, "value", other) == self.value

    def __hash__(self):
        return hash(self.value)


class _PairLocks:
    """策略里偶尔查"这个币有没有被锁"。本程序不用保护锁：永远没锁"""

    @staticmethod
    def is_pair_locked(*a, **k):
        return False

    @staticmethod
    def is_global_lock(*a, **k):
        return False

    @staticmethod
    def get_pair_locks(*a, **k):
        return []

    @staticmethod
    def get_pair_longest_lock(*a, **k):
        return None

    @staticmethod
    def lock_pair(*a, **k):
        return None

    @staticmethod
    def unlock_pair(*a, **k):
        return None


class _DP:
    """假的 DataProvider。
    get_pair_dataframe：别的币/别的周期的K线，从 source(币, 周期) 拿（回测时由调用方给历史K线，实盘时现拉）；
    回测里策略回调（自定义止损等）调用时只给到当前时间之前已收盘的（和 freqtrade 一样，now 由引擎设）。"""
    runmode = _RunMode()

    def __init__(self):
        self.frames = {}
        self.analyzed = {}
        self.limit = {}
        self.source = None              # 函数 (pair, timeframe) -> DataFrame
        self.now = None                 # 回测时当前K线时间（datetime）；None = 不截
        self.whitelist = []
        self.main_tf = "5m"

    def get_pair_dataframe(self, pair, timeframe=None, candle_type=""):
        import pandas as pd
        tf = timeframe or self.main_tf
        d = None
        if self.source is not None:
            d = self.source(pair, tf)
        if d is None:
            d = self.frames.get((pair, tf))
        if d is None:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        if self.now is not None:
            d = d.loc[d["date"] < timeframe_to_prev_date(tf, self.now)]
        return d.copy()

    historic_ohlcv = get_pair_dataframe

    def ohlcv(self, pair, timeframe=None, copy=True, candle_type=""):
        return self.get_pair_dataframe(pair, timeframe)

    def get_analyzed_dataframe(self, pair, timeframe):
        """算好指标的K线。回测时只给到"上一根收盘K线"为止、最多 1000 根（和 freqtrade 回测一样）；实盘给全部"""
        import pandas as pd
        df = self.analyzed.get(pair)
        if df is None:
            return pd.DataFrame(), datetime.fromtimestamp(0, timezone.utc)
        end = self.limit.get(pair)
        if end is not None:
            df = df.iloc[max(0, end - 1000):end]
        return df, datetime.now(timezone.utc)

    def current_whitelist(self):
        return list(self.whitelist) or sorted({p for p, _ in self.frames})

    def market(self, pair):
        base, quote = _split_pair(pair)
        return {"base": base, "quote": quote, "symbol": pair}


def _fake_modules(trade_cls=_Trade):
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
    mod("freqtrade.strategy", IStrategy=IStrategy, **params, **helpers, Trade=trade_cls)
    mod("freqtrade.strategy.interface", IStrategy=IStrategy)
    mod("freqtrade.strategy.hyper", **params)
    mod("freqtrade.strategy.parameters", **params)
    mod("freqtrade.strategy.strategy_helper", **helpers)
    mod("freqtrade.persistence", Trade=trade_cls, Order=type("Order", (), {}), LocalTrade=trade_cls, PairLocks=_PairLocks)
    mod("freqtrade.misc", json_load=json.load, file_load_json=lambda f: json.load(open(f)),
        deep_merge_dicts=_deep_merge, round_dict=lambda d, n: {k: round(v, n) for k, v in d.items()})
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


def _deep_merge(source, destination, allow_null_overrides=True):
    for k, v in source.items():
        if isinstance(v, dict):
            _deep_merge(v, destination.setdefault(k, {}), allow_null_overrides)
        elif v is not None or allow_null_overrides:
            destination[k] = v
    return destination


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

    def __init__(self, cls_name: str, path: str | None = None, runmode: str = "dry_run"):
        path = path or os.path.join(STRAT_DIR, cls_name + ".py")
        _np_compat()
        self.trade_cls = type("Trade", (_Trade,), {"provider": None})     # 这个策略自己的 Trade（查持仓用）
        fake = _fake_modules(self.trade_cls)
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
        import pathlib
        udd = pathlib.Path(HERE) / "user_data"
        self.runmode = _RunMode(runmode)
        self.s = cls({"stake_currency": "USDT", "runmode": self.runmode, "exchange": {"name": "okx", "pair_whitelist": []},
                      "max_open_trades": 10, "dry_run": True, "user_data_dir": udd, "datadir": udd / "data",
                      "timeframe": getattr(cls, "timeframe", None) or getattr(cls, "ticker_interval", "5m"),
                      "candle_type_def": "spot", "trading_mode": "spot", "stake_amount": "unlimited"})
        self.s.dp = _DP()
        self.s.dp.runmode = self.runmode
        self.s.wallets = _Wallets()
        self.s.dp.main_tf = self.s.config["timeframe"]
        # @informative 装饰的函数（和 freqtrade 一样按名字排序收集）
        self.inf_methods = []
        for attr in dir(cls):
            fn = getattr(cls, attr, None)
            lst = getattr(fn, "_ft_informative", None) if callable(fn) else None
            if isinstance(lst, list):
                for d in lst:
                    self.inf_methods.append((d, fn))
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
        # 策略自己写的回调（照 freqtrade 规则调用）
        over = lambda m: any(m in b.__dict__ for b in cls.__mro__[:-1] if b is not IStrategy)
        self.use_custom_stoploss = bool(g("use_custom_stoploss", False))
        self.has_custom_exit = over("custom_exit") or over("custom_sell")
        self.has_confirm_exit = over("confirm_trade_exit")
        self.has_confirm_entry = over("confirm_trade_entry")
        import inspect
        self.stop_uses_after_fill = over("custom_stoploss") and "after_fill" in inspect.getfullargspec(s.custom_stoploss).args
        ot = dict(IStrategy.order_types); ot.update(g("order_types") or {})
        self.order_types = ot
        tif = g("order_time_in_force") or {}
        self.tif_entry = tif.get("entry", tif.get("buy", "GTC"))
        self.tif_exit = tif.get("exit", tif.get("sell", "GTC"))
        # 本程序不支持的功能：用到就拒绝加载（宁可不跑，也不能跑出和回测不一样的结果）
        bad = []
        self.position_adjustment_enable = bool(g("position_adjustment_enable", False))
        self.max_entry_position_adjustment = int(g("max_entry_position_adjustment", -1) if g("max_entry_position_adjustment", -1) is not None else -1)
        self.has_custom_stake = over("custom_stake_amount")
        if g("can_short", False):
            bad.append("做空")
        self.has_bot_loop_start = over("bot_loop_start")
        for meth in ("custom_entry_price", "custom_exit_price", "custom_roi",
                     "order_filled", "adjust_entry_price", "adjust_exit_price", "adjust_order_price",
                     "check_entry_timeout", "check_exit_timeout"):
            if over(meth):
                bad.append(meth)
        if g("use_custom_roi", False):
            bad.append("custom_roi")
        for d, _ in self.inf_methods:
            if d["timeframe"] not in TF_MIN or TF_MIN[d["timeframe"]] < self.tf_min:
                bad.append(f"@informative 周期 {d['timeframe']}")
            if d.get("candle_type") not in (None, "", "spot", "futures"):
                bad.append(f"@informative K线类型 {d['candle_type']}")
        # informative_pairs() 只用来提前知道要哪些K线（freqtrade 回测根本不调用它，策略要时才去拿）；出错不影响
        self.unsupported = bad
        # 回调里要用 dp.get_analyzed_dataframe 等（回测时要把算好指标的完整K线给引擎）
        self.uses_callbacks = bool(self.use_custom_stoploss or self.has_custom_exit or self.has_confirm_exit or self.has_confirm_entry
                                   or self.position_adjustment_enable or self.has_custom_stake or self.has_bot_loop_start)

    def informative_pairs_static(self, whitelist=None):
        """策略 informative_pairs() 返回的 (币, 周期)（要用 dp.current_whitelist 的，按给的币列表算）"""
        if whitelist is not None:
            self.s.dp.whitelist = list(whitelist)
        try:
            return [(x[0], x[1]) for x in (self.s.informative_pairs() or [])]
        except Exception:  # noqa: BLE001
            return []

    def inf_needs(self, pair, whitelist=None):
        """这个币算信号要的其他K线：{(币, 周期)}（币名照策略写的，比如 BTC/USDT）"""
        need = set(self.informative_pairs_static(whitelist))
        for d, _ in self.inf_methods:
            need.add((self._inf_asset(d, pair), d["timeframe"]))
        return need

    def _inf_asset(self, d, pair):
        if not d["asset"]:
            return pair
        base, quote = _split_pair(pair)
        return d["asset"].format(stake_currency="USDT", stake="USDT", base=base.lower(), BASE=base.upper(),
                                 quote=quote.lower(), QUOTE=quote.upper()).upper()

    def _apply_informative(self, d, pair):
        """freqtrade _create_and_merge_informative_pair"""
        for inf, fn in self.inf_methods:
            asset = self._inf_asset(inf, pair)
            tf = inf["timeframe"]
            fmt = inf["fmt"] or ("{column}_{timeframe}" if not inf["asset"] else "{base}_{quote}_{column}_{timeframe}")
            src = self.s.dp.get_pair_dataframe(asset, tf)
            if src.empty:
                raise ValueError(f"Informative dataframe for ({asset}, {tf}) is empty. Can't populate informative indicators.")
            src = fn(self.s, src, {"pair": asset, "timeframe": tf})
            base, quote = _split_pair(asset)
            args = {"base": base.lower(), "BASE": base.upper(), "quote": quote.lower(), "QUOTE": quote.upper(),
                    "asset": asset, "timeframe": tf}
            f = fmt if callable(fmt) else fmt.format
            src = src.rename(columns=lambda c: f(column=c, **args))
            date_col = f(column="date", **args)
            if date_col in d.columns:
                raise ValueError(f"Duplicate column name {date_col} exists in dataframe! Ensure column names are unique!")
            inf_df, dm = _prepare_informative(src, self.timeframe, tf, append_timeframe=False, date_column=date_col)
            d = _merge_informative(d, inf_df, dm, ffill=inf["ffill"])
        return d

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

    def analyze(self, df, pair="BTC/USDT:USDT", keep_full=False, inf=None):
        """df: date(UTC)/open/high/low/close/volume，按时间排好、没有缺K线。返回加了 enter_long / exit_long 的表。
        inf: 函数 (币, 周期) -> K线表，策略要别的币/别的周期时用（informative_pairs / @informative）"""
        import pandas as pd
        meta = {"pair": pair}
        d = df.copy()
        dp = self.s.dp
        if inf is not None:
            dp.source = inf
        dp.now = None
        if self.inf_methods:
            d = self._apply_informative(d, pair)
        d = self.s.populate_indicators(d, meta)
        # 和 freqtrade advise_entry / advise_exit 一样：先放空标签列，再算信号，老写法的 buy/sell 列改名
        d.loc[:, "enter_tag"] = ""
        d = self.s.populate_entry_trend(d, meta) if self.v3 else self.s.populate_buy_trend(d, meta)
        if "enter_long" not in d.columns:
            d = d.rename({"buy": "enter_long", "buy_tag": "enter_tag"}, axis="columns")
        d.loc[:, "exit_tag"] = ""
        d = self.s.populate_exit_trend(d, meta) if self.v3 else self.s.populate_sell_trend(d, meta)
        if "exit_long" not in d.columns:
            d = d.rename({"sell": "exit_long"}, axis="columns")
        col = lambda name: d[name] if name in d and not isinstance(d[name], pd.DataFrame) else (
            d[name].iloc[:, -1] if name in d else pd.Series(0 if "long" in name else "", index=d.index))
        out = df[["date", "open", "high", "low", "close", "volume"]].copy()
        out["enter_long"] = (pd.to_numeric(col("enter_long"), errors="coerce").fillna(0) == 1).astype(int).values
        out["exit_long"] = (pd.to_numeric(col("exit_long"), errors="coerce").fillna(0) == 1).astype(int).values
        for tag in ("enter_tag", "exit_tag"):
            v = col(tag)
            out[tag] = v.where(v.notna(), "").astype(str).replace("nan", "").values
        if keep_full:
            return out, d
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
        cu = "、".join(x for x, on in (("自定义止损（策略自己按情况收紧止损）", self.use_custom_stoploss),
                                        ("自定义平仓条件", self.has_custom_exit),
                                        ("平仓前再确认一次（不满足就先不平）", self.has_confirm_exit),
                                        ("开仓前再确认一次", self.has_confirm_entry),
                                        ("会加仓/分批平仓（策略自己决定什么时候补、补多少）", getattr(self, "position_adjustment_enable", False)),
                                        ("每单资金由策略自己定", getattr(self, "has_custom_stake", False))) if on)
        return {"roi": roi, "stoploss": f"止损 {self.stoploss * 100:g}%", "trailing": tr, "exit": ex,
                "custom": ("另外还有：" + cu) if cu else "", "timeframe": self.timeframe}


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


def callback_columns(st):
    """策略回调（自定义止损/平仓/确认/加仓/每单资金/每轮开始）里用到的K线列名：从源码里找字符串（连带回调里调用的策略自己的函数）。
    回调里有拼出来的列名（f-string）就返回 None（= 全部保留）。全部币回测时只留这些列，省内存（结果不变：回调只看得到这些列）"""
    import ast
    import inspect
    import textwrap
    cls = type(st.s)
    names = {"volume"}
    todo = ["custom_stoploss", "custom_exit", "custom_sell", "confirm_trade_exit", "confirm_trade_entry",
            "adjust_trade_position", "custom_stake_amount", "bot_loop_start"]
    seen = set()
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.add(m)
        fn = getattr(cls, m, None)
        if fn is None or getattr(IStrategy, m, None) is fn or not callable(fn):
            continue
        try:
            tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        except (OSError, TypeError, SyntaxError):
            return None
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                return None
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                names.add(node.value)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
                if callable(getattr(cls, node.attr, None)) and not hasattr(IStrategy, node.attr):
                    todo.append(node.attr)            # 回调里调用的策略自己的函数：一起看
    return names
