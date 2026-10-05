"""NFI 头部币抄跌（NostalgiaForInfinityX7 的 Top Coins 模式，进场条件 141~145）搬进订单流。

不装 freqtrade：直接加载 NFI 原版策略文件（orderflow/nfi/NostalgiaForInfinityX7.py），
用几行假的 freqtrade 接口（IStrategy、merge_informative_pair、Trade）把它跑起来，只打开 141~145 这 5 个进场条件。
指标要 TA-Lib 和 pandas（启动脚本会自动装）；装不上这个打法就不出信号，其他打法不受影响。

数据：每个币从欧易拉 5 分钟 / 15 分钟 / 1 小时 / 4 小时 / 日线收盘K线，加 BTC 4 小时，
每根 5 分钟K线收盘后算一次，最后一根K线有信号就出「NFI 头部币抄跌」做多信号。
回测（2022-2026 三段 OKX 数据，每个条件都比随机进场好、截断数据重算信号完全一致）见
分析/策略实验室/NFI信号对比/。"""
import importlib.util
import logging
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
NFI_FILE = os.path.join(HERE, "nfi", "NostalgiaForInfinityX7.py")
NFI_TAGS = ("141", "142", "143", "144", "145")
# 每个周期拉多少根收盘K线（5 分钟里最长的指标要 2016 根 = 7 天）
BARS = {"5m": 2600, "15m": 600, "1h": 600, "4h": 600, "1d": 400}
TF_MIN = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
OKX_BAR = {"5m": "5m", "15m": "15m", "1h": "1H", "4h": "4Hutc", "1d": "1Dutc"}
log = logging.getLogger("of_nfi")


class _RunMode:
    value = "dry_run"     # 当成实盘跑：不用回测才有的"上市满 3 天"过滤


def _shim_modules():
    """假的 freqtrade / rapidjson，只够加载策略文件、算指标和进场信号"""
    import json
    import pandas as pd

    def merge_informative_pair(dataframe, informative, timeframe, timeframe_inf, ffill=True,
                               append_timeframe=True, date_column="date", suffix=None):
        # 和 freqtrade 2026 版逻辑一样：大周期K线的时间往后挪一根（减一根小周期），只合并已经收盘的大周期K线
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
            if len(dataframe) > 1 and len(informative) > 0 and pd.isnull(dataframe.at[0, date_merge]):
                first = dataframe[date_merge].first_valid_index()
                if first:
                    prev = informative[informative[date_merge] < dataframe.at[first, date_merge]]
                    if not prev.empty:
                        dataframe.loc[: first - 1] = dataframe.loc[: first - 1].fillna(prev.iloc[-1])
        else:
            dataframe = pd.merge(dataframe, informative, left_on="date", right_on=date_merge, how="left")
        return dataframe.drop(date_merge, axis=1)

    class IStrategy:
        def __init__(self, config):
            self.config = config

    class Trade:
        @staticmethod
        def get_open_trade_count():
            return 0

        @staticmethod
        def get_trades_proxy(**kw):
            return []

    class Order:
        pass

    mods = {}
    ft = types.ModuleType("freqtrade"); mods["freqtrade"] = ft
    st = types.ModuleType("freqtrade.strategy"); st.merge_informative_pair = merge_informative_pair; st.IStrategy = IStrategy
    mods["freqtrade.strategy"] = st
    it = types.ModuleType("freqtrade.strategy.interface"); it.IStrategy = IStrategy; mods["freqtrade.strategy.interface"] = it
    pe = types.ModuleType("freqtrade.persistence"); pe.Trade = Trade; pe.Order = Order; mods["freqtrade.persistence"] = pe
    try:
        import rapidjson  # noqa: F401
    except ImportError:
        rj = types.ModuleType("rapidjson")
        rj.load, rj.loads, rj.dump, rj.dumps = json.load, json.loads, json.dump, json.dumps
        rj.JSONDecodeError = json.JSONDecodeError
        rj.NM_NATIVE = rj.PM_COMMENTS = rj.PM_TRAILING_COMMAS = 0
        mods["rapidjson"] = rj
    return mods


class _DP:
    """假的 DataProvider：策略要哪个币哪个周期的K线，就从这次喂进来的 frames 里给"""
    runmode = _RunMode()

    def __init__(self):
        self.frames = {}

    def get_pair_dataframe(self, pair, timeframe=None, candle_type=""):
        import pandas as pd
        d = self.frames.get((pair, timeframe or "5m"))
        return d.copy() if d is not None else pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    def current_whitelist(self):
        return sorted({p for p, _ in self.frames})


class NfiSignals:
    """加载一次策略，之后 signal(pair, frames) 每次算一个币。加载失败 self.err 写原因"""

    def __init__(self, path=NFI_FILE, tags=NFI_TAGS, all_coins=False):
        self.err, self.strat, self.top = "", None, set()
        try:
            import numpy  # noqa: F401
            import pandas  # noqa: F401
            import talib  # noqa: F401
        except Exception as e:  # noqa: BLE001
            self.err = f"缺少 pandas / TA-Lib（{e}），NFI 打法不出信号；重新运行启动脚本会自动安装"
            return
        if not os.path.exists(path):
            self.err = f"找不到 NFI 策略文件 {path}"
            return
        saved = {k: sys.modules.get(k) for k in ("freqtrade", "freqtrade.strategy", "freqtrade.strategy.interface",
                                                   "freqtrade.persistence", "rapidjson")}
        try:
            sys.modules.update(_shim_modules())
            spec = importlib.util.spec_from_file_location("of_nfi_x7", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:  # noqa: BLE001
            self.err = f"加载 NFI 策略失败：{e}"
            return
        finally:
            for k, v in saved.items():            # 别把假的 freqtrade 留在全局（同一个 Python 里要是真装了 freqtrade 也不影响）
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
        cls = mod.NostalgiaForInfinityX7
        s = cls.__new__(cls)                     # 不走 freqtrade 的初始化（要交易所、数据库），只设算信号要用的东西
        s.config = {"stake_currency": "USDT", "trading_mode": "futures", "max_open_trades": 6, "runmode": _RunMode(),
                    "exchange": {"name": "okx"}}
        s.dp = _DP()
        s.is_futures_mode = True
        s.long_entry_signal_params = {k: k.rsplit("_", 2)[1] in tags for k in cls.long_entry_signal_params}
        for t in tags:                            # 策略文件里被注释掉的条件也能打开
            s.long_entry_signal_params.setdefault(f"long_entry_condition_{t}_enable", True)
        s.short_entry_signal_params = {k: False for k in cls.short_entry_signal_params}
        self.top = set(cls.top_coins_mode_coins)
        if all_coins:                             # 不只头部币：所有币都当头部币
            s.top_coins_mode_coins = _Everything()
        self.strat, self.tags = s, set(tags)

    def eligible(self, coin):
        return self.strat is not None and coin in self.strat.top_coins_mode_coins

    def analyze(self, pair, frames):
        """frames: {(pair, tf): DataFrame[date, open, high, low, close, volume]}，全是已收盘K线，date 是 UTC 开盘时间。
        返回带 enter_long / enter_tag 的 5 分钟 DataFrame"""
        s = self.strat
        s.dp.frames = frames
        md = {"pair": pair}
        df = frames[(pair, "5m")].copy()
        df = s.populate_indicators(df, md)
        df = s.populate_entry_trend(df, md)
        return df

    def signal(self, pair, frames):
        """最后一根收盘的 5 分钟K线有没有信号：返回 (True/False, 触发的条件号)"""
        df = self.analyze(pair, frames)
        last = df.iloc[-1]
        tags = [t for t in str(last.get("enter_tag") or "").split() if t in self.tags]
        return bool(last.get("enter_long", 0) == 1 and tags), " ".join(tags)


class _Everything:
    def __contains__(self, x):
        return True


def okx_rows_to_df(rows):
    """欧易K线 [[ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm], ...] → 只留已收盘的，按时间排好"""
    import pandas as pd
    rows = [r for r in rows if len(r) < 9 or str(r[8]) == "1"]
    df = pd.DataFrame([[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in rows],
                      columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    df["date"] = pd.to_datetime(df.ts, unit="ms", utc=True)
    return df[["date", "open", "high", "low", "close", "volume"]]
