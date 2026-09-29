"""
ML交易引擎 - 干跑模式虚拟账户模块
- 用假钱模拟交易，不扣真钱
- 维护虚拟账户余额、持仓、交易记录
- 统计胜率、盈亏比、总收益、最大回撤
- 加滑点模拟，更接近真实
- 数据保存到 ml_dry_run.json，重启不丢失
"""
import os
import json
import time
from typing import Optional, Dict, Any, List
from loguru import logger

# 数据文件路径
DATA_FILE = os.path.join(os.path.dirname(__file__), "ml_dry_run.json")

# 默认配置
DEFAULT_INITIAL_BALANCE = 10000.0  # 初始资金
DEFAULT_SLIPPAGE = 0.001  # 滑点0.1%
DEFAULT_FEE_RATE = 0.001  # 手续费0.1%


class DryRunPosition:
    """干跑模式单个持仓"""
    def __init__(self, symbol: str, side: str, entry_price: float, size: float,
                 initial_size: float, stop_price: float, opened_at: float, open_fee: float = 0):
        self.symbol = symbol
        self.side = side  # long / short
        self.entry_price = entry_price
        self.size = size
        self.initial_size = initial_size
        self.stop_price = stop_price
        self.opened_at = opened_at
        self.open_fee = open_fee  # 开仓手续费
        self.partial_tp_done = False
        self.trailing_level = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": self.entry_price,
            "size": self.size,
            "initial_size": self.initial_size,
            "stop_price": self.stop_price,
            "opened_at": self.opened_at,
            "open_fee": self.open_fee,
            "partial_tp_done": self.partial_tp_done,
            "trailing_level": self.trailing_level,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DryRunPosition":
        pos = cls(
            symbol=data["symbol"],
            side=data["side"],
            entry_price=data["entry_price"],
            size=data["size"],
            initial_size=data.get("initial_size", data["size"]),
            stop_price=data.get("stop_price", 0),
            opened_at=data.get("opened_at", time.time()),
            open_fee=data.get("open_fee", 0),
        )
        pos.partial_tp_done = data.get("partial_tp_done", False)
        pos.trailing_level = data.get("trailing_level", 0)
        return pos

    def calc_pnl_pct(self, current_price: float) -> float:
        """计算当前盈亏百分比"""
        if self.side == "long":
            return (current_price - self.entry_price) / self.entry_price
        else:
            return (self.entry_price - current_price) / self.entry_price

    def calc_unrealized_pnl(self, current_price: float) -> float:
        """计算未实现盈亏（USDT）"""
        pnl_pct = self.calc_pnl_pct(current_price)
        position_value = self.size * self.entry_price
        return position_value * pnl_pct


class DryRunAccount:
    """干跑模式虚拟账户"""

    def __init__(self):
        self.balance: float = DEFAULT_INITIAL_BALANCE
        self.initial_balance: float = DEFAULT_INITIAL_BALANCE
        self.positions: Dict[str, DryRunPosition] = {}
        self.trades: List[Dict[str, Any]] = []
        self.equity_curve: List[Dict[str, Any]] = []
        self.slippage: float = DEFAULT_SLIPPAGE
        self.fee_rate: float = DEFAULT_FEE_RATE
        self.max_drawdown: float = 0.0
        self.peak_equity: float = DEFAULT_INITIAL_BALANCE
        self._load()

    # ==================================================================
    # 持久化
    # ==================================================================
    def _load(self):
        """从文件加载数据"""
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.balance = data.get("balance", DEFAULT_INITIAL_BALANCE)
                self.initial_balance = data.get("initial_balance", DEFAULT_INITIAL_BALANCE)
                self.slippage = data.get("slippage", DEFAULT_SLIPPAGE)
                self.fee_rate = data.get("fee_rate", DEFAULT_FEE_RATE)
                self.max_drawdown = data.get("max_drawdown", 0.0)
                self.peak_equity = data.get("peak_equity", self.initial_balance)
                self.trades = data.get("trades", [])
                self.equity_curve = data.get("equity_curve", [])
                # 加载持仓
                self.positions = {}
                for sym, pos_data in data.get("positions", {}).items():
                    self.positions[sym] = DryRunPosition.from_dict(pos_data)
                logger.info(f"[干跑模式] 加载数据成功 | 余额={self.balance:.2f}U | 持仓={len(self.positions)} | 交易={len(self.trades)}笔")
        except Exception as e:
            logger.error(f"[干跑模式] 加载数据失败，使用默认: {e}")
            self.reset()

    def _save(self):
        """保存数据到文件"""
        try:
            data = {
                "balance": self.balance,
                "initial_balance": self.initial_balance,
                "slippage": self.slippage,
                "fee_rate": self.fee_rate,
                "max_drawdown": self.max_drawdown,
                "peak_equity": self.peak_equity,
                "positions": {sym: pos.to_dict() for sym, pos in self.positions.items()},
                "trades": self.trades[-500:],  # 只保留最近500笔
                "equity_curve": self.equity_curve[-1000:],  # 只保留最近1000个点
            }
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[干跑模式] 保存数据失败: {e}")

    def reset(self, initial_balance: Optional[float] = None):
        """重置账户"""
        self.initial_balance = initial_balance or DEFAULT_INITIAL_BALANCE
        self.balance = self.initial_balance
        self.positions = {}
        self.trades = []
        self.equity_curve = []
        self.max_drawdown = 0.0
        self.peak_equity = self.initial_balance
        self._save()
        logger.info(f"[干跑模式] 账户已重置 | 初始资金={self.initial_balance}U")

    # ==================================================================
    # 价格模拟（滑点+手续费）
    # ==================================================================
    def _simulate_buy_price(self, price: float) -> float:
        """模拟买入成交价（加滑点）"""
        return price * (1 + self.slippage)

    def _simulate_sell_price(self, price: float) -> float:
        """模拟卖出成交价（减滑点）"""
        return price * (1 - self.slippage)

    def _calc_fee(self, amount_usdt: float) -> float:
        """计算手续费"""
        return amount_usdt * self.fee_rate

    # ==================================================================
    # 开仓
    # ==================================================================
    def open_position(self, symbol: str, side: str, price: float, size: float,
                       stop_price: float) -> Dict[str, Any]:
        """模拟开仓

        Args:
            symbol: 币种
            side: long / short
            price: 当前价格
            size: 下单数量（币的数量）
            stop_price: 止损价

        Returns:
            {success, message, entry_price, size, fee}
        """
        # 模拟成交价（滑点）
        if side == "long":
            entry_price = self._simulate_buy_price(price)
        else:
            entry_price = self._simulate_sell_price(price)

        # 计算仓位价值和手续费
        position_value = size * entry_price
        fee = self._calc_fee(position_value)

        # 检查余额
        if position_value + fee > self.balance:
            max_size = (self.balance * 0.95) / entry_price
            if max_size <= 0:
                return {"success": False, "message": f"余额不足，无法开仓 | 余额={self.balance:.2f}U"}
            size = max_size
            position_value = size * entry_price
            fee = self._calc_fee(position_value)

        # 扣除余额
        self.balance -= (position_value + fee)

        # 创建持仓
        pos = DryRunPosition(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            size=size,
            initial_size=size,
            stop_price=stop_price,
            opened_at=time.time(),
            open_fee=fee,
        )
        self.positions[symbol] = pos

        # 记录交易
        trade = {
            "time": time.time(),
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol,
            "side": side,
            "action": "open",
            "price": entry_price,
            "size": size,
            "value": position_value,
            "fee": fee,
            "stop_price": stop_price,
            "balance_after": self.balance,
        }
        self.trades.append(trade)

        # 更新权益曲线
        self._update_equity_curve()

        self._save()
        logger.info(f"[干跑模式] 开仓成功 | {symbol} {side} | 入场价={entry_price:.4f} | 数量={size:.6f} | 价值={position_value:.2f}U | 手续费={fee:.2f}U | 余额={self.balance:.2f}U")
        return {
            "success": True,
            "message": "干跑开仓成功",
            "entry_price": entry_price,
            "size": size,
            "fee": fee,
            "position_value": position_value,
        }

    # ==================================================================
    # 平仓
    # ==================================================================
    def close_position(self, symbol: str, price: float, reason: str = "",
                       close_ratio: float = 1.0) -> Dict[str, Any]:
        """模拟平仓

        Args:
            symbol: 币种
            price: 当前价格
            reason: 平仓原因
            close_ratio: 平仓比例（1.0=全平，0.5=平一半）

        Returns:
            {success, message, close_price, pnl, fee}
        """
        if symbol not in self.positions:
            return {"success": False, "message": f"没有持仓: {symbol}"}

        pos = self.positions[symbol]

        # 计算平仓数量
        close_size = pos.size * close_ratio
        if close_size <= 0:
            return {"success": False, "message": "平仓数量为0"}

        # 模拟成交价（滑点）
        if pos.side == "long":
            close_price = self._simulate_sell_price(price)
        else:
            close_price = self._simulate_buy_price(price)

        # 计算盈亏
        if pos.side == "long":
            pnl_pct = (close_price - pos.entry_price) / pos.entry_price
        else:
            pnl_pct = (pos.entry_price - close_price) / pos.entry_price
        close_value = close_size * close_price
        pnl = close_size * pos.entry_price * pnl_pct
        fee = self._calc_fee(close_value)
        # 按比例扣除开仓手续费
        open_fee_ratio = close_size / pos.initial_size if pos.initial_size > 0 else 1
        open_fee_portion = pos.open_fee * open_fee_ratio
        net_pnl = pnl - fee - open_fee_portion

        # 更新余额: 平仓得到的钱(close_value)减去手续费(fee)
        # 开仓时扣除了 position_value + fee_open, 平仓时加回 close_value - fee_close
        self.balance += (close_value - fee)

        # 更新持仓
        pos.size -= close_size
        if pos.size <= 1e-10:
            del self.positions[symbol]
            position_closed = True
        else:
            position_closed = False

        # 记录交易
        trade = {
            "time": time.time(),
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol,
            "side": pos.side,
            "action": "close",
            "price": close_price,
            "size": close_size,
            "value": close_value,
            "pnl": pnl,
            "net_pnl": net_pnl,
            "pnl_pct": pnl_pct * 100,
            "fee": fee,
            "open_fee": open_fee_portion,
            "reason": reason,
            "balance_after": self.balance,
            "partial": not position_closed,
        }
        self.trades.append(trade)

        # 更新最大回撤
        self._update_max_drawdown()

        # 更新权益曲线
        self._update_equity_curve()

        self._save()
        logger.info(f"[干跑模式] 平仓成功 | {symbol} {pos.side} | 平仓价={close_price:.4f} | 数量={close_size:.6f} | 盈亏={net_pnl:.2f}U ({pnl_pct*100:.2f}%) | 手续费={fee:.2f}U | 余额={self.balance:.2f}U | 原因={reason}")
        return {
            "success": True,
            "message": "干跑平仓成功",
            "close_price": close_price,
            "pnl": pnl,
            "net_pnl": net_pnl,
            "pnl_pct": pnl_pct * 100,
            "fee": fee,
            "close_size": close_size,
            "position_closed": position_closed,
        }

    # ==================================================================
    # 更新止损价（移动止损用）
    # ==================================================================
    def update_stop_price(self, symbol: str, stop_price: float, trailing_level: int = 0):
        """更新持仓止损价"""
        if symbol in self.positions:
            self.positions[symbol].stop_price = stop_price
            self.positions[symbol].trailing_level = trailing_level
            self._save()

    def set_partial_tp_done(self, symbol: str, done: bool = True):
        """设置分批止盈已完成"""
        if symbol in self.positions:
            self.positions[symbol].partial_tp_done = done
            self._save()

    # ==================================================================
    # 统计
    # ==================================================================
    def _update_equity_curve(self):
        """更新权益曲线"""
        total_equity = self.balance
        for sym, pos in self.positions.items():
            # 用入场价估算未实现盈亏（简化）
            total_equity += pos.size * pos.entry_price
        point = {
            "time": time.time(),
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S"),
            "equity": total_equity,
        }
        self.equity_curve.append(point)

    def _update_max_drawdown(self):
        """更新最大回撤"""
        total_equity = self.balance
        for sym, pos in self.positions.items():
            total_equity += pos.size * pos.entry_price
        if total_equity > self.peak_equity:
            self.peak_equity = total_equity
        if self.peak_equity > 0:
            drawdown = (self.peak_equity - total_equity) / self.peak_equity
            if drawdown > self.max_drawdown:
                self.max_drawdown = drawdown

    def get_statistics(self) -> Dict[str, Any]:
        """获取统计数据"""
        # 计算已平仓交易
        closed_trades = [t for t in self.trades if t.get("action") == "close"]
        total_trades = len(closed_trades)
        win_trades = [t for t in closed_trades if t.get("net_pnl", 0) > 0]
        loss_trades = [t for t in closed_trades if t.get("net_pnl", 0) <= 0]
        win_rate = (len(win_trades) / total_trades * 100) if total_trades > 0 else 0

        total_pnl = sum(t.get("net_pnl", 0) for t in closed_trades)
        total_pnl_pct = (total_pnl / self.initial_balance * 100) if self.initial_balance > 0 else 0

        avg_win = sum(t.get("net_pnl", 0) for t in win_trades) / len(win_trades) if win_trades else 0
        avg_loss = abs(sum(t.get("net_pnl", 0) for t in loss_trades) / len(loss_trades)) if loss_trades else 0
        profit_factor = (avg_win / avg_loss) if avg_loss > 0 else (float('inf') if avg_win > 0 else 0)

        # 当前总权益
        total_equity = self.balance
        for sym, pos in self.positions.items():
            total_equity += pos.size * pos.entry_price

        # 数据是否足够
        enough_data = total_trades >= 5

        return {
            "initial_balance": self.initial_balance,
            "balance": self.balance,
            "total_equity": total_equity,
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct,
            "total_trades": total_trades,
            "win_trades": len(win_trades),
            "loss_trades": len(loss_trades),
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "max_drawdown": self.max_drawdown * 100,
            "open_positions": len(self.positions),
            "enough_data": enough_data,
            "data_note": "交易次数少于5笔，数据仅供参考" if not enough_data else "",
        }

    def get_positions_list(self) -> List[Dict[str, Any]]:
        """获取持仓列表"""
        result = []
        for sym, pos in self.positions.items():
            result.append({
                "symbol": pos.symbol,
                "side": pos.side,
                "entry_price": pos.entry_price,
                "size": pos.size,
                "initial_size": pos.initial_size,
                "stop_price": pos.stop_price,
                "trailing_level": pos.trailing_level,
                "partial_tp_done": pos.partial_tp_done,
                "opened_at": pos.opened_at,
                "opened_at_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(pos.opened_at)),
            })
        return result

    def get_recent_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取最近交易记录"""
        return self.trades[-limit:][::-1]  # 倒序，最新的在前面


# 全局单例
dry_run_account = DryRunAccount()
