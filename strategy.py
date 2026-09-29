"""
策略引擎
提供15种交易策略:
趋势类: ma_cross, ema_cross, triple_ma, macd, turtle, donchian, atr_breakout, momentum
震荡类: rsi, kdj, bollinger, mean_reversion, cci, grid
量价类: volume
"""
import time
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Tuple
from loguru import logger
import pandas as pd
import ta


class Signal:
    BUY = "buy"
    SELL = "sell"
    CLOSE_LONG = "close_long"
    CLOSE_SHORT = "close_short"
    HOLD = "hold"


class BaseStrategy(ABC):
    def __init__(self, name: str):
        self.name = name
        self.last_signal: str = Signal.HOLD
        self.signal_time: float = 0

    @abstractmethod
    def generate_signal(self, ohlcv: List[List[float]],
                        has_position: bool, position_side: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
        pass

    def _to_dataframe(self, ohlcv: List[List[float]]) -> pd.DataFrame:
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        return df

    def get_info(self) -> Dict[str, Any]:
        return {"name": self.name, "last_signal": self.last_signal, "last_signal_time": self.signal_time}


# ===== 1. 双均线交叉 =====
class MACrossStrategy(BaseStrategy):
    def __init__(self, fast_period: int = 9, slow_period: int = 21):
        super().__init__("ma_cross")
        self.fast_period = fast_period
        self.slow_period = slow_period

    def generate_signal(self, ohlcv, has_position=False, position_side=None):
        if len(ohlcv) < self.slow_period + 2:
            return Signal.HOLD, {"reason": "K线数据不足"}
        df = self._to_dataframe(ohlcv)
        df["ma_fast"] = df["close"].rolling(window=self.fast_period).mean()
        df["ma_slow"] = df["close"].rolling(window=self.slow_period).mean()
        prev, curr = df.iloc[-2], df.iloc[-1]
        info = {"ma_fast": round(curr["ma_fast"], 2), "ma_slow": round(curr["ma_slow"], 2), "price": round(curr["close"], 2)}
        golden = prev["ma_fast"] <= prev["ma_slow"] and curr["ma_fast"] > curr["ma_slow"]
        death = prev["ma_fast"] >= prev["ma_slow"] and curr["ma_fast"] < curr["ma_slow"]
        # 趋势判断(不只是交叉瞬间, 趋势明确就开仓)
        uptrend = curr["ma_fast"] > curr["ma_slow"] and curr["close"] > curr["ma_fast"]
        downtrend = curr["ma_fast"] < curr["ma_slow"] and curr["close"] < curr["ma_fast"]

        if not has_position:
            if golden or uptrend:
                signal = Signal.BUY; info["reason"] = "金叉/上升趋势买入"
            elif death or downtrend:
                signal = Signal.SELL; info["reason"] = "死叉/下降趋势开空"
            else:
                signal = Signal.HOLD; info["reason"] = "趋势不明"
        elif position_side == "long":
            if death or curr["close"] < curr["ma_slow"]:
                signal = Signal.CLOSE_LONG; info["reason"] = "死叉/跌破均线平多"
            else:
                signal = Signal.HOLD; info["reason"] = "持有多单"
        elif position_side == "short":
            if golden or curr["close"] > curr["ma_slow"]:
                signal = Signal.CLOSE_SHORT; info["reason"] = "金叉/突破均线平空"
            else:
                signal = Signal.HOLD; info["reason"] = "持有空单"
        else:
            signal = Signal.HOLD; info["reason"] = "无交叉"
        self.last_signal = signal; self.signal_time = time.time()
        return signal, info


# ===== 2. EMA交叉 =====
class EMACrossStrategy(BaseStrategy):
    def __init__(self, fast_period: int = 12, slow_period: int = 26):
        super().__init__("ema_cross")
        self.fast_period = fast_period
        self.slow_period = slow_period

    def generate_signal(self, ohlcv, has_position=False, position_side=None):
        if len(ohlcv) < self.slow_period + 2:
            return Signal.HOLD, {"reason": "K线数据不足"}
        df = self._to_dataframe(ohlcv)
        df["ema_fast"] = df["close"].ewm(span=self.fast_period, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=self.slow_period, adjust=False).mean()
        prev, curr = df.iloc[-2], df.iloc[-1]
        info = {"ema_fast": round(curr["ema_fast"], 2), "ema_slow": round(curr["ema_slow"], 2), "price": round(curr["close"], 2)}
        golden = prev["ema_fast"] <= prev["ema_slow"] and curr["ema_fast"] > curr["ema_slow"]
        death = prev["ema_fast"] >= prev["ema_slow"] and curr["ema_fast"] < curr["ema_slow"]
        # EMA趋势判断
        uptrend = curr["ema_fast"] > curr["ema_slow"] and curr["close"] > curr["ema_fast"]
        downtrend = curr["ema_fast"] < curr["ema_slow"] and curr["close"] < curr["ema_fast"]

        if not has_position:
            if golden or uptrend:
                signal = Signal.BUY; info["reason"] = "EMA金叉/上升趋势买入"
            elif death or downtrend:
                signal = Signal.SELL; info["reason"] = "EMA死叉/下降趋势开空"
            else:
                signal = Signal.HOLD; info["reason"] = "趋势不明"
        elif position_side == "long":
            if death or curr["close"] < curr["ema_slow"]:
                signal = Signal.CLOSE_LONG; info["reason"] = "EMA死叉/跌破均线平多"
            else:
                signal = Signal.HOLD; info["reason"] = "持有多单"
        elif position_side == "short":
            if golden or curr["close"] > curr["ema_slow"]:
                signal = Signal.CLOSE_SHORT; info["reason"] = "EMA金叉/突破均线平空"
            else:
                signal = Signal.HOLD; info["reason"] = "持有空单"
        else:
            signal = Signal.HOLD; info["reason"] = "无EMA交叉"
        self.last_signal = signal; self.signal_time = time.time()
        return signal, info


# ===== 3. 三均线策略 =====
class TripleMAStrategy(BaseStrategy):
    """均线多头排列买入, 空头排列卖出"""
    def __init__(self, p1: int = 5, p2: int = 10, p3: int = 20):
        super().__init__("triple_ma")
        self.p1, self.p2, self.p3 = p1, p2, p3

    def generate_signal(self, ohlcv, has_position=False, position_side=None):
        if len(ohlcv) < self.p3 + 2:
            return Signal.HOLD, {"reason": "K线数据不足"}
        df = self._to_dataframe(ohlcv)
        df["ma1"] = df["close"].rolling(self.p1).mean()
        df["ma2"] = df["close"].rolling(self.p2).mean()
        df["ma3"] = df["close"].rolling(self.p3).mean()
        prev, curr = df.iloc[-2], df.iloc[-1]
        info = {"ma1": round(curr["ma1"], 2), "ma2": round(curr["ma2"], 2), "ma3": round(curr["ma3"], 2), "price": round(curr["close"], 2)}
        bull_now = curr["ma1"] > curr["ma2"] > curr["ma3"]
        bear_now = curr["ma1"] < curr["ma2"] < curr["ma3"]
        # 状态触发: 多头排列就买, 空头排列就卖, 不只是变化瞬间
        if not has_position:
            if bull_now:
                signal = Signal.BUY; info["reason"] = "均线多头排列买入"
            elif bear_now:
                signal = Signal.SELL; info["reason"] = "均线空头排列开空"
            else:
                signal = Signal.HOLD; info["reason"] = "均线纠缠无趋势"
        elif position_side == "long":
            if bear_now or curr["close"] < curr["ma3"]:
                signal = Signal.CLOSE_LONG; info["reason"] = "均线空头排列/跌破长期均线平多"
            else:
                signal = Signal.HOLD; info["reason"] = "持有多单"
        elif position_side == "short":
            if bull_now or curr["close"] > curr["ma3"]:
                signal = Signal.CLOSE_SHORT; info["reason"] = "均线多头排列/突破长期均线平空"
            else:
                signal = Signal.HOLD; info["reason"] = "持有空单"
        else:
            signal = Signal.HOLD; info["reason"] = "均线排列未变化"
        self.last_signal = signal; self.signal_time = time.time()
        return signal, info


# ===== 4. MACD =====
class MACDStrategy(BaseStrategy):
    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        super().__init__("macd")
        self.fast, self.slow, self.signal_period = fast, slow, signal

    def generate_signal(self, ohlcv, has_position=False, position_side=None):
        if len(ohlcv) < self.slow + self.signal_period + 2:
            return Signal.HOLD, {"reason": "K线数据不足"}
        df = self._to_dataframe(ohlcv)
        macd_ind = ta.trend.MACD(close=df["close"], window_slow=self.slow, window_fast=self.fast, window_sign=self.signal_period)
        df["macd"] = macd_ind.macd()
        df["macd_signal"] = macd_ind.macd_signal()
        df["macd_hist"] = macd_ind.macd_diff()
        prev, curr = df.iloc[-2], df.iloc[-1]
        info = {"macd": round(curr["macd"], 4), "macd_signal": round(curr["macd_signal"], 4), "hist": round(curr["macd_hist"], 4), "price": round(curr["close"], 2)}
        golden = prev["macd"] <= prev["macd_signal"] and curr["macd"] > curr["macd_signal"]
        death = prev["macd"] >= prev["macd_signal"] and curr["macd"] < curr["macd_signal"]
        # MACD趋势判断
        uptrend = curr["macd"] > curr["macd_signal"] and curr["macd_hist"] > 0
        downtrend = curr["macd"] < curr["macd_signal"] and curr["macd_hist"] < 0

        if not has_position:
            if golden or uptrend:
                signal = Signal.BUY; info["reason"] = "MACD金叉/上升趋势买入"
            elif death or downtrend:
                signal = Signal.SELL; info["reason"] = "MACD死叉/下降趋势开空"
            else:
                signal = Signal.HOLD; info["reason"] = "趋势不明"
        elif position_side == "long":
            if death or curr["macd_hist"] < 0:
                signal = Signal.CLOSE_LONG; info["reason"] = "MACD死叉/柱转负平多"
            else:
                signal = Signal.HOLD; info["reason"] = "持有多单"
        elif position_side == "short":
            if golden or curr["macd_hist"] > 0:
                signal = Signal.CLOSE_SHORT; info["reason"] = "MACD金叉/柱转正平空"
            else:
                signal = Signal.HOLD; info["reason"] = "持有空单"
        else:
            signal = Signal.HOLD; info["reason"] = "无MACD交叉"
        self.last_signal = signal; self.signal_time = time.time()
        return signal, info


# ===== 5. RSI =====
class RSIStrategy(BaseStrategy):
    def __init__(self, period: int = 14, oversold: float = 30, overbought: float = 70):
        super().__init__("rsi")
        self.period, self.oversold, self.overbought = period, oversold, overbought

    def generate_signal(self, ohlcv, has_position=False, position_side=None):
        if len(ohlcv) < self.period + 2:
            return Signal.HOLD, {"reason": "K线数据不足"}
        df = self._to_dataframe(ohlcv)
        df["rsi"] = ta.momentum.RSIIndicator(close=df["close"], window=self.period).rsi()
        curr_rsi, prev_rsi = df["rsi"].iloc[-1], df["rsi"].iloc[-2]
        info = {"rsi": round(curr_rsi, 2), "price": round(df["close"].iloc[-1], 2)}
        # 状态触发: RSI低于超卖就买, 高于超买就卖, 不只是回升瞬间
        if not has_position:
            if curr_rsi < self.oversold:
                signal = Signal.BUY; info["reason"] = f"RSI超卖({curr_rsi:.1f})买入"
            elif curr_rsi > self.overbought:
                signal = Signal.SELL; info["reason"] = f"RSI超买({curr_rsi:.1f})开空"
            else:
                signal = Signal.HOLD; info["reason"] = f"RSI={curr_rsi:.1f}中性"
        elif position_side == "long":
            if curr_rsi > self.overbought:
                signal = Signal.CLOSE_LONG; info["reason"] = f"RSI超买({curr_rsi:.1f})平多"
            else:
                signal = Signal.HOLD; info["reason"] = "持有多单"
        elif position_side == "short":
            if curr_rsi < self.oversold:
                signal = Signal.CLOSE_SHORT; info["reason"] = f"RSI超卖({curr_rsi:.1f})平空"
            else:
                signal = Signal.HOLD; info["reason"] = "持有空单"
        else:
            signal = Signal.HOLD; info["reason"] = f"RSI={curr_rsi:.1f}无信号"
        self.last_signal = signal; self.signal_time = time.time()
        return signal, info


# ===== 6. KDJ =====
class KDJStrategy(BaseStrategy):
    def __init__(self, period: int = 9, smooth_k: int = 3, smooth_d: int = 3):
        super().__init__("kdj")
        self.period, self.smooth_k, self.smooth_d = period, smooth_k, smooth_d

    def generate_signal(self, ohlcv, has_position=False, position_side=None):
        if len(ohlcv) < self.period + self.smooth_k + self.smooth_d + 2:
            return Signal.HOLD, {"reason": "K线数据不足"}
        df = self._to_dataframe(ohlcv)
        stoch = ta.momentum.StochasticOscillator(high=df["high"], low=df["low"], close=df["close"], window=self.period, smooth_window=self.smooth_k)
        df["k"] = stoch.stoch()
        df["d"] = stoch.stoch_signal()
        df["j"] = 3 * df["k"] - 2 * df["d"]
        prev, curr = df.iloc[-2], df.iloc[-1]
        info = {"k": round(curr["k"], 2), "d": round(curr["d"], 2), "j": round(curr["j"], 2), "price": round(curr["close"], 2)}
        golden = prev["k"] <= prev["d"] and curr["k"] > curr["d"]
        death = prev["k"] >= prev["d"] and curr["k"] < curr["d"]
        # 状态触发: K低于20且金叉或K在低位就买; K高于80且死叉或K在高位就卖
        low_zone = curr["k"] < 30
        high_zone = curr["k"] > 70
        if not has_position:
            if (golden and low_zone) or low_zone:
                signal = Signal.BUY; info["reason"] = f"KDJ低位({curr['k']:.1f})买入"
            elif (death and high_zone) or high_zone:
                signal = Signal.SELL; info["reason"] = f"KDJ高位({curr['k']:.1f})开空"
            else:
                signal = Signal.HOLD; info["reason"] = f"KDJ K={curr['k']:.1f}中性"
        elif position_side == "long":
            if death or high_zone:
                signal = Signal.CLOSE_LONG; info["reason"] = f"KDJ高位/死叉({curr['k']:.1f})平多"
            else:
                signal = Signal.HOLD; info["reason"] = "持有多单"
        elif position_side == "short":
            if golden or low_zone:
                signal = Signal.CLOSE_SHORT; info["reason"] = f"KDJ低位/金叉({curr['k']:.1f})平空"
            else:
                signal = Signal.HOLD; info["reason"] = "持有空单"
        else:
            signal = Signal.HOLD; info["reason"] = "无KDJ信号"
        self.last_signal = signal; self.signal_time = time.time()
        return signal, info


# ===== 7. 布林带 =====
class BollingerStrategy(BaseStrategy):
    def __init__(self, period: int = 20, std_dev: float = 2.0):
        super().__init__("bollinger")
        self.period, self.std_dev = period, std_dev

    def generate_signal(self, ohlcv, has_position=False, position_side=None):
        if len(ohlcv) < self.period + 2:
            return Signal.HOLD, {"reason": "K线数据不足"}
        df = self._to_dataframe(ohlcv)
        bb = ta.volatility.BollingerBands(close=df["close"], window=self.period, window_dev=self.std_dev)
        df["bb_upper"] = bb.bollinger_hband()
        df["bb_middle"] = bb.bollinger_mavg()
        df["bb_lower"] = bb.bollinger_lband()
        prev, curr = df.iloc[-2], df.iloc[-1]
        info = {"upper": round(curr["bb_upper"], 2), "middle": round(curr["bb_middle"], 2), "lower": round(curr["bb_lower"], 2), "price": round(curr["close"], 2)}
        # 状态触发: 价格低于下轨就买, 高于上轨就卖, 不只是反弹瞬间
        below_lower = curr["close"] < curr["bb_lower"]
        above_upper = curr["close"] > curr["bb_upper"]
        if not has_position:
            if below_lower:
                signal = Signal.BUY; info["reason"] = "价格低于下轨超卖买入"
            elif above_upper:
                signal = Signal.SELL; info["reason"] = "价格高于上轨超买开空"
            else:
                signal = Signal.HOLD; info["reason"] = "价格在布林带内"
        elif position_side == "long":
            if above_upper:
                signal = Signal.CLOSE_LONG; info["reason"] = "价格高于上轨平多"
            else:
                signal = Signal.HOLD; info["reason"] = "持有多单"
        elif position_side == "short":
            if below_lower:
                signal = Signal.CLOSE_SHORT; info["reason"] = "价格低于下轨平空"
            else:
                signal = Signal.HOLD; info["reason"] = "持有空单"
        else:
            signal = Signal.HOLD; info["reason"] = "价格在布林带内"
        self.last_signal = signal; self.signal_time = time.time()
        return signal, info


# ===== 8. 均值回归 =====
class MeanReversionStrategy(BaseStrategy):
    """价格偏离均线过远时反向操作"""
    def __init__(self, period: int = 20, threshold: float = 0.02):
        super().__init__("mean_reversion")
        self.period, self.threshold = period, threshold

    def generate_signal(self, ohlcv, has_position=False, position_side=None):
        if len(ohlcv) < self.period + 2:
            return Signal.HOLD, {"reason": "K线数据不足"}
        df = self._to_dataframe(ohlcv)
        df["ma"] = df["close"].rolling(self.period).mean()
        curr = df.iloc[-1]
        deviation = (curr["close"] - curr["ma"]) / curr["ma"]
        info = {"ma": round(curr["ma"], 2), "deviation": f"{deviation*100:.2f}%", "price": round(curr["close"], 2)}
        if deviation < -self.threshold and not has_position:
            signal = Signal.BUY; info["reason"] = f"价格低于均线{abs(deviation)*100:.1f}%, 超卖买入"
        elif deviation > self.threshold and has_position and position_side == "long":
            signal = Signal.CLOSE_LONG; info["reason"] = f"价格高于均线{deviation*100:.1f}%, 超买平多"
        elif deviation > self.threshold and not has_position:
            signal = Signal.SELL; info["reason"] = f"价格高于均线{deviation*100:.1f}%, 超买开空"
        elif deviation < -self.threshold and has_position and position_side == "short":
            signal = Signal.CLOSE_SHORT; info["reason"] = "超卖区平空"
        else:
            signal = Signal.HOLD; info["reason"] = "偏离度不足"
        self.last_signal = signal; self.signal_time = time.time()
        return signal, info


# ===== 9. CCI =====
class CCIStrategy(BaseStrategy):
    def __init__(self, period: int = 20, oversold: float = -100, overbought: float = 100):
        super().__init__("cci")
        self.period, self.oversold, self.overbought = period, oversold, overbought

    def generate_signal(self, ohlcv, has_position=False, position_side=None):
        if len(ohlcv) < self.period + 2:
            return Signal.HOLD, {"reason": "K线数据不足"}
        df = self._to_dataframe(ohlcv)
        df["cci"] = ta.trend.CCIIndicator(high=df["high"], low=df["low"], close=df["close"], window=self.period).cci()
        curr_cci, prev_cci = df["cci"].iloc[-1], df["cci"].iloc[-2]
        info = {"cci": round(curr_cci, 2), "price": round(df["close"].iloc[-1], 2)}
        # 状态触发: CCI低于超卖就买, 高于超买就卖
        if not has_position:
            if curr_cci < self.oversold:
                signal = Signal.BUY; info["reason"] = f"CCI超卖({curr_cci:.0f})买入"
            elif curr_cci > self.overbought:
                signal = Signal.SELL; info["reason"] = f"CCI超买({curr_cci:.0f})开空"
            else:
                signal = Signal.HOLD; info["reason"] = f"CCI={curr_cci:.0f}中性"
        elif position_side == "long":
            if curr_cci > self.overbought:
                signal = Signal.CLOSE_LONG; info["reason"] = f"CCI超买({curr_cci:.0f})平多"
            else:
                signal = Signal.HOLD; info["reason"] = "持有多单"
        elif position_side == "short":
            if curr_cci < self.oversold:
                signal = Signal.CLOSE_SHORT; info["reason"] = f"CCI超卖({curr_cci:.0f})平空"
            else:
                signal = Signal.HOLD; info["reason"] = "持有空单"
        else:
            signal = Signal.HOLD; info["reason"] = f"CCI={curr_cci:.0f}无信号"
        self.last_signal = signal; self.signal_time = time.time()
        return signal, info


# ===== 10. 海龟交易法则 =====
class TurtleStrategy(BaseStrategy):
    """唐奇安通道突破, 20日高点突破买入, 10日低点跌破卖出"""
    def __init__(self, entry_period: int = 20, exit_period: int = 10):
        super().__init__("turtle")
        self.entry_period, self.exit_period = entry_period, exit_period

    def generate_signal(self, ohlcv, has_position=False, position_side=None):
        if len(ohlcv) < self.entry_period + 2:
            return Signal.HOLD, {"reason": "K线数据不足"}
        df = self._to_dataframe(ohlcv)
        df["high_n"] = df["high"].rolling(self.entry_period).max()
        df["low_n"] = df["low"].rolling(self.exit_period).min()
        prev, curr = df.iloc[-2], df.iloc[-1]
        info = {"high_n": round(curr["high_n"], 2), "low_n": round(curr["low_n"], 2), "price": round(curr["close"], 2)}
        # 状态触发: 价格在高点上方就买, 在低点下方就卖
        above_high = curr["close"] > curr["high_n"]
        below_low = curr["close"] < curr["low_n"]
        if not has_position:
            if above_high:
                signal = Signal.BUY; info["reason"] = f"价格在{self.entry_period}日高点上方买入"
            elif below_low:
                signal = Signal.SELL; info["reason"] = f"价格在{self.exit_period}日低点下方开空"
            else:
                signal = Signal.HOLD; info["reason"] = "在通道内"
        elif position_side == "long":
            if below_low:
                signal = Signal.CLOSE_LONG; info["reason"] = f"跌破{self.exit_period}日低点平多"
            else:
                signal = Signal.HOLD; info["reason"] = "持有多单"
        elif position_side == "short":
            if above_high:
                signal = Signal.CLOSE_SHORT; info["reason"] = f"突破{self.entry_period}日高点平空"
            else:
                signal = Signal.HOLD; info["reason"] = "持有空单"
        else:
            signal = Signal.HOLD; info["reason"] = "未突破通道"
        self.last_signal = signal; self.signal_time = time.time()
        return signal, info


# ===== 11. 唐奇安通道 =====
class DonchianStrategy(BaseStrategy):
    """价格突破上轨买入, 跌破下轨卖出(与海龟类似但上下轨同周期)"""
    def __init__(self, period: int = 20):
        super().__init__("donchian")
        self.period = period

    def generate_signal(self, ohlcv, has_position=False, position_side=None):
        if len(ohlcv) < self.period + 2:
            return Signal.HOLD, {"reason": "K线数据不足"}
        df = self._to_dataframe(ohlcv)
        df["dc_upper"] = df["high"].rolling(self.period).max()
        df["dc_lower"] = df["low"].rolling(self.period).min()
        prev, curr = df.iloc[-2], df.iloc[-1]
        info = {"upper": round(curr["dc_upper"], 2), "lower": round(curr["dc_lower"], 2), "price": round(curr["close"], 2)}
        # 状态触发: 价格在上轨上方就买, 在下轨下方就卖
        above_upper = curr["close"] > curr["dc_upper"]
        below_lower = curr["close"] < curr["dc_lower"]
        if not has_position:
            if above_upper:
                signal = Signal.BUY; info["reason"] = "价格在唐奇安上轨上方买入"
            elif below_lower:
                signal = Signal.SELL; info["reason"] = "价格在唐奇安下轨下方开空"
            else:
                signal = Signal.HOLD; info["reason"] = "在通道内"
        elif position_side == "long":
            if below_lower:
                signal = Signal.CLOSE_LONG; info["reason"] = "跌破唐奇安下轨平多"
            else:
                signal = Signal.HOLD; info["reason"] = "持有多单"
        elif position_side == "short":
            if above_upper:
                signal = Signal.CLOSE_SHORT; info["reason"] = "突破唐奇安上轨平空"
            else:
                signal = Signal.HOLD; info["reason"] = "持有空单"
        else:
            signal = Signal.HOLD; info["reason"] = "在通道内"
        self.last_signal = signal; self.signal_time = time.time()
        return signal, info


# ===== 12. ATR突破 =====
class ATRBreakoutStrategy(BaseStrategy):
    """收盘价超过前收盘价+N倍ATR -> 买入; 低于前收盘价-N倍ATR -> 卖出"""
    def __init__(self, period: int = 14, multiplier: float = 1.5):
        super().__init__("atr_breakout")
        self.period, self.multiplier = period, multiplier

    def generate_signal(self, ohlcv, has_position=False, position_side=None):
        if len(ohlcv) < self.period + 2:
            return Signal.HOLD, {"reason": "K线数据不足"}
        df = self._to_dataframe(ohlcv)
        df["atr"] = ta.volatility.AverageTrueRange(high=df["high"], low=df["low"], close=df["close"], window=self.period).average_true_range()
        prev, curr = df.iloc[-2], df.iloc[-1]
        atr_val = curr["atr"]
        info = {"atr": round(atr_val, 2), "multiplier": self.multiplier, "price": round(curr["close"], 2)}
        breakout_up = curr["close"] > prev["close"] + self.multiplier * atr_val
        breakout_down = curr["close"] < prev["close"] - self.multiplier * atr_val
        if breakout_up and not has_position:
            signal = Signal.BUY; info["reason"] = f"向上突破{self.multiplier}倍ATR买入"
        elif breakout_down and has_position and position_side == "long":
            signal = Signal.CLOSE_LONG; info["reason"] = f"向下跌破{self.multiplier}倍ATR平多"
        elif breakout_down and not has_position:
            signal = Signal.SELL; info["reason"] = f"向下跌破{self.multiplier}倍ATR开空"
        elif breakout_up and has_position and position_side == "short":
            signal = Signal.CLOSE_SHORT; info["reason"] = "向上突破平空"
        else:
            signal = Signal.HOLD; info["reason"] = "无ATR突破"
        self.last_signal = signal; self.signal_time = time.time()
        return signal, info


# ===== 13. 动量策略 =====
class MomentumStrategy(BaseStrategy):
    """N周期收益率为正且加速 -> 买入; 为负且加速 -> 卖出"""
    def __init__(self, period: int = 10, threshold: float = 0.0):
        super().__init__("momentum")
        self.period, self.threshold = period, threshold

    def generate_signal(self, ohlcv, has_position=False, position_side=None):
        if len(ohlcv) < self.period + 2:
            return Signal.HOLD, {"reason": "K线数据不足"}
        df = self._to_dataframe(ohlcv)
        df["momentum"] = df["close"].pct_change(self.period) * 100
        curr_mom, prev_mom = df["momentum"].iloc[-1], df["momentum"].iloc[-2]
        info = {"momentum": f"{curr_mom:.2f}%", "price": round(df["close"].iloc[-1], 2)}
        # 状态触发: 动量为正就买, 为负就卖
        if not has_position:
            if curr_mom > self.threshold:
                signal = Signal.BUY; info["reason"] = f"动量为正({curr_mom:.2f}%)买入"
            elif curr_mom < -self.threshold:
                signal = Signal.SELL; info["reason"] = f"动量为负({curr_mom:.2f}%)开空"
            else:
                signal = Signal.HOLD; info["reason"] = f"动量({curr_mom:.2f}%)接近零"
        elif position_side == "long":
            if curr_mom < -self.threshold:
                signal = Signal.CLOSE_LONG; info["reason"] = f"动量转负({curr_mom:.2f}%)平多"
            else:
                signal = Signal.HOLD; info["reason"] = "持有多单"
        elif position_side == "short":
            if curr_mom > self.threshold:
                signal = Signal.CLOSE_SHORT; info["reason"] = f"动量转正({curr_mom:.2f}%)平空"
            else:
                signal = Signal.HOLD; info["reason"] = "持有空单"
        else:
            signal = Signal.HOLD; info["reason"] = "动量无拐点"
        self.last_signal = signal; self.signal_time = time.time()
        return signal, info


# ===== 14. 成交量突破 =====
class VolumeStrategy(BaseStrategy):
    """成交量放大+价格上涨 -> 买入; 成交量放大+价格下跌 -> 卖出"""
    def __init__(self, volume_period: int = 20, volume_multiplier: float = 1.5):
        super().__init__("volume")
        self.volume_period, self.volume_multiplier = volume_period, volume_multiplier

    def generate_signal(self, ohlcv, has_position=False, position_side=None):
        if len(ohlcv) < self.volume_period + 2:
            return Signal.HOLD, {"reason": "K线数据不足"}
        df = self._to_dataframe(ohlcv)
        df["vol_ma"] = df["volume"].rolling(self.volume_period).mean()
        curr = df.iloc[-1]
        vol_ratio = curr["volume"] / curr["vol_ma"] if curr["vol_ma"] > 0 else 0
        price_change = (curr["close"] - df["close"].iloc[-2]) / df["close"].iloc[-2]
        info = {"volume": int(curr["volume"]), "vol_ma": int(curr["vol_ma"]), "vol_ratio": f"{vol_ratio:.2f}x", "price_change": f"{price_change*100:.2f}%", "price": round(curr["close"], 2)}
        if vol_ratio >= self.volume_multiplier and price_change > 0:
            if not has_position:
                signal = Signal.BUY; info["reason"] = f"放量{vol_ratio:.1f}倍上涨买入"
            elif position_side == "short":
                signal = Signal.CLOSE_SHORT; info["reason"] = f"放量{vol_ratio:.1f}倍上涨平空"
            else:
                signal = Signal.HOLD; info["reason"] = "放量上涨持有多单"
        elif vol_ratio >= self.volume_multiplier and price_change < 0:
            if has_position and position_side == "long":
                signal = Signal.CLOSE_LONG; info["reason"] = f"放量{vol_ratio:.1f}倍下跌平多"
            elif not has_position:
                signal = Signal.SELL; info["reason"] = f"放量{vol_ratio:.1f}倍下跌开空"
            else:
                signal = Signal.HOLD; info["reason"] = "放量下跌持有空单"
        else:
            signal = Signal.HOLD; info["reason"] = "成交量无异常"
        self.last_signal = signal; self.signal_time = time.time()
        return signal, info


# ===== 15. 网格交易 =====
class GridStrategy(BaseStrategy):
    def __init__(self, grid_count: int = 10, grid_pct: float = 0.01,
                 upper_price: Optional[float] = None, lower_price: Optional[float] = None):
        super().__init__("grid")
        self.grid_count, self.grid_pct = grid_count, grid_pct
        self.upper_price, self.lower_price = upper_price, lower_price
        self._grid_levels: List[float] = []
        self._last_grid_index: int = -1

    def _init_grid(self, current_price: float):
        if self.upper_price is None or self.lower_price is None:
            half = self.grid_count // 2
            self.lower_price = current_price * (1 - self.grid_pct * half)
            self.upper_price = current_price * (1 + self.grid_pct * half)
        step = (self.upper_price - self.lower_price) / self.grid_count
        self._grid_levels = [self.lower_price + i * step for i in range(self.grid_count + 1)]
        logger.info(f"网格初始化: {self.lower_price:.2f}~{self.upper_price:.2f}, {self.grid_count}格")

    def generate_signal(self, ohlcv, has_position=False, position_side=None):
        if len(ohlcv) < 2:
            return Signal.HOLD, {"reason": "数据不足"}
        df = self._to_dataframe(ohlcv)
        current_price, prev_price = df["close"].iloc[-1], df["close"].iloc[-2]
        if not self._grid_levels:
            self._init_grid(current_price)
        current_index = 0
        for i, level in enumerate(self._grid_levels):
            if current_price >= level:
                current_index = i
        info = {"price": round(current_price, 2), "grid_index": current_index, "grid_lower": round(self.lower_price, 2), "grid_upper": round(self.upper_price, 2)}
        if self._last_grid_index >= 0 and current_index < self._last_grid_index:
            signal = Signal.BUY; info["reason"] = "价格下跌穿网格买入"
        elif self._last_grid_index >= 0 and current_index > self._last_grid_index:
            if has_position:
                signal = Signal.CLOSE_LONG; info["reason"] = "价格上涨穿网格卖出"
            else:
                signal = Signal.HOLD; info["reason"] = "上涨但无持仓"
        else:
            signal = Signal.HOLD; info["reason"] = "网格内无操作"
        self._last_grid_index = current_index
        self.last_signal = signal; self.signal_time = time.time()
        return signal, info


# ===== 16. K线形态策略(FVG+完整K线形态识别) =====
class KlinePatternStrategy(BaseStrategy):
    """
    基于K线形态识别的交易策略
    包含: FVG公平价值缺口回踩入场 + 单根/双根/三根K线反转形态 + 突破形态
    做多做空都支持, 等回调/到达价格入场, 不追高
    识别形态清单:
      单根: 锤头线, 倒锤头, 射击之星, 上吊线, 十字星
      双根: 看涨吞没, 看跌吞没, 刺透形态, 乌云盖顶, 看涨孕线, 看跌孕线
      三根: 早晨之星, 黄昏之星, 红三兵, 三只乌鸦, 多方炮
      突破: 一阳穿三线, 一阴穿三线, 箱体突破, 箱体跌破, N字突破, 量价齐升
    """
    def __init__(self):
        super().__init__("kline_pattern")
        self._pending_fvg: List[Dict[str, Any]] = []
        self._shadow_ratio = 2.0
        self._fvg_lookback = 50
        self._fvg_max_age = 100

    # ---- 基础K线工具 ----
    def _is_bullish(self, c): return c["close"] > c["open"]
    def _is_bearish(self, c): return c["close"] < c["open"]
    def _body(self, c): return abs(c["close"] - c["open"])
    def _upper_shadow(self, c): return c["high"] - max(c["open"], c["close"])
    def _lower_shadow(self, c): return min(c["open"], c["close"]) - c["low"]

    def _is_doji(self, c, threshold=0.001):
        return self._body(c) <= c["close"] * threshold

    def _is_hammer(self, c):
        b = self._body(c)
        if b == 0: return False
        return self._lower_shadow(c) >= self._shadow_ratio * b and self._upper_shadow(c) <= b * 0.5

    def _is_inverted_hammer(self, c):
        b = self._body(c)
        if b == 0: return False
        return self._upper_shadow(c) >= self._shadow_ratio * b and self._lower_shadow(c) <= b * 0.5

    # ---- 双根形态 ----
    def _is_bullish_engulfing(self, c1, c2):
        if not self._is_bearish(c1) or not self._is_bullish(c2): return False
        return c2["open"] <= c1["close"] and c2["close"] >= c1["open"]

    def _is_bearish_engulfing(self, c1, c2):
        if not self._is_bullish(c1) or not self._is_bearish(c2): return False
        return c2["open"] >= c1["close"] and c2["close"] <= c1["open"]

    def _is_piercing(self, c1, c2):
        if not self._is_bearish(c1) or not self._is_bullish(c2): return False
        mid = (c1["open"] + c1["close"]) / 2
        return c2["open"] < c1["close"] and c2["close"] > mid and c2["close"] < c1["open"]

    def _is_dark_cloud(self, c1, c2):
        if not self._is_bullish(c1) or not self._is_bearish(c2): return False
        mid = (c1["open"] + c1["close"]) / 2
        return c2["open"] > c1["close"] and c2["close"] < mid and c2["close"] > c1["open"]

    def _is_bullish_harami(self, c1, c2):
        if not self._is_bearish(c1) or not self._is_bullish(c2): return False
        return c2["open"] > c1["close"] and c2["close"] < c1["open"] and self._body(c2) < self._body(c1) * 0.6

    def _is_bearish_harami(self, c1, c2):
        if not self._is_bullish(c1) or not self._is_bearish(c2): return False
        return c2["open"] < c1["close"] and c2["close"] > c1["open"] and self._body(c2) < self._body(c1) * 0.6

    # ---- 三根形态 ----
    def _is_morning_star(self, c1, c2, c3):
        if not self._is_bearish(c1) or not self._is_bullish(c3): return False
        if self._body(c2) > self._body(c1) * 0.3: return False
        return c3["close"] > (c1["open"] + c1["close"]) / 2

    def _is_evening_star(self, c1, c2, c3):
        if not self._is_bullish(c1) or not self._is_bearish(c3): return False
        if self._body(c2) > self._body(c1) * 0.3: return False
        return c3["close"] < (c1["open"] + c1["close"]) / 2

    def _is_three_white_soldiers(self, c1, c2, c3):
        if not (self._is_bullish(c1) and self._is_bullish(c2) and self._is_bullish(c3)): return False
        return c3["close"] > c2["close"] > c1["close"]

    def _is_three_black_crows(self, c1, c2, c3):
        if not (self._is_bearish(c1) and self._is_bearish(c2) and self._is_bearish(c3)): return False
        return c3["close"] < c2["close"] < c1["close"]

    def _is_three_inside_up(self, c1, c2, c3):
        if not (self._is_bullish(c1) and self._is_bearish(c2) and self._is_bullish(c3)): return False
        return c3["close"] > c1["close"]

    # ---- FVG识别 ----
    def _detect_fvg(self, candles, idx):
        if idx < 2: return None
        c1, c2, c3 = candles[idx-2], candles[idx-1], candles[idx]
        if c1["high"] < c3["low"] and self._is_bullish(c2) and self._body(c2) > 0:
            return {"type": "bullish", "top": c3["low"], "bottom": c1["high"], "created_at": idx}
        if c1["low"] > c3["high"] and self._is_bearish(c2) and self._body(c2) > 0:
            return {"type": "bearish", "top": c1["low"], "bottom": c3["high"], "created_at": idx}
        return None

    def _update_fvg(self, candles):
        n = len(candles)
        for i in range(max(2, n - self._fvg_lookback), n):
            fvg = self._detect_fvg(candles, i)
            if fvg and not any(f["created_at"] == fvg["created_at"] and f["type"] == fvg["type"] for f in self._pending_fvg):
                self._pending_fvg.append(fvg)
                logger.info(f"[FVG识别] 发现{fvg['type']}FVG | 区域={fvg['bottom']:.2f}~{fvg['top']:.2f}")
        self._pending_fvg = [f for f in self._pending_fvg if n - f["created_at"] <= self._fvg_max_age]

    # ---- 主信号 ----
    def generate_signal(self, ohlcv, has_position=False, position_side=None):
        if len(ohlcv) < 30:
            return Signal.HOLD, {"reason": "K线数据不足(需要至少30根)"}
        df = self._to_dataframe(ohlcv)
        candles = df.reset_index().to_dict("records")
        n = len(candles)
        price = candles[-1]["close"]
        c1, c2, c3 = candles[-1], candles[-2], candles[-3]
        info = {"price": round(price, 2), "fvg_pending": len(self._pending_fvg)}

        self._update_fvg(candles)

        # 高低位判断
        lb = min(20, n)
        rh = max(c["high"] for c in candles[-lb:])
        rl = min(c["low"] for c in candles[-lb:])
        pos = (price - rl) / (rh - rl) if rh > rl else 0.5
        is_low, is_high = pos < 0.3, pos > 0.7

        # 均线
        ma5 = sum(c["close"] for c in candles[-5:]) / 5
        ma10 = sum(c["close"] for c in candles[-10:]) / 10
        ma20 = sum(c["close"] for c in candles[-20:]) / 20 if n >= 20 else ma10

        # ===== 看涨形态扫描 =====
        bull = []
        if self._is_hammer(c1) and is_low: bull.append(("锤头线", 1))
        if self._is_inverted_hammer(c1) and is_low: bull.append(("倒锤头", 1))
        if self._is_doji(c1) and is_low: bull.append(("十字星(低位)", 0.5))
        if self._is_bullish_engulfing(c2, c1): bull.append(("看涨吞没", 2))
        if self._is_piercing(c2, c1): bull.append(("刺透形态", 1.5))
        if self._is_bullish_harami(c2, c1): bull.append(("看涨孕线", 1))
        if self._is_morning_star(c3, c2, c1): bull.append(("早晨之星", 2.5))
        if self._is_three_white_soldiers(c3, c2, c1): bull.append(("红三兵", 2))
        if self._is_three_inside_up(c3, c2, c1): bull.append(("多方炮", 1.5))
        if self._is_bullish(c1) and c1["open"] < min(ma5, ma10, ma20) and c1["close"] > max(ma5, ma10, ma20):
            bull.append(("一阳穿三线", 2))
        if n >= 12:
            bh = max(c["high"] for c in candles[-11:-1])
            if c1["close"] > bh and self._is_bullish(c1): bull.append(("箱体突破", 2))
        if n >= 8:
            sh = max(c["high"] for c in candles[-8:-3])
            pl = min(c["low"] for c in candles[-5:-1])
            if c1["close"] > sh and pl < sh * 0.97 and self._is_bullish(c1):
                bull.append(("N字突破", 1.5))
        if n >= 6:
            vm = sum(c["volume"] for c in candles[-6:-1]) / 5
            if c1["volume"] > vm * 1.5 and self._is_bullish(c1) and c1["close"] > c2["close"]:
                bull.append(("量价齐升", 1))

        # ===== 看跌形态扫描 =====
        bear = []
        if self._is_inverted_hammer(c1) and is_high: bear.append(("射击之星", 1))
        if self._is_hammer(c1) and is_high: bear.append(("上吊线", 1))
        if self._is_doji(c1) and is_high: bear.append(("十字星(高位)", 0.5))
        if self._is_bearish_engulfing(c2, c1): bear.append(("看跌吞没", 2))
        if self._is_dark_cloud(c2, c1): bear.append(("乌云盖顶", 1.5))
        if self._is_bearish_harami(c2, c1): bear.append(("看跌孕线", 1))
        if self._is_evening_star(c3, c2, c1): bear.append(("黄昏之星", 2.5))
        if self._is_three_black_crows(c3, c2, c1): bear.append(("三只乌鸦", 2))
        if self._is_bearish(c1) and c1["open"] > max(ma5, ma10, ma20) and c1["close"] < min(ma5, ma10, ma20):
            bear.append(("一阴穿三线", 2))
        if n >= 12:
            bl = min(c["low"] for c in candles[-11:-1])
            if c1["close"] < bl and self._is_bearish(c1): bear.append(("箱体跌破", 2))

        info["bullish_patterns"] = [p[0] for p in bull]
        info["bearish_patterns"] = [p[0] for p in bear]
        info["price_position"] = f"{'低位' if is_low else '高位' if is_high else '中位'}({pos:.0%})"

        # ===== FVG回踩入场(优先) =====
        for fvg in self._pending_fvg:
            in_zone = fvg["bottom"] <= price <= fvg["top"]
            if fvg["type"] == "bullish" and in_zone and not has_position:
                if bull:
                    best = max(bull, key=lambda x: x[1])
                    info["reason"] = f"看涨FVG回踩({fvg['bottom']:.2f}~{fvg['top']:.2f})+{best[0]}确认"
                    logger.info(f"[K线形态] FVG入场: {info['reason']}")
                    self.last_signal = Signal.BUY; self.signal_time = time.time()
                    return Signal.BUY, info
                elif is_low:
                    info["reason"] = f"看涨FVG回踩({fvg['bottom']:.2f}~{fvg['top']:.2f}), 低位企稳"
                    logger.info(f"[K线形态] FVG入场: {info['reason']}")
                    self.last_signal = Signal.BUY; self.signal_time = time.time()
                    return Signal.BUY, info
            elif fvg["type"] == "bearish" and in_zone and not has_position:
                if bear:
                    best = max(bear, key=lambda x: x[1])
                    info["reason"] = f"看跌FVG回踩({fvg['bottom']:.2f}~{fvg['top']:.2f})+{best[0]}确认"
                    logger.info(f"[K线形态] FVG入场: {info['reason']}")
                    self.last_signal = Signal.SELL; self.signal_time = time.time()
                    return Signal.SELL, info
                elif is_high:
                    info["reason"] = f"看跌FVG回踩({fvg['bottom']:.2f}~{fvg['top']:.2f}), 高位遇阻"
                    logger.info(f"[K线形态] FVG入场: {info['reason']}")
                    self.last_signal = Signal.SELL; self.signal_time = time.time()
                    return Signal.SELL, info

        # ===== 无持仓: 形态信号 =====
        if not has_position:
            if bull:
                best = max(bull, key=lambda x: x[1])
                if best[1] >= 1.5:
                    info["reason"] = f"看涨形态: {best[0]}(强度{best[1]})"
                    logger.info(f"[K线形态] 看涨: {best[0]} | 全部: {[p[0] for p in bull]}")
                    self.last_signal = Signal.BUY; self.signal_time = time.time()
                    return Signal.BUY, info
            if bear:
                best = max(bear, key=lambda x: x[1])
                if best[1] >= 1.5:
                    info["reason"] = f"看跌形态: {best[0]}(强度{best[1]})"
                    logger.info(f"[K线形态] 看跌: {best[0]} | 全部: {[p[0] for p in bear]}")
                    self.last_signal = Signal.SELL; self.signal_time = time.time()
                    return Signal.SELL, info
            if bull or bear:
                info["reason"] = f"形态强度不足(看涨:{[p[0] for p in bull]}, 看跌:{[p[0] for p in bear]}), 等待确认"
            else:
                info["reason"] = "无明显K线形态, 等待"
            self.last_signal = Signal.HOLD; self.signal_time = time.time()
            return Signal.HOLD, info

        # ===== 有持仓: 反向形态平仓 =====
        if has_position and position_side == "long":
            if bear:
                best = max(bear, key=lambda x: x[1])
                if best[1] >= 1.5:
                    info["reason"] = f"看跌形态平多: {best[0]}(强度{best[1]})"
                    logger.info(f"[K线形态] 平多: {best[0]}")
                    self.last_signal = Signal.CLOSE_LONG; self.signal_time = time.time()
                    return Signal.CLOSE_LONG, info
            info["reason"] = "持有多单, 无平仓形态"
            self.last_signal = Signal.HOLD; self.signal_time = time.time()
            return Signal.HOLD, info

        if has_position and position_side == "short":
            if bull:
                best = max(bull, key=lambda x: x[1])
                if best[1] >= 1.5:
                    info["reason"] = f"看涨形态平空: {best[0]}(强度{best[1]})"
                    logger.info(f"[K线形态] 平空: {best[0]}")
                    self.last_signal = Signal.CLOSE_SHORT; self.signal_time = time.time()
                    return Signal.CLOSE_SHORT, info
            info["reason"] = "持有空单, 无平仓形态"
            self.last_signal = Signal.HOLD; self.signal_time = time.time()
            return Signal.HOLD, info

        info["reason"] = "无操作"
        self.last_signal = Signal.HOLD; self.signal_time = time.time()
        return Signal.HOLD, info


# ===== 17. 智能关键位策略(多时间框架+支撑阻力+等价格入场) =====
class SmartKeyLevelStrategy(BaseStrategy):
    """
    智能关键位策略
    - 多时间框架: 高周期判断大趋势+关键位, 当前周期等价格入场
    - 自动识别支撑位/阻力位/关键位(摆动高低点+前期高低点+均线+整数关口)
    - 自动判断做多做空: 上涨趋势等回调到支撑位开多, 下跌趋势等反弹到阻力位开空
    - 等价格到达才入场, 不追高
    - 入场后不再加仓, 只做仓位管理(止损止盈由trader处理)
    - 趋势反转时平仓, 等价格到达新关键位再反向开仓
    """
    def __init__(self):
        super().__init__("smart_key_level")
        self._high_tf_map = {
            "1m": "5m", "3m": "15m", "5m": "15m", "15m": "1h",
            "30m": "1h", "1h": "4h", "2h": "4h", "4h": "1d", "1d": "1w",
        }
        self._trend = "neutral"
        self._supports: List[float] = []
        self._resistances: List[float] = []
        self._pending_entry: Optional[Dict[str, Any]] = None
        self._last_high_tf_update: float = 0
        self._high_tf_cache: Optional[Dict[str, Any]] = None

    def _get_high_tf(self, current_tf: str) -> str:
        # 统一转小写, 兼容用户配置大写(如1H)和小写(如1h)
        return self._high_tf_map.get(current_tf.lower(), "1h")

    def _fetch_high_tf_ohlcv(self, current_tf: str) -> Optional[List[List[float]]]:
        """获取高周期K线(延迟导入okx_client避免循环导入)"""
        try:
            from okx_client import okx_client
            from config import config
            high_tf = self._get_high_tf(current_tf)
            candles = okx_client.get_ohlcv(timeframe=high_tf, limit=100)
            return candles
        except Exception as e:
            logger.warning(f"[智能关键位] 获取高周期K线失败: {e}, 只用当前周期")
            return None

    def _detect_swing_points(self, candles: List[Dict], left: int = 3, right: int = 3) -> Tuple[List[float], List[float]]:
        """识别摆动高点和摆动低点"""
        swing_highs = []
        swing_lows = []
        n = len(candles)
        for i in range(left, n - right):
            c = candles[i]
            # 摆动高点: 左边left根高点都低于当前高点, 右边right根高点都低于当前高点
            is_high = all(candles[j]["high"] <= c["high"] for j in range(i - left, i)) and \
                      all(candles[j]["high"] <= c["high"] for j in range(i + 1, i + right + 1))
            if is_high:
                swing_highs.append(c["high"])
            # 摆动低点
            is_low = all(candles[j]["low"] >= c["low"] for j in range(i - left, i)) and \
                     all(candles[j]["low"] >= c["low"] for j in range(i + 1, i + right + 1))
            if is_low:
                swing_lows.append(c["low"])
        return swing_highs, swing_lows

    def _detect_key_levels(self, candles: List[Dict], current_price: float) -> Tuple[List[float], List[float]]:
        """识别关键位: 支撑位和阻力位"""
        supports = []
        resistances = []

        # 1. 摆动高低点
        swing_highs, swing_lows = self._detect_swing_points(candles)
        for h in swing_highs:
            if h > current_price:
                resistances.append(h)
            else:
                supports.append(h)
        for l in swing_lows:
            if l < current_price:
                supports.append(l)
            else:
                resistances.append(l)

        # 2. 前期高低点(最近20根和50根)
        if len(candles) >= 20:
            recent20_high = max(c["high"] for c in candles[-20:])
            recent20_low = min(c["low"] for c in candles[-20:])
            if recent20_high > current_price:
                resistances.append(recent20_high)
            else:
                supports.append(recent20_high)
            if recent20_low < current_price:
                supports.append(recent20_low)
            else:
                resistances.append(recent20_low)
        if len(candles) >= 50:
            recent50_high = max(c["high"] for c in candles[-50:])
            recent50_low = min(c["low"] for c in candles[-50:])
            if recent50_high > current_price:
                resistances.append(recent50_high)
            if recent50_low < current_price:
                supports.append(recent50_low)

        # 3. 均线支撑/阻力
        closes = [c["close"] for c in candles]
        if len(closes) >= 20:
            ma20 = sum(closes[-20:]) / 20
            if ma20 < current_price:
                supports.append(ma20)
            else:
                resistances.append(ma20)
        if len(closes) >= 50:
            ma50 = sum(closes[-50:]) / 50
            if ma50 < current_price:
                supports.append(ma50)
            else:
                resistances.append(ma50)

        # 4. 整数关口
        step = 100 if current_price > 10000 else (10 if current_price > 100 else 1)
        for i in range(-3, 4):
            level = (int(current_price / step) + i) * step
            if level > 0:
                if level < current_price:
                    supports.append(level)
                else:
                    resistances.append(level)

        # 去重和排序(距离太近的合并)
        supports = self._merge_levels(sorted(set(supports), reverse=True))
        resistances = self._merge_levels(sorted(set(resistances)))
        return supports, resistances

    def _merge_levels(self, levels: List[float], threshold_pct: float = 0.005) -> List[float]:
        """合并距离太近的关键位"""
        if not levels:
            return []
        merged = [levels[0]]
        for lv in levels[1:]:
            if abs(lv - merged[-1]) / merged[-1] > threshold_pct:
                merged.append(lv)
        return merged

    def _detect_trend(self, candles: List[Dict], current_price: float) -> str:
        """判断大趋势: up/down/neutral"""
        if len(candles) < 50:
            return "neutral"
        closes = [c["close"] for c in candles]
        ma20 = sum(closes[-20:]) / 20
        ma50 = sum(closes[-50:]) / 50
        ma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else ma50

        # 摆动高低点判断趋势结构
        swing_highs, swing_lows = self._detect_swing_points(candles, left=5, right=5)
        higher_highs = len(swing_highs) >= 2 and swing_highs[-1] > swing_highs[-2]
        higher_lows = len(swing_lows) >= 2 and swing_lows[-1] > swing_lows[-2]
        lower_highs = len(swing_highs) >= 2 and swing_highs[-1] < swing_highs[-2]
        lower_lows = len(swing_lows) >= 2 and swing_lows[-1] < swing_lows[-2]

        if (current_price > ma200 and ma20 > ma50 and higher_highs and higher_lows) or \
           (current_price > ma200 and ma20 > ma50):
            return "up"
        elif (current_price < ma200 and ma20 < ma50 and lower_highs and lower_lows) or \
             (current_price < ma200 and ma20 < ma50):
            return "down"
        return "neutral"

    def _find_nearest_level(self, price: float, levels: List[float], direction: str) -> Optional[float]:
        """找最近的关键位
        direction: 'below' 找价格下方最近的, 'above' 找价格上方最近的
        """
        if direction == "below":
            below = [l for l in levels if l < price]
            return max(below) if below else None
        else:
            above = [l for l in levels if l > price]
            return min(above) if above else None

    def _check_kline_confirm(self, candles: List[Dict], direction: str) -> Tuple[bool, str]:
        """检查当前K线形态是否确认方向(二次确认, 提高准确率)
        direction: 'long' 检查看涨形态, 'short' 检查看跌形态
        覆盖形态: 吞没/锤头/倒锤头/早晨之星/红三兵/刺透/孕线/上升三法/光头光脚/多方炮/出水芙蓉
        返回: (是否确认, 形态名称)
        """
        if len(candles) < 3:
            return True, "数据不足默认确认"
        c1, c2, c3 = candles[-1], candles[-2], candles[-3]
        body = lambda c: abs(c["close"] - c["open"])
        is_bull = lambda c: c["close"] > c["open"]
        is_bear = lambda c: c["close"] < c["open"]
        upper_shadow = lambda c: c["high"] - max(c["open"], c["close"])
        lower_shadow = lambda c: min(c["open"], c["close"]) - c["low"]
        if direction == "long":
            # 看涨吞没
            if is_bear(c2) and is_bull(c1) and c1["open"] <= c2["close"] and c1["close"] >= c2["open"]:
                return True, "看涨吞没"
            # 锤头线(低位, 下影线长)
            if body(c1) > 0 and lower_shadow(c1) >= body(c1) * 2 and is_bull(c1):
                return True, "锤头线"
            # 倒锤头(低位, 上影线长, 收盘看涨)
            if body(c1) > 0 and upper_shadow(c1) >= body(c1) * 2 and is_bull(c1) and lower_shadow(c1) < body(c1):
                return True, "倒锤头"
            # 早晨之星
            if is_bear(c3) and body(c2) < body(c3) * 0.3 and is_bull(c1) and c1["close"] > (c3["open"] + c3["close"]) / 2:
                return True, "早晨之星"
            # 红三兵
            if is_bull(c1) and is_bull(c2) and is_bull(c3) and c1["close"] > c2["close"] > c3["close"]:
                return True, "红三兵"
            # 刺透形态
            if is_bear(c2) and is_bull(c1) and c1["open"] < c2["close"] and c1["close"] > (c2["open"] + c2["close"]) / 2:
                return True, "刺透形态"
            # 看涨孕线(前阴后阳, 实体被包含)
            if is_bear(c2) and is_bull(c1) and body(c1) < body(c2) and c1["open"] >= c2["close"] and c1["close"] <= c2["open"]:
                return True, "看涨孕线"
            # 光头光脚阳线(无上下影线, 强势)
            if is_bull(c1) and upper_shadow(c1) < body(c1) * 0.1 and lower_shadow(c1) < body(c1) * 0.1 and body(c1) > 0:
                return True, "光头光脚阳线"
            # 多方炮(两阳夹一阴)
            if is_bull(c3) and is_bear(c2) and is_bull(c1) and c1["close"] > c2["open"] and c3["close"] > c2["close"]:
                return True, "多方炮"
            # 上升三法(阳-三阴-阳, 后阳创新高)
            if len(candles) >= 5 and is_bull(candles[-5]) and all(is_bear(candles[i]) for i in range(-4, -1)) and is_bull(c1) and c1["close"] > candles[-5]["close"]:
                return True, "上升三法"
            # 出水芙蓉(一阳穿多均线, 需要ma数据, 用近3根K线近似)
            if is_bull(c1) and c1["open"] < c2["low"] and c1["close"] > c2["high"] and body(c1) > body(c2) * 1.5:
                return True, "出水芙蓉"
            return False, "无看涨确认形态"
        else:
            # 看跌吞没
            if is_bull(c2) and is_bear(c1) and c1["open"] >= c2["close"] and c1["close"] <= c2["open"]:
                return True, "看跌吞没"
            # 射击之星(高位, 上影线长)
            if body(c1) > 0 and upper_shadow(c1) >= body(c1) * 2 and is_bear(c1):
                return True, "射击之星"
            # 上吊线(高位, 下影线长, 收盘看跌)
            if body(c1) > 0 and lower_shadow(c1) >= body(c1) * 2 and is_bear(c1) and upper_shadow(c1) < body(c1):
                return True, "上吊线"
            # 黄昏之星
            if is_bull(c3) and body(c2) < body(c3) * 0.3 and is_bear(c1) and c1["close"] < (c3["open"] + c3["close"]) / 2:
                return True, "黄昏之星"
            # 三只乌鸦
            if is_bear(c1) and is_bear(c2) and is_bear(c3) and c1["close"] < c2["close"] < c3["close"]:
                return True, "三只乌鸦"
            # 乌云盖顶
            if is_bull(c2) and is_bear(c1) and c1["open"] > c2["close"] and c1["close"] < (c2["open"] + c2["close"]) / 2:
                return True, "乌云盖顶"
            # 看跌孕线
            if is_bull(c2) and is_bear(c1) and body(c1) < body(c2) and c1["open"] <= c2["close"] and c1["close"] >= c2["open"]:
                return True, "看跌孕线"
            # 光头光脚阴线
            if is_bear(c1) and upper_shadow(c1) < body(c1) * 0.1 and lower_shadow(c1) < body(c1) * 0.1 and body(c1) > 0:
                return True, "光头光脚阴线"
            # 空方炮(两阴夹一阳)
            if is_bear(c3) and is_bull(c2) and is_bear(c1) and c1["close"] < c2["open"] and c3["close"] < c2["close"]:
                return True, "空方炮"
            # 下降三法(阴-三阳-阴, 后阴创新低)
            if len(candles) >= 5 and is_bear(candles[-5]) and all(is_bull(candles[i]) for i in range(-4, -1)) and is_bear(c1) and c1["close"] < candles[-5]["close"]:
                return True, "下降三法"
            # 断头铡刀(一阴穿多K线)
            if is_bear(c1) and c1["open"] > c2["high"] and c1["close"] < c2["low"] and body(c1) > body(c2) * 1.5:
                return True, "断头铡刀"
            return False, "无看跌确认形态"

    def _calc_confidence(self, trend: str, direction: str, level: float, levels: List[float],
                          kline_confirm: bool, volume_ratio: float, price_distance_pct: float) -> int:
        """计算信号置信度0-100分
        打分维度: 趋势一致(25) + 关键位强度(25) + K线形态确认(25) + 成交量(15) + 价格精确(10)
        """
        score = 0
        # 1. 趋势一致(25分)
        if (direction == "long" and trend == "up") or (direction == "short" and trend == "down"):
            score += 25
        elif trend == "neutral":
            score += 12
        # 2. 关键位强度(25分): 关键位在列表中排越前(越近/越重要)分越高
        if levels:
            rank = levels.index(level) if level in levels else len(levels)
            if rank == 0:
                score += 25  # 最近最强关键位
            elif rank == 1:
                score += 18
            elif rank <= 3:
                score += 12
            else:
                score += 6
        # 3. K线形态确认(25分)
        if kline_confirm:
            score += 25
        # 4. 成交量(15分): 放量加分
        if volume_ratio >= 1.5:
            score += 15
        elif volume_ratio >= 1.2:
            score += 10
        elif volume_ratio >= 1.0:
            score += 5
        # 5. 价格精确到达(10分): 距离关键位越近分越高
        if price_distance_pct <= 0.1:
            score += 10
        elif price_distance_pct <= 0.2:
            score += 7
        elif price_distance_pct <= 0.3:
            score += 4
        return min(100, max(0, score))

    def generate_signal(self, ohlcv, has_position=False, position_side=None):
        if len(ohlcv) < 30:
            return Signal.HOLD, {"reason": "K线数据不足(需要至少30根)"}

        df = self._to_dataframe(ohlcv)
        candles = df.reset_index().to_dict("records")
        current_price = candles[-1]["close"]
        # closed_price = 已收盘K线的收盘价(倒数第二根, 最后一根是当前正在形成的K线)
        # 用收盘价做入场判断, 避免盘中插针误触发
        closed_price = candles[-2]["close"] if len(candles) >= 2 else current_price
        info = {"price": round(current_price, 2), "strategy": "smart_key_level", "closed_price": round(closed_price, 2)}

        # ===== 有持仓时: 只检查趋势反转, 不做任何开仓 =====
        if has_position:
            # 定期更新高周期趋势(每10轮更新一次, 避免频繁请求)
            now = time.time()
            if now - self._last_high_tf_update > 300 or not self._high_tf_cache:
                from config import config
                high_candles = self._fetch_high_tf_ohlcv(config.trading.timeframe)
                if high_candles and len(high_candles) >= 30:
                    hdf = self._to_dataframe(high_candles)
                    hcandles = hdf.reset_index().to_dict("records")
                    self._trend = self._detect_trend(hcandles, current_price)
                    self._supports, self._resistances = self._detect_key_levels(hcandles, current_price)
                    self._last_high_tf_update = now
                    self._high_tf_cache = {"trend": self._trend, "supports": self._supports, "resistances": self._resistances}
                    logger.info(f"[智能关键位] 高周期更新 | 趋势={self._trend} | 支撑位={[round(s,2) for s in self._supports[:3]]} | 阻力位={[round(r,2) for r in self._resistances[:3]]}")

            info["trend"] = self._trend
            info["supports"] = [round(s, 2) for s in self._supports[:5]]
            info["resistances"] = [round(r, 2) for r in self._resistances[:5]]

            # 趋势反转检查: 持有多单但大趋势转跌 → 平多
            if position_side == "long" and self._trend == "down":
                info["reason"] = f"大趋势转为下跌, 平多(趋势={self._trend})"
                logger.info(f"[智能关键位] 趋势反转平仓: {info['reason']}")
                self._pending_entry = None
                self.last_signal = Signal.CLOSE_LONG; self.signal_time = time.time()
                return Signal.CLOSE_LONG, info
            # 持有空单但大趋势转涨 → 平空
            if position_side == "short" and self._trend == "up":
                info["reason"] = f"大趋势转为上涨, 平空(趋势={self._trend})"
                logger.info(f"[智能关键位] 趋势反转平仓: {info['reason']}")
                self._pending_entry = None
                self.last_signal = Signal.CLOSE_SHORT; self.signal_time = time.time()
                return Signal.CLOSE_SHORT, info

            info["reason"] = f"持仓中, 大趋势={self._trend}, 做好仓位管理"
            self.last_signal = Signal.HOLD; self.signal_time = time.time()
            return Signal.HOLD, info

        # ===== 无持仓时: 更新高周期数据, 等价格到达关键位入场 =====
        now = time.time()
        if now - self._last_high_tf_update > 300 or not self._high_tf_cache:
            from config import config
            high_candles = self._fetch_high_tf_ohlcv(config.trading.timeframe)
            if high_candles and len(high_candles) >= 30:
                hdf = self._to_dataframe(high_candles)
                hcandles = hdf.reset_index().to_dict("records")
                self._trend = self._detect_trend(hcandles, current_price)
                self._supports, self._resistances = self._detect_key_levels(hcandles, current_price)
                self._last_high_tf_update = now
                self._high_tf_cache = {"trend": self._trend, "supports": self._supports, "resistances": self._resistances}
                logger.info(f"[智能关键位] 高周期更新 | 趋势={self._trend} | 支撑位={[round(s,2) for s in self._supports[:3]]} | 阻力位={[round(r,2) for r in self._resistances[:3]]}")
            else:
                # 高周期获取失败, 用当前周期
                self._trend = self._detect_trend(candles, current_price)
                self._supports, self._resistances = self._detect_key_levels(candles, current_price)
                self._last_high_tf_update = now
                logger.info(f"[智能关键位] 用当前周期 | 趋势={self._trend} | 支撑位={[round(s,2) for s in self._supports[:3]]} | 阻力位={[round(r,2) for r in self._resistances[:3]]}")

        info["trend"] = self._trend
        info["supports"] = [round(s, 2) for s in self._supports[:5]]
        info["resistances"] = [round(r, 2) for r in self._resistances[:5]]

        # 计算K线形态二次确认和成交量(提高准确率)
        from config import config as _cfg
        bull_confirm, bull_pattern = self._check_kline_confirm(candles, "long")
        bear_confirm, bear_pattern = self._check_kline_confirm(candles, "short")
        # 成交量比率
        vol_ratio = 1.0
        if len(candles) >= 6:
            vol_ma5 = sum(c["volume"] for c in candles[-6:-1]) / 5
            vol_ratio = candles[-1]["volume"] / vol_ma5 if vol_ma5 > 0 else 1.0
        info["bull_confirm"] = bull_confirm
        info["bear_confirm"] = bear_confirm
        info["bull_pattern"] = bull_pattern
        info["bear_pattern"] = bear_pattern
        info["volume_ratio"] = round(vol_ratio, 2)

        # 容差: 收盘价到达关键位±0.5%就算到达(用收盘价, 不是盘中价)
        tolerance = 0.005
        # 回踩深度限制: 偏离关键位超过2%直接放弃(不追单, 不抄底)
        max_deviation = 0.02

        # ===== 实时检测状态日志(每轮都打印, 让用户看到机器人在做什么) =====
        _near_sup = self._find_nearest_level(closed_price, self._supports, "below")
        _near_res = self._find_nearest_level(closed_price, self._resistances, "above")
        _sup_dist = f"{(closed_price - _near_sup)/_near_sup*100:.2f}%" if _near_sup else "无"
        _res_dist = f"{(_near_res - closed_price)/closed_price*100:.2f}%" if _near_res else "无"
        # 实时K线形态检测
        _bull_ok, _bull_name = bull_confirm, bull_pattern
        _bear_ok, _bear_name = bear_confirm, bear_pattern
        _kline_text = f"看涨形态={_bull_name if _bull_ok else '无'} | 看跌形态={_bear_name if _bear_ok else '无'}"
        # 实时开多开空信号判断
        if self._trend == "up" and _near_sup and abs(closed_price - _near_sup) / _near_sup <= tolerance:
            _signal_text = "✅到达支撑位, 满足开多条件(等K线确认)"
        elif self._trend == "down" and _near_res and abs(closed_price - _near_res) / _near_res <= tolerance:
            _signal_text = "✅到达阻力位, 满足开空条件(等K线确认)"
        elif self._trend == "up":
            _signal_text = "⏳上涨趋势, 等待回调到支撑位开多"
        elif self._trend == "down":
            _signal_text = "⏳下跌趋势, 等待反弹到阻力位开空"
        else:
            _signal_text = "⏳震荡趋势, 等待到达支撑/阻力位"
        logger.info(f"[智能关键位] [实时检测] 现价={current_price:.2f} | 收盘={closed_price:.2f} | 趋势={self._trend} | {_kline_text} | {_signal_text} | 最近支撑={_near_sup}({_sup_dist}) | 最近阻力={_near_res}({_res_dist}) | 量比={vol_ratio:.2f}")

        trend_text = {"up": "上涨", "down": "下跌", "neutral": "震荡"}.get(self._trend, self._trend)

        # ===== 自动判断方向, 等价格到达关键位入场 =====
        if self._trend == "up":
            # 上涨趋势: 等回调到支撑位开多(用已收盘K线收盘价判断, 避免插针误触发)
            nearest_support = self._find_nearest_level(closed_price, self._supports, "below")
            if nearest_support:
                distance_pct = (closed_price - nearest_support) / nearest_support * 100
                info["entry_target"] = round(nearest_support, 2)
                info["entry_direction"] = "做多"
                # 回踩深度校验: 收盘价偏离支撑位超过2%直接放弃(不抄底, 不追单)
                if distance_pct > max_deviation * 100:
                    info["reason"] = f"上涨趋势, 但收盘价{closed_price:.2f}偏离支撑位{nearest_support:.2f}达{distance_pct:.2f}%(超过{max_deviation*100:.0f}%), 放弃信号, 等待回调到位"
                    logger.info(f"[智能关键位] [回踩过深] {info['reason']}")
                    self.last_signal = Signal.HOLD; self.signal_time = time.time()
                    return Signal.HOLD, info
                if abs(closed_price - nearest_support) / nearest_support <= tolerance:
                    # 置信度计算 + K线形态二次确认
                    conf = self._calc_confidence(self._trend, "long", nearest_support, self._supports, bull_confirm, vol_ratio, distance_pct)
                    info["confidence"] = conf
                    info["reason"] = f"上涨趋势, 价格回调到支撑位{nearest_support:.2f}(距离{distance_pct:.2f}%), 置信度{conf}分, 自动开多"
                    if _cfg.risk.high_tf_confirm and not bull_confirm:
                        info["reason"] = f"价格到达支撑位但无看涨K线确认({bull_pattern}), 继续等待 | 置信度{conf}分"
                        logger.info(f"[智能关键位] 等待K线确认: {info['reason']}")
                        self.last_signal = Signal.HOLD; self.signal_time = time.time()
                        return Signal.HOLD, info
                    logger.info(f"[智能关键位] 入场信号: {info['reason']} | K线确认={bull_pattern}")
                    self._pending_entry = None
                    self.last_signal = Signal.BUY; self.signal_time = time.time()
                    return Signal.BUY, info
                else:
                    info["reason"] = f"上涨趋势, 等待收盘价回调到支撑位{nearest_support:.2f}(收盘{closed_price:.2f}/现价{current_price:.2f}, 还差{distance_pct:.2f}%)"
                    logger.info(f"[智能关键位] 等待入场: {info['reason']}")
                    self.last_signal = Signal.HOLD; self.signal_time = time.time()
                    return Signal.HOLD, info
            else:
                info["reason"] = "上涨趋势, 但未识别到有效支撑位, 等待"
                self.last_signal = Signal.HOLD; self.signal_time = time.time()
                return Signal.HOLD, info

        elif self._trend == "down":
            # 下跌趋势: 等反弹到阻力位开空(用已收盘K线收盘价判断)
            nearest_resistance = self._find_nearest_level(closed_price, self._resistances, "above")
            if nearest_resistance:
                distance_pct = (nearest_resistance - closed_price) / closed_price * 100
                info["entry_target"] = round(nearest_resistance, 2)
                info["entry_direction"] = "做空"
                # 反弹深度校验: 收盘价偏离阻力位超过2%直接放弃(不追空)
                if distance_pct > max_deviation * 100:
                    info["reason"] = f"下跌趋势, 但收盘价{closed_price:.2f}偏离阻力位{nearest_resistance:.2f}达{distance_pct:.2f}%(超过{max_deviation*100:.0f}%), 放弃信号, 等待反弹到位"
                    logger.info(f"[智能关键位] [反弹过深] {info['reason']}")
                    self.last_signal = Signal.HOLD; self.signal_time = time.time()
                    return Signal.HOLD, info
                if abs(closed_price - nearest_resistance) / nearest_resistance <= tolerance:
                    conf = self._calc_confidence(self._trend, "short", nearest_resistance, self._resistances, bear_confirm, vol_ratio, distance_pct)
                    info["confidence"] = conf
                    info["reason"] = f"下跌趋势, 价格反弹到阻力位{nearest_resistance:.2f}(距离{distance_pct:.2f}%), 置信度{conf}分, 自动开空"
                    if _cfg.risk.high_tf_confirm and not bear_confirm:
                        info["reason"] = f"价格到达阻力位但无看跌K线确认({bear_pattern}), 继续等待 | 置信度{conf}分"
                        logger.info(f"[智能关键位] 等待K线确认: {info['reason']}")
                        self.last_signal = Signal.HOLD; self.signal_time = time.time()
                        return Signal.HOLD, info
                    logger.info(f"[智能关键位] 入场信号: {info['reason']} | K线确认={bear_pattern}")
                    self._pending_entry = None
                    self.last_signal = Signal.SELL; self.signal_time = time.time()
                    return Signal.SELL, info
                else:
                    info["reason"] = f"下跌趋势, 等待收盘价反弹到阻力位{nearest_resistance:.2f}(收盘{closed_price:.2f}/现价{current_price:.2f}, 还差{distance_pct:.2f}%)"
                    logger.info(f"[智能关键位] 等待入场: {info['reason']}")
                    self.last_signal = Signal.HOLD; self.signal_time = time.time()
                    return Signal.HOLD, info
            else:
                info["reason"] = "下跌趋势, 但未识别到有效阻力位, 等待"
                self.last_signal = Signal.HOLD; self.signal_time = time.time()
                return Signal.HOLD, info

        else:
            # 震荡趋势: 到支撑位开多, 到阻力位开空
            nearest_support = self._find_nearest_level(closed_price, self._supports, "below")
            nearest_resistance = self._find_nearest_level(closed_price, self._resistances, "above")
            if nearest_support and abs(closed_price - nearest_support) / nearest_support <= tolerance:
                info["entry_target"] = round(nearest_support, 2)
                info["entry_direction"] = "做多"
                dist_s = abs(closed_price - nearest_support) / nearest_support * 100
                conf = self._calc_confidence(self._trend, "long", nearest_support, self._supports, bull_confirm, vol_ratio, dist_s)
                info["confidence"] = conf
                info["reason"] = f"震荡趋势, 价格到达支撑位{nearest_support:.2f}, 置信度{conf}分, 自动开多"
                if _cfg.risk.high_tf_confirm and not bull_confirm:
                    info["reason"] = f"价格到达支撑位但无看涨K线确认({bull_pattern}), 继续等待 | 置信度{conf}分"
                    logger.info(f"[智能关键位] 等待K线确认: {info['reason']}")
                    self.last_signal = Signal.HOLD; self.signal_time = time.time()
                    return Signal.HOLD, info
                logger.info(f"[智能关键位] 入场信号: {info['reason']} | K线确认={bull_pattern}")
                self.last_signal = Signal.BUY; self.signal_time = time.time()
                return Signal.BUY, info
            if nearest_resistance and abs(closed_price - nearest_resistance) / nearest_resistance <= tolerance:
                info["entry_target"] = round(nearest_resistance, 2)
                info["entry_direction"] = "做空"
                dist_r = abs(closed_price - nearest_resistance) / nearest_resistance * 100
                conf = self._calc_confidence(self._trend, "short", nearest_resistance, self._resistances, bear_confirm, vol_ratio, dist_r)
                info["confidence"] = conf
                info["reason"] = f"震荡趋势, 价格到达阻力位{nearest_resistance:.2f}, 置信度{conf}分, 自动开空"
                if _cfg.risk.high_tf_confirm and not bear_confirm:
                    info["reason"] = f"价格到达阻力位但无看跌K线确认({bear_pattern}), 继续等待 | 置信度{conf}分"
                    logger.info(f"[智能关键位] 等待K线确认: {info['reason']}")
                    self.last_signal = Signal.HOLD; self.signal_time = time.time()
                    return Signal.HOLD, info
                logger.info(f"[智能关键位] 入场信号: {info['reason']} | K线确认={bear_pattern}")
                self.last_signal = Signal.SELL; self.signal_time = time.time()
                return Signal.SELL, info
            # 都没到, 等最近的那个
            if nearest_support and nearest_resistance:
                dist_support = (closed_price - nearest_support) / nearest_support * 100
                dist_resistance = (nearest_resistance - closed_price) / closed_price * 100
                if dist_support < dist_resistance:
                    info["reason"] = f"震荡趋势, 等待价格到支撑位{nearest_support:.2f}开多(还差{dist_support:.2f}%)"
                else:
                    info["reason"] = f"震荡趋势, 等待价格到阻力位{nearest_resistance:.2f}开空(还差{dist_resistance:.2f}%)"
            elif nearest_support:
                info["reason"] = f"震荡趋势, 等待价格到支撑位{nearest_support:.2f}开多"
            elif nearest_resistance:
                info["reason"] = f"震荡趋势, 等待价格到阻力位{nearest_resistance:.2f}开空"
            else:
                info["reason"] = "震荡趋势, 未识别到有效关键位, 等待"
            logger.info(f"[智能关键位] 等待入场: {info['reason']}")
            self.last_signal = Signal.HOLD; self.signal_time = time.time()
            return Signal.HOLD, info


# ===== 策略工厂 =====
_STRATEGY_REGISTRY = {
    "ma_cross": MACrossStrategy,
    "ema_cross": EMACrossStrategy,
    "triple_ma": TripleMAStrategy,
    "macd": MACDStrategy,
    "rsi": RSIStrategy,
    "kdj": KDJStrategy,
    "bollinger": BollingerStrategy,
    "mean_reversion": MeanReversionStrategy,
    "cci": CCIStrategy,
    "turtle": TurtleStrategy,
    "donchian": DonchianStrategy,
    "atr_breakout": ATRBreakoutStrategy,
    "momentum": MomentumStrategy,
    "volume": VolumeStrategy,
    "grid": GridStrategy,
    "kline_pattern": KlinePatternStrategy,
    "smart_key_level": SmartKeyLevelStrategy,
}


def create_strategy(name: str, **kwargs) -> BaseStrategy:
    if name not in _STRATEGY_REGISTRY:
        raise ValueError(f"未知策略: {name}, 可选: {list(_STRATEGY_REGISTRY.keys())}")
    return _STRATEGY_REGISTRY[name](**kwargs)


def list_strategies() -> List[Dict[str, str]]:
    return [
        {"name": "ma_cross", "desc": "双均线交叉(金叉买/死叉卖), 趋势行情"},
        {"name": "ema_cross", "desc": "EMA指数均线交叉, 比MA更灵敏, 趋势行情"},
        {"name": "triple_ma", "desc": "三均线多头/空头排列, 强趋势确认"},
        {"name": "macd", "desc": "MACD金叉死叉, 趋势+动量, 通用"},
        {"name": "rsi", "desc": "RSI超买超卖, 震荡行情"},
        {"name": "kdj", "desc": "KDJ随机指标, 低位金叉买/高位死叉卖, 震荡行情"},
        {"name": "bollinger", "desc": "布林带突破, 跌破下轨买/突破上轨卖, 震荡行情"},
        {"name": "mean_reversion", "desc": "均值回归, 价格偏离均线过远反向操作, 震荡行情"},
        {"name": "cci", "desc": "CCI顺势指标, 超卖回升买/超买回落卖"},
        {"name": "turtle", "desc": "海龟交易法则, 20日高点突破买/10日低点跌破卖, 趋势行情"},
        {"name": "donchian", "desc": "唐奇安通道突破, 趋势行情"},
        {"name": "atr_breakout", "desc": "ATR波动率突破, 大波动趋势跟踪"},
        {"name": "momentum", "desc": "动量策略, 收益率拐点追涨杀跌"},
        {"name": "volume", "desc": "成交量突破, 放量上涨买/放量下跌卖"},
        {"name": "grid", "desc": "网格交易, 震荡行情高抛低吸(仅做多, 不支持做空)"},
        {"name": "kline_pattern", "desc": "K线形态策略(FVG缺口回踩+吞没/锤头/早晨之星等20种形态, 自动识别多空, 等回调入场)"},
        {"name": "smart_key_level", "desc": "智能关键位策略(多时间框架+支撑阻力位识别+等价格到达才入场, 自动判断多空, 趋势反转才反向开仓)"},
        {"name": "ml_predict", "desc": "机器学习预测策略(随机森林模型, 预测未来涨跌概率, 需先在机器学习页面训练模型)"},
    ]


# ===== 机器学习预测策略注册(独立新增, 不改动原有策略) =====
try:
    from ml_predict_strategy import MLPredictStrategy
    _STRATEGY_REGISTRY["ml_predict"] = MLPredictStrategy
    logger.info("[策略注册] 机器学习预测策略(ml_predict)注册成功")
except ImportError as e:
    logger.warning(f"[策略注册] 机器学习策略未注册(依赖未安装): {e}")
except Exception as e:
    logger.warning(f"[策略注册] 机器学习策略注册失败: {e}")
