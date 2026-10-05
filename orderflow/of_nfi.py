"""三个"急跌抄底"打法（只做多），回测见 分析/策略实验室/NFI信号对比/：

  nfi_5m   NFI 头部币急跌（5 分钟）：NostalgiaForInfinityX7 的 Top Coins 进场条件 141~145 原样（含它的安全检查），只做 NFI 头部币名单。
           出场：止盈 3%、止损 8%、最多 48 小时。
  nfi_15m  头部币 15 分钟急跌：把 141/142/144 的"急跌"判断放到 15 分钟K线上（幅度 4~6%），加 NFI 的安全检查，只做头部币。出场同上。
  vn_dip   大跌抄底（按波动）：1 小时跌超 5 个标准差、或 4 小时跌超 4 个标准差（标准差 = 这个币过去 7 天 1 小时涨跌的波动），
           只在 BTC 牛市（日线收盘在 200 天均线上方）开。出场：止盈 1 倍 ATR、止损 3 倍 ATR（1 小时 ATR14）、最多 48 小时。

不装 freqtrade：直接加载 NFI 原版策略文件（orderflow/nfi/NostalgiaForInfinityX7.py），用几行假的 freqtrade 接口把它跑起来。
"安全检查"是另外加载一份把 141~145 的触发部分去掉的同一个文件得到的（只剩检查）。
指标要 TA-Lib 和 pandas（启动脚本会自动装）；装不上这三个打法就不出信号，其他打法不受影响。"""
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
OKX_BAR = {"5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1Dutc"}   # 4H 在欧易就是按 UTC 0/4/8 点切
# 15 分钟急跌：(名字, 触发类型, 幅度, 用哪种安全检查)；any = 141~145 任一检查通过，数字 = 那一条的检查
TF15_RULES = [("A142", 0.05, "any"), ("A141", 0.05, "any"), ("A142", 0.04, "142"), ("A141", 0.06, "any"), ("C144", 0.15, "144")]
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


def neutralize(src: str) -> str:
    """把 141~145 的"触发"部分换成永远成立，只留前面的安全检查（和回测用的 strat_var 一样的改法）"""
    lines = src.split("\n"); out = []; i = 0; mode = None
    while i < len(lines):
        ln = lines[i]
        if "long_entry_condition_index == " in ln:
            idx = int(ln.split("==")[1].strip(" :"))
            mode = idx if idx in (141, 142, 143, 144, 145) else None
        if mode and ln.strip() == "# Logic":
            out.append(ln)
            j = i + 1
            while lines[j] != "          )":
                j += 1
            out.append("          long_entry_logic.append(rsi_3 > -1.0)")
            mode = None; i = j + 1
            continue
        out.append(ln); i += 1
    return "\n".join(out)


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


def _load_module(name, path, text=None):
    saved = {k: sys.modules.get(k) for k in ("freqtrade", "freqtrade.strategy", "freqtrade.strategy.interface",
                                               "freqtrade.persistence", "rapidjson")}
    try:
        sys.modules.update(_shim_modules())
        if text is None:
            spec = importlib.util.spec_from_file_location(name, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        else:
            mod = types.ModuleType(name); mod.__file__ = path
            exec(compile(text, path, "exec"), mod.__dict__)
        return mod
    finally:
        for k, v in saved.items():            # 别把假的 freqtrade 留在全局
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _make(mod, tags, all_top=False):
    cls = mod.NostalgiaForInfinityX7
    s = cls.__new__(cls)                     # 不走 freqtrade 的初始化（要交易所、数据库），只设算信号要用的东西
    s.config = {"stake_currency": "USDT", "trading_mode": "futures", "max_open_trades": 6, "runmode": _RunMode(),
                "exchange": {"name": "okx"}}
    s.dp = _DP()
    s.is_futures_mode = True
    s.long_entry_signal_params = {k: k.rsplit("_", 2)[1] in tags for k in cls.long_entry_signal_params}
    for t in tags:
        s.long_entry_signal_params.setdefault(f"long_entry_condition_{t}_enable", True)
    s.short_entry_signal_params = {k: False for k in cls.short_entry_signal_params}
    if all_top:
        s.top_coins_mode_coins = _Everything()
    return s


class NfiSignals:
    """加载一次策略（原版 + 只留安全检查的一份），之后 analyze(pair, frames) 每次算一个币。加载失败 self.err 写原因"""

    def __init__(self, path=NFI_FILE, tags=NFI_TAGS, all_coins=False):
        self.err, self.strat, self.guard, self.top = "", None, None, set()
        try:
            import numpy  # noqa: F401
            import pandas  # noqa: F401
            import talib  # noqa: F401
        except Exception as e:  # noqa: BLE001
            self.err = f"缺少 pandas / TA-Lib（{e}），这三个抄底打法不出信号；重新运行启动脚本会自动安装"
            return
        if not os.path.exists(path):
            self.err = f"找不到 NFI 策略文件 {path}"
            return
        try:
            mod = _load_module("of_nfi_x7", path)
            gmod = _load_module("of_nfi_x7_guard", path, neutralize(open(path, encoding="utf-8").read()))
        except Exception as e:  # noqa: BLE001
            self.err = f"加载 NFI 策略失败：{e}"
            return
        self.strat = _make(mod, tags, all_coins)
        self.guard = _make(gmod, NFI_TAGS, True)      # 安全检查：所有币都算（头部币限制在外面判断）
        self.top = set(mod.NostalgiaForInfinityX7.top_coins_mode_coins)
        self.tags = set(tags)

    def eligible(self, coin):
        return self.strat is not None and coin in self.strat.top_coins_mode_coins

    def analyze(self, pair, frames):
        """frames: {(pair, tf): DataFrame[date, open, high, low, close, volume]}，全是已收盘K线，date 是 UTC 开盘时间。
        返回带 enter_long / enter_tag 的 5 分钟 DataFrame（原版条件）"""
        s = self.strat
        s.dp.frames = frames
        df = s.populate_indicators(frames[(pair, "5m")].copy(), {"pair": pair})
        return s.populate_entry_trend(df, {"pair": pair})

    def evaluate(self, pair, frames):
        """最后一根 5 分钟K线：原版 141~145 哪些触发了、5 段安全检查哪些通过了；指标只算一次"""
        md = {"pair": pair}
        self.strat.dp.frames = frames; self.guard.dp.frames = frames
        df = self.strat.populate_indicators(frames[(pair, "5m")].copy(), md)
        a = self.strat.populate_entry_trend(df.copy(), md).iloc[-1]
        g = self.guard.populate_entry_trend(df.copy(), md).iloc[-1]
        fired = [t for t in str(a.get("enter_tag") or "").split() if t in self.tags] if a.get("enter_long", 0) == 1 else []
        ok = {t for t in str(g.get("enter_tag") or "").split() if t in NFI_TAGS} if g.get("enter_long", 0) == 1 else set()
        return fired, ok

    def signal(self, pair, frames):
        """最后一根收盘的 5 分钟K线有没有原版信号：返回 (True/False, 触发的条件号)"""
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


def _resample(d5, rule):
    return d5.set_index("date").resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()


def tf15_triggers(d5):
    """最后一根 5 分钟K线正好是一根 15 分钟K线的最后一根时，算那根 15 分钟K线上的急跌触发。
    返回 [(规则名, 安全检查要求)]；不在 15 分钟收盘时返回 []"""
    import numpy as np
    import pandas as pd
    import talib
    last = d5.date.iloc[-1]
    if (last.minute % 15) != 10:
        return []
    k = _resample(d5, "15min")
    if len(k) < 60 or k.index[-1] != last - pd.Timedelta("10min"):
        return []
    cl, hi, lo = k.close.values.astype(float), k.high.values.astype(float), k.low.values.astype(float)
    r3, r4, r14, r20 = (talib.RSI(cl, timeperiod=p) for p in (3, 4, 14, 20))
    sma = talib.SMA(cl, timeperiod=16); _, au = talib.AROON(hi, lo, timeperiod=14)
    wr = talib.WILLR(hi, lo, cl, timeperiod=14); cmax = pd.Series(cl).rolling(48).max().values
    c, i = cl[-1], -1
    fired = []
    for typ, x, guard in TF15_RULES:
        if typ == "A141":
            hit = r20[i] < r20[i - 1] and r3[i] < 30 and au[i] < 25 and c < sma[i] * (1 - x)
        elif typ == "A142":
            hit = r3[i] > 5 and r4[i] < 46 and r20[i] < r20[i - 1] and c < sma[i] * (1 - x)
        else:
            hit = wr[i] < -50 and r14[i] < 40 and cmax[i] >= c * (1 + x)
        if bool(np.nan_to_num(hit)):
            fired.append((f"15分钟{typ}跌{x:.0%}", guard))
    return fired


def vn_features(d5):
    """最后一根 5 分钟K线正好是整点前最后一根时，算刚收盘那根 1 小时K线的：
    vn1 = 1 小时涨跌 ÷ 7 天波动，vn4 = 4 小时涨跌 ÷ (7 天波动 × 2)，atr = 1 小时 ATR14。不在整点返回 None"""
    import numpy as np
    import pandas as pd
    import talib
    last = d5.date.iloc[-1]
    if last.minute != 55:
        return None
    k = _resample(d5, "1h")
    if len(k) < 60 or k.index[-1] != last - pd.Timedelta("55min"):
        return None
    kc = k.close.values.astype(float)
    r1 = np.r_[np.nan, kc[1:] / kc[:-1] - 1]
    vol1 = pd.Series(r1).rolling(24 * 7, min_periods=48).std().values
    vn1 = r1[-1] / vol1[-1]
    vn4 = (kc[-1] / kc[-5] - 1) / (vol1[-1] * np.sqrt(4))
    atr = talib.ATR(k.high.values.astype(float), k.low.values.astype(float), kc, 14)[-1]
    return {"vn1": float(vn1), "vn4": float(vn4), "atr": float(atr), "close": float(kc[-1])}


def btc_bull_200(daily_df):
    """BTC 日线（已收盘）：昨天收盘在 200 天均线上方 = True；不够 100 天返回 None（和回测一样，前 100 天以后就开始算）"""
    c = daily_df.close.values.astype(float)
    if len(c) < 100:
        return None
    ma = c[-200:].mean()
    return bool(c[-1] > ma)


async def fetch_back(get_json, client, inst, tf, n):
    """从欧易往前拉 n 根已收盘K线（最近 1440 根用 candles，再早用 history-candles）"""
    import asyncio
    rows, after, ep = [], None, "/api/v5/market/candles"
    while len(rows) < n + 1:
        p = {"instId": inst, "bar": OKX_BAR[tf], "limit": 300 if ep.endswith("/candles") else 100}
        if after:
            p["after"] = after
        data = await get_json(client, ep, **p)
        if not data:
            if ep.endswith("/candles"):
                ep = "/api/v5/market/history-candles"; continue
            break
        rows += data; after = data[-1][0]
        if ep.endswith("/candles") and len(rows) >= 1400:
            ep = "/api/v5/market/history-candles"
        await asyncio.sleep(0.12)
    return okx_rows_to_df(rows).tail(n).reset_index(drop=True)


async def fetch_new(get_json, client, inst, tf):
    return okx_rows_to_df(await get_json(client, "/api/v5/market/candles", instId=inst, bar=OKX_BAR[tf], limit=20))


class CandleCache:
    """每个币每个周期的已收盘K线；第一次整段拉，之后每次只拉最新几根补上"""

    def __init__(self):
        self.d = {}

    async def get(self, get_json, client, inst, tf):
        import pandas as pd
        cur = self.d.get((inst, tf))
        if cur is None or len(cur) < BARS[tf] * 0.5:
            cur = await fetch_back(get_json, client, inst, tf, BARS[tf])
        else:
            step = pd.Timedelta(minutes=TF_MIN[tf])
            now = pd.Timestamp.now(tz="UTC")
            if cur.date.iloc[-1] + 2 * step <= now:         # 有新的大周期K线收盘了才去拉
                new = await fetch_new(get_json, client, inst, tf)
                if len(new) and new.date.iloc[0] > cur.date.iloc[-1] + step:      # 断太久了，整段重拉
                    cur = await fetch_back(get_json, client, inst, tf, BARS[tf])
                else:
                    cur = pd.concat([cur, new]).drop_duplicates("date", keep="last").sort_values("date").tail(BARS[tf]).reset_index(drop=True)
        self.d[(inst, tf)] = cur
        return cur
