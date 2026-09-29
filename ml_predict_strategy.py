"""
机器学习预测策略(独立新增, 不改动原有代码)
模拟盘和实盘共用同一个策略类, 信号逻辑完全一致
唯一区别: 模拟盘用虚拟资金下单, 实盘调用OKX API真实下单
"""
import time
from typing import List, Dict, Any, Optional, Tuple
from loguru import logger

# 延迟导入, 避免循环导入
from strategy import BaseStrategy, Signal


class MLPredictStrategy(BaseStrategy):
    """机器学习预测策略
    用训练好的随机森林模型预测未来N根K线涨跌概率
    - 上涨概率 > 65% → 买入/开多
    - 下跌概率 > 65% → 卖出/开空
    - 有持仓时, 反向概率 > 60% → 平仓
    概率值直接作为置信度, 对接现有风控的signal_confidence_threshold
    """

    def __init__(self, symbol: Optional[str] = None):
        super().__init__("ml_predict")
        self._symbol = symbol
        self._last_predict: Optional[Dict[str, Any]] = None
        self._predict_count = 0
        self._model_missing_warned = False

    def _get_symbol(self) -> str:
        """获取当前交易币种"""
        if self._symbol:
            return self._symbol
        try:
            from config import config
            return config.trading.symbol
        except Exception:
            return "UNKNOWN"

    def generate_signal(self, ohlcv: List[List[float]],
                        has_position: bool = False,
                        position_side: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
        """生成交易信号
        Args:
            ohlcv: K线数据 [[timestamp, open, high, low, close, volume], ...]
            has_position: 是否有持仓
            position_side: 持仓方向 long/short
        Returns:
            (signal, info)
        """
        symbol = self._get_symbol()

        if len(ohlcv) < 60:
            info = {"reason": f"K线数据不足({len(ohlcv)}根), 至少需要60根", "strategy": "ml_predict"}
            self.last_signal = Signal.HOLD
            self.signal_time = time.time()
            return Signal.HOLD, info

        # 调用机器学习预测
        try:
            from ml_learning import predict, is_model_expired, get_model_meta
            result = predict(symbol, ohlcv)
        except Exception as e:
            logger.error(f"[ML策略] [{symbol}] 预测异常: {e}")
            info = {"reason": f"预测异常: {e}", "strategy": "ml_predict"}
            self.last_signal = Signal.HOLD
            self.signal_time = time.time()
            return Signal.HOLD, info

        self._last_predict = result
        self._predict_count += 1

        # 模型不存在或预测失败
        if "error" in result:
            if not self._model_missing_warned:
                logger.warning(f"[ML策略] [{symbol}] {result['error']}, 输出HOLD")
                self._model_missing_warned = True
            info = {
                "reason": result["error"],
                "strategy": "ml_predict",
                "suggestion": "请先在机器学习页面训练该币种模型",
            }
            self.last_signal = Signal.HOLD
            self.signal_time = time.time()
            return Signal.HOLD, info

        self._model_missing_warned = False

        up_prob = result.get("up_prob", 0.5)
        down_prob = result.get("down_prob", 0.5)
        confidence = result.get("confidence", 50)

        # 检查模型是否过期
        expired = is_model_expired(symbol)
        meta = get_model_meta(symbol)
        train_time = meta.get("train_time", "未知") if meta else "未知"

        info = {
            "strategy": "ml_predict",
            "symbol": symbol,
            "up_prob": up_prob,
            "down_prob": down_prob,
            "confidence": confidence,
            "predict_count": self._predict_count,
            "model_train_time": train_time,
            "model_expired": expired,
            "price": round(ohlcv[-1][4], 2) if ohlcv else 0,
        }

        # ===== 有持仓: 检查是否需要平仓 =====
        if has_position and position_side == "long":
            # 持有多单, 预测下跌概率高 → 平多
            if down_prob >= 0.60:
                info["reason"] = f"持有多单, 预测下跌概率{down_prob*100:.1f}% > 60%, 平多"
                logger.info(f"[ML策略] [{symbol}] {info['reason']}")
                self.last_signal = Signal.CLOSE_LONG
                self.signal_time = time.time()
                return Signal.CLOSE_LONG, info
            else:
                info["reason"] = f"持有多单, 上涨概率{up_prob*100:.1f}%, 继续持有"
                self.last_signal = Signal.HOLD
                self.signal_time = time.time()
                return Signal.HOLD, info

        if has_position and position_side == "short":
            # 持有空单, 预测上涨概率高 → 平空
            if up_prob >= 0.60:
                info["reason"] = f"持有空单, 预测上涨概率{up_prob*100:.1f}% > 60%, 平空"
                logger.info(f"[ML策略] [{symbol}] {info['reason']}")
                self.last_signal = Signal.CLOSE_SHORT
                self.signal_time = time.time()
                return Signal.CLOSE_SHORT, info
            else:
                info["reason"] = f"持有空单, 下跌概率{down_prob*100:.1f}%, 继续持有"
                self.last_signal = Signal.HOLD
                self.signal_time = time.time()
                return Signal.HOLD, info

        # ===== 无持仓: 检查是否需要开仓 =====
        if up_prob >= 0.65:
            info["reason"] = f"预测上涨概率{up_prob*100:.1f}% > 65%, 开多"
            if expired:
                info["reason"] += " (注意: 模型已过期, 建议重新训练)"
            logger.info(f"[ML策略] [{symbol}] {info['reason']} | 置信度{confidence}")
            self.last_signal = Signal.BUY
            self.signal_time = time.time()
            return Signal.BUY, info

        if down_prob >= 0.65:
            info["reason"] = f"预测下跌概率{down_prob*100:.1f}% > 65%, 开空"
            if expired:
                info["reason"] += " (注意: 模型已过期, 建议重新训练)"
            logger.info(f"[ML策略] [{symbol}] {info['reason']} | 置信度{confidence}")
            self.last_signal = Signal.SELL
            self.signal_time = time.time()
            return Signal.SELL, info

        # 概率不明确, 持有不动
        info["reason"] = f"上涨概率{up_prob*100:.1f}%, 下跌概率{down_prob*100:.1f}%, 概率不明确, 等待"
        self.last_signal = Signal.HOLD
        self.signal_time = time.time()
        return Signal.HOLD, info

    def get_last_predict(self) -> Optional[Dict[str, Any]]:
        """获取最近一次预测结果"""
        return self._last_predict
