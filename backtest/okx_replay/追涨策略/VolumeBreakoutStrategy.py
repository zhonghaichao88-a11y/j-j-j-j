"""
VolumeBreakoutStrategy — Volume-confirmed price breakout
=========================================================
Logic:
  Entry  : Price breaks above N-bar high AND volume > 2x average
            AND RSI between 50-70 (momentum zone, not overbought)
  Exit   : Price drops below N-bar low OR RSI > 80
  Stop   : 4% fixed + trailing after 3% profit
"""

from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter
from pandas import DataFrame
import talib.abstract as ta


class VolumeBreakoutStrategy(IStrategy):
    """Volume-confirmed breakout strategy."""

    timeframe = "5m"
    minimal_roi = {"0": 0.15, "120": 0.08, "360": 0.03}
    stoploss = -0.04
    trailing_stop = True
    trailing_stop_positive = 0.02
    trailing_stop_positive_offset = 0.03
    trailing_only_offset_is_reached = True
    can_short = False
    startup_candle_count = 50

    lookback = IntParameter(10, 30, default=20, space="buy")
    vol_mult = DecimalParameter(1.5, 3.0, default=2.0, space="buy")
    rsi_min = IntParameter(45, 58, default=50, space="buy")
    rsi_max = IntParameter(65, 80, default=70, space="buy")

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        # N-bar high and low
        dataframe["high_n"] = dataframe["high"].rolling(self.lookback.value).max().shift(1)
        dataframe["low_n"] = dataframe["low"].rolling(self.lookback.value).min().shift(1)

        # Volume average
        dataframe["vol_ma"] = dataframe["volume"].rolling(20).mean()
        dataframe["vol_ratio"] = dataframe["volume"] / dataframe["vol_ma"].replace(0, 1)

        # Breakout flag
        dataframe["breakout_up"] = (dataframe["close"] > dataframe["high_n"]).astype(int)
        dataframe["breakdown"] = (dataframe["close"] < dataframe["low_n"]).astype(int)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (dataframe["breakout_up"] == 1)
                & (dataframe["vol_ratio"] > self.vol_mult.value)
                & (dataframe["rsi"] > self.rsi_min.value)
                & (dataframe["rsi"] < self.rsi_max.value)
                & (dataframe["volume"] > 0)
            ),
            "enter_long",
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (dataframe["breakdown"] == 1)
                | (dataframe["rsi"] > 80)
            ),
            "exit_long",
        ] = 1
        return dataframe
