# 成交量暴增顺势（StockSharp "Volume Spike Trend" 思路）：
# 1小时K线，成交量 > 20根均量的2倍：收盘在20均线上方做多，下方做空。反向信号平仓，止损2%。
from freqtrade.strategy import IStrategy
from pandas import DataFrame
import talib.abstract as ta

class VolumeSpikeTrend(IStrategy):
    timeframe = "1h"
    can_short = True
    minimal_roi = {"0": 100}
    stoploss = -0.02
    startup_candle_count = 30
    def populate_indicators(self, df: DataFrame, metadata: dict) -> DataFrame:
        df["ma"] = ta.SMA(df, timeperiod=20)
        df["vol_ma"] = df["volume"].rolling(20).mean()
        df["spike"] = df["volume"] > 2 * df["vol_ma"]
        return df
    def populate_entry_trend(self, df, metadata):
        df.loc[df["spike"] & (df["close"] > df["ma"]), "enter_long"] = 1
        df.loc[df["spike"] & (df["close"] < df["ma"]), "enter_short"] = 1
        return df
    def populate_exit_trend(self, df, metadata):
        df.loc[df["spike"] & (df["close"] < df["ma"]), "exit_long"] = 1
        df.loc[df["spike"] & (df["close"] > df["ma"]), "exit_short"] = 1
        return df
