"""
风控管理器
在每次交易前检查风险限制, 防止过度交易和大额亏损
"""
import time
from datetime import datetime, date
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field
from loguru import logger

from config import config


@dataclass
class TradeRecord:
    """交易记录"""
    id: str
    timestamp: float
    side: str  # buy / sell
    amount: float
    price: float
    pnl: float = 0.0  # 已实现盈亏
    reason: str = ""


class RiskManager:
    """风控管理器"""

    def __init__(self):
        self._daily_pnl: float = 0.0
        self._daily_trades: List[TradeRecord] = []
        self._current_date: str = date.today().isoformat()
        self._trading_enabled: bool = True
        self._open_positions_count: int = 0

    def _check_new_day(self):
        """检查是否跨日, 跨日则重置每日统计"""
        today = date.today().isoformat()
        if today != self._current_date:
            logger.info(f"新的一天 ({today}), 重置每日风控统计")
            self._current_date = today
            self._daily_pnl = 0.0
            self._daily_trades.clear()
            self._trading_enabled = True

    # ------------------------------------------------------------------
    # 交易前检查
    # ------------------------------------------------------------------
    def can_trade(self, side: str, amount_usdt: float) -> tuple[bool, str]:
        """检查是否允许交易

        Returns:
            (是否允许, 原因)
        """
        self._check_new_day()

        # 1. 全局开关
        if not self._trading_enabled:
            return False, "交易已被风控暂停(达到每日亏损上限)"

        # 2. 每日亏损上限
        if self._daily_pnl <= -config.risk.daily_max_loss:
            self._trading_enabled = False
            logger.warning(f"达到每日最大亏损 {config.risk.daily_max_loss} USDT, 停止今日交易")
            return False, f"已达每日最大亏损 {config.risk.daily_max_loss} USDT"

        # 3. 单笔亏损上限 (按止损价估算)
        estimated_loss = amount_usdt * config.risk.stop_loss_pct
        if estimated_loss > config.risk.max_loss_per_trade:
            logger.warning(f"单笔预估亏损 {estimated_loss:.2f} USDT 超过上限 {config.risk.max_loss_per_trade} USDT")
            return False, f"单笔预估亏损 {estimated_loss:.2f} 超过上限 {config.risk.max_loss_per_trade}"

        # 4. 持仓数量限制
        if self._open_positions_count >= config.risk.max_positions and side in ("buy", "sell"):
            # 开仓方向才检查, 平仓不检查
            return False, f"已达最大持仓数 {config.risk.max_positions}"

        return True, "通过"

    # ------------------------------------------------------------------
    # 交易记录
    # ------------------------------------------------------------------
    def record_trade(self, trade_id: str, side: str, amount: float,
                     price: float, reason: str = ""):
        """记录一笔交易"""
        record = TradeRecord(
            id=trade_id,
            timestamp=time.time(),
            side=side,
            amount=amount,
            price=price,
            reason=reason,
        )
        self._daily_trades.append(record)
        logger.info(f"交易记录: {side} {amount} @ {price} | {reason}")

    def record_pnl(self, pnl: float):
        """记录已实现盈亏"""
        self._daily_pnl += pnl
        logger.info(f"盈亏记录: {pnl:.2f} USDT | 今日累计: {self._daily_pnl:.2f} USDT")

    def update_position_count(self, count: int):
        """更新当前持仓数量"""
        self._open_positions_count = count

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------
    def get_status(self) -> Dict[str, Any]:
        """获取风控状态"""
        self._check_new_day()
        return {
            "trading_enabled": self._trading_enabled,
            "daily_pnl": round(self._daily_pnl, 4),
            "daily_trades_count": len(self._daily_trades),
            "daily_max_loss": config.risk.daily_max_loss,
            "daily_loss_remaining": round(config.risk.daily_max_loss + self._daily_pnl, 4),
            "open_positions": self._open_positions_count,
            "max_positions": config.risk.max_positions,
            "max_loss_per_trade": config.risk.max_loss_per_trade,
            "stop_loss_pct": config.risk.stop_loss_pct,
            "take_profit_pct": config.risk.take_profit_pct,
            "dry_run": config.risk.dry_run,
        }

    def emergency_stop(self, reason: str = "手动紧急停止"):
        """紧急停止交易"""
        self._trading_enabled = False
        logger.warning(f"紧急停止: {reason}")

    def resume(self):
        """恢复交易"""
        self._check_new_day()
        self._trading_enabled = True
        logger.info("交易已恢复")


# 全局单例
risk_manager = RiskManager()
