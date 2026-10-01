# TradingView "Pump Detector (EMA 4H, Retest H1)"：
# 4小时 EMA12 上穿 EMA21 且成交量 > 20根均量 → 之后10根4小时K线（40小时）内，
# 1小时K线最低价回踩到1小时 EMA12 或 EMA21 → 下一根开盘买入。
# 原作者没给出场规则，这里统一用：止损5%，止盈10%，最多拿48小时。
from freqtrade.strategy import IStrategy, informative
from pandas import DataFrame
import talib.abstract as ta

class PumpDetector(IStrategy):
    timeframe = "1h"
    can_short = False
    minimal_roi = {"0": 0.10, "2880": -1}
    stoploss = -0.05
    startup_candle_count = 120

    @informative("4h")
    def populate_indicators_4h(self, df: DataFrame, metadata: dict) -> DataFrame:
        e12 = ta.EMA(df, timeperiod=12); e21 = ta.EMA(df, timeperiod=21)
        cross = (e12 > e21) & (e12.shift(1) <= e21.shift(1)) & (df["volume"] > df["volume"].rolling(20).mean())
        # 10根4小时K线内有效
        df["active"] = cross.astype(int).rolling(10, min_periods=1).max()
        return df

    def populate_indicators(self, df: DataFrame, metadata: dict) -> DataFrame:
        df["e12"] = ta.EMA(df, timeperiod=12); df["e21"] = ta.EMA(df, timeperiod=21)
        return df
    def populate_entry_trend(self, df, metadata):
        touch = (df["low"] <= df["e12"]) | (df["low"] <= df["e21"])
        df.loc[(df["active_4h"] == 1) & touch & (df["volume"] > 0), "enter_long"] = 1
        return df
    def populate_exit_trend(self, df, metadata):
        return df
