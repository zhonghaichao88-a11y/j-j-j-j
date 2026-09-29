"""
机器学习模拟盘交易引擎(独立新增, 不改动原有代码)
功能:
  - 虚拟账户(假钱), 真实行情(从OKX实盘API拉)
  - 用训练好的机器学习模型自动模拟交易
  - 完整模拟开仓/持仓/止损止盈/平仓
  - 支持多币种同时模拟
  - 保存交易记录到本地, 可查看历史/胜率/资金曲线
  - 可随时启动/停止/重置
"""
import os
import time
import json
import threading
import traceback
from typing import List, Dict, Any, Optional
from loguru import logger

# 模拟盘数据保存文件
PAPER_FILE = os.path.join(os.path.dirname(__file__), "ml_paper_trading.json")

# 默认初始资金
DEFAULT_INITIAL_BALANCE = 10000.0


class PaperPosition:
    """模拟持仓"""
    def __init__(self, symbol: str, side: str, entry_price: float,
                 size: float, amount_usdt: float, leverage: int,
                 stop_loss_price: float, take_profit_price: float):
        self.symbol = symbol
        self.side = side  # long / short
        self.entry_price = entry_price
        self.size = size  # 币数量
        self.amount_usdt = amount_usdt  # 下单金额=保证金(USDT)，永续实际名义仓位=金额×杠杆
        self.leverage = leverage
        self.stop_loss_price = stop_loss_price
        self.take_profit_price = take_profit_price
        self.open_time = time.strftime("%Y-%m-%d %H:%M:%S")
        self.open_timestamp = time.time()
        self.highest_price = entry_price
        self.lowest_price = entry_price
        self.partial_tp_done = False
        self.breakeven_done = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": self.entry_price,
            "size": self.size,
            "amount_usdt": self.amount_usdt,
            "leverage": self.leverage,
            "stop_loss_price": self.stop_loss_price,
            "take_profit_price": self.take_profit_price,
            "open_time": self.open_time,
            "open_timestamp": self.open_timestamp,
            "highest_price": self.highest_price,
            "lowest_price": self.lowest_price,
            "partial_tp_done": self.partial_tp_done,
            "breakeven_done": self.breakeven_done,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PaperPosition":
        pos = cls(
            symbol=d["symbol"], side=d["side"], entry_price=d["entry_price"],
            size=d["size"], amount_usdt=d["amount_usdt"], leverage=d["leverage"],
            stop_loss_price=d["stop_loss_price"], take_profit_price=d["take_profit_price"],
        )
        pos.open_time = d.get("open_time", "")
        pos.open_timestamp = d.get("open_timestamp", time.time())
        pos.highest_price = d.get("highest_price", d["entry_price"])
        pos.lowest_price = d.get("lowest_price", d["entry_price"])
        pos.partial_tp_done = d.get("partial_tp_done", False)
        pos.breakeven_done = d.get("breakeven_done", False)
        return pos

    def calc_pnl_pct(self, current_price: float) -> float:
        """计算浮动盈亏百分比(含杠杆)"""
        if self.side == "long":
            return (current_price - self.entry_price) / self.entry_price * 100 * self.leverage
        else:
            return (self.entry_price - current_price) / self.entry_price * 100 * self.leverage

    def calc_pnl_usdt(self, current_price: float) -> float:
        """计算浮动盈亏(USDT)"""
        pnl_pct = self.calc_pnl_pct(current_price) / 100
        return self.amount_usdt * pnl_pct


class PaperTradingEngine:
    """模拟盘交易引擎"""

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # 账户状态
        self.initial_balance = DEFAULT_INITIAL_BALANCE
        self.balance = DEFAULT_INITIAL_BALANCE
        self.positions: Dict[str, PaperPosition] = {}  # symbol -> position
        self.trades: List[Dict[str, Any]] = []
        self.equity_curve: List[Dict[str, Any]] = []  # [{time, equity}]
        self.symbols: List[str] = []  # 监控的币种列表
        self.order_amount_usdt = 100.0
        self.leverage = 10
        self.stop_loss_pct = 0.02
        self.take_profit_pct = 0.04
        self._iteration = 0
        self._last_error = ""

        self._load()

    # ==================================================================
    # 持久化
    # ==================================================================
    def _load(self):
        """从本地加载模拟盘数据"""
        try:
            if os.path.exists(PAPER_FILE):
                with open(PAPER_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.initial_balance = data.get("initial_balance", DEFAULT_INITIAL_BALANCE)
                    self.balance = data.get("balance", DEFAULT_INITIAL_BALANCE)
                    self.symbols = data.get("symbols", [])
                    self.order_amount_usdt = data.get("order_amount_usdt", 100.0)
                    self.leverage = data.get("leverage", 10)
                    self.stop_loss_pct = data.get("stop_loss_pct", 0.02)
                    self.take_profit_pct = data.get("take_profit_pct", 0.04)
                    self.trades = data.get("trades", [])
                    self.equity_curve = data.get("equity_curve", [])
                    positions = data.get("positions", {})
                    self.positions = {k: PaperPosition.from_dict(v) for k, v in positions.items()}
                    logger.info(f"[模拟盘] 加载完成: 余额{self.balance}U, 持仓{len(self.positions)}个, 历史交易{len(self.trades)}笔")
        except Exception as e:
            logger.warning(f"[模拟盘] 加载数据失败: {e}, 使用默认值")

    def _save(self):
        """保存到本地"""
        try:
            data = {
                "initial_balance": self.initial_balance,
                "balance": self.balance,
                "symbols": self.symbols,
                "order_amount_usdt": self.order_amount_usdt,
                "leverage": self.leverage,
                "stop_loss_pct": self.stop_loss_pct,
                "take_profit_pct": self.take_profit_pct,
                "trades": self.trades[-500:],
                "equity_curve": self.equity_curve[-1000:],
                "positions": {k: v.to_dict() for k, v in self.positions.items()},
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            with open(PAPER_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[模拟盘] 保存数据失败: {e}")

    # ==================================================================
    # 配置
    # ==================================================================
    def configure(self, symbols: Optional[List[str]] = None,
                  order_amount_usdt: Optional[float] = None,
                  leverage: Optional[int] = None,
                  initial_balance: Optional[float] = None):
        """配置模拟盘参数"""
        with self._lock:
            if symbols is not None:
                self.symbols = symbols
            if order_amount_usdt is not None:
                self.order_amount_usdt = order_amount_usdt
            if leverage is not None:
                self.leverage = max(1, min(125, leverage))
            if initial_balance is not None and not self._running:
                self.initial_balance = initial_balance
                self.balance = initial_balance
            self._save()
        logger.info(f"[模拟盘] 配置更新: 币种{self.symbols}, 金额{self.order_amount_usdt}U, 杠杆{self.leverage}x")

    def reset(self, initial_balance: Optional[float] = None):
        """重置模拟账户"""
        if self._running:
            self.stop()
        with self._lock:
            self.initial_balance = initial_balance or DEFAULT_INITIAL_BALANCE
            self.balance = self.initial_balance
            self.positions = {}
            self.trades = []
            self.equity_curve = []
            self._iteration = 0
            self._last_error = ""
            self._save()
        logger.info(f"[模拟盘] 已重置, 初始资金{self.initial_balance}U")

    # ==================================================================
    # 启动/停止
    # ==================================================================
    def start(self) -> bool:
        """启动模拟盘"""
        if self._running:
            logger.warning("[模拟盘] 已在运行")
            return False
        if not self.symbols:
            logger.error("[模拟盘] 未配置监控币种, 无法启动")
            return False
        self._running = True
        self._thread = threading.Thread(target=self._main_loop, daemon=True)
        self._thread.start()
        logger.info(f"[模拟盘] 已启动 | 币种: {self.symbols} | 金额: {self.order_amount_usdt}U | 杠杆: {self.leverage}x | 余额: {self.balance}U")
        return True

    def stop(self):
        """停止模拟盘"""
        logger.info("[模拟盘] 正在停止...")
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._save()
        logger.info("[模拟盘] 已停止")

    @property
    def is_running(self) -> bool:
        return self._running

    # ==================================================================
    # 主循环
    # ==================================================================
    def _main_loop(self):
        """模拟盘主循环"""
        logger.info("[模拟盘] 主循环开始")
        while self._running:
            try:
                self._iteration += 1
                logger.info(f"[模拟盘] ====== 第{self._iteration}轮开始 | 余额{self.balance:.2f}U | 持仓{len(self.positions)}个 ======")

                for i, symbol in enumerate(self.symbols):
                    try:
                        self._process_symbol(symbol)
                    except Exception as e:
                        logger.error(f"[模拟盘] [{symbol}] 处理异常: {e}")
                        logger.error(traceback.format_exc())
                    # 多币种之间加1秒延迟，避免OKX限流
                    if i < len(self.symbols) - 1:
                        time.sleep(1)

                # 记录权益曲线
                total_equity = self.balance
                for pos in self.positions.values():
                    try:
                        from okx_client import okx_client
                        from config import config as _cfg
                        ccxt_sym = _cfg.trading.get_ccxt_symbol(pos.symbol)
                        ticker = okx_client.get_ticker(ccxt_sym)
                        current_price = float(ticker.get("last") or ticker.get("close") or 0)
                        total_equity += pos.calc_pnl_usdt(current_price)
                    except Exception:
                        pass

                self.equity_curve.append({
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "timestamp": time.time(),
                    "equity": round(total_equity, 2),
                })

                # 每10轮保存一次
                if self._iteration % 10 == 0:
                    self._save()

                logger.info(f"[模拟盘] ====== 第{self._iteration}轮完成 | 总权益{total_equity:.2f}U ======")

            except Exception as e:
                self._last_error = str(e)
                logger.error(f"[模拟盘] 主循环异常: {e}")
                logger.error(traceback.format_exc())
                time.sleep(5)

            time.sleep(30)  # 每30秒一轮，避免OKX限流

    def _process_symbol(self, symbol: str):
        """处理单个币种"""
        from okx_client import okx_client
        from config import config as _cfg
        from ml_predict_strategy import MLPredictStrategy

        ccxt_sym = _cfg.trading.get_ccxt_symbol(symbol)

        # 获取K线
        ohlcv = okx_client.get_ohlcv(symbol=ccxt_sym, limit=200)
        if not ohlcv or len(ohlcv) < 60:
            logger.info(f"[模拟盘] [{symbol}] K线数据不足({len(ohlcv) if ohlcv else 0}根), 跳过")
            return

        current_price = float(ohlcv[-1][4])

        # 获取实时价格
        try:
            ticker = okx_client.get_ticker(ccxt_sym)
            current_price = float(ticker.get("last") or current_price)
        except Exception:
            pass

        # 检查持仓止损止盈
        if symbol in self.positions:
            self._check_stop_loss_take_profit(symbol, current_price)
            # 如果平仓了, 继续下一轮
            if symbol not in self.positions:
                return

        # 用机器学习策略生成信号
        strategy = MLPredictStrategy(symbol=symbol)
        has_position = symbol in self.positions
        position_side = self.positions[symbol].side if has_position else None
        signal, info = strategy.generate_signal(ohlcv, has_position, position_side)

        logger.info(f"[模拟盘] [{symbol}] 信号: {signal} | 置信度: {info.get('confidence', 0)} | 原因: {info.get('reason', '')}")

        # 执行信号
        if signal == "buy" and not has_position:
            self._open_position(symbol, "long", current_price, info)
        elif signal == "sell" and not has_position:
            self._open_position(symbol, "short", current_price, info)
        elif signal == "close_long" and has_position and position_side == "long":
            self._close_position(symbol, current_price, "策略平仓", info)
        elif signal == "close_short" and has_position and position_side == "short":
            self._close_position(symbol, current_price, "策略平仓", info)

    # ==================================================================
    # 开仓/平仓
    # ==================================================================
    def _open_position(self, symbol: str, side: str, price: float, info: Dict[str, Any]):
        """模拟开仓"""
        with self._lock:
            # 检查余额
            if self.balance < self.order_amount_usdt:
                logger.warning(f"[模拟盘] [{symbol}] 余额不足({self.balance:.2f}U < {self.order_amount_usdt}U), 跳过开仓")
                return

            # 检查最大持仓数(最多5个)
            if len(self.positions) >= 5:
                logger.warning(f"[模拟盘] 达到最大持仓数(5个), 跳过开仓")
                return

            # 计算币数量(加杠杆)
            position_value = self.order_amount_usdt * self.leverage
            size = position_value / price

            # 止损止盈价
            if side == "long":
                stop_loss_price = price * (1 - self.stop_loss_pct)
                take_profit_price = price * (1 + self.take_profit_pct)
            else:
                stop_loss_price = price * (1 + self.stop_loss_pct)
                take_profit_price = price * (1 - self.take_profit_pct)

            pos = PaperPosition(
                symbol=symbol, side=side, entry_price=price,
                size=size, amount_usdt=self.order_amount_usdt, leverage=self.leverage,
                stop_loss_price=stop_loss_price, take_profit_price=take_profit_price,
            )
            self.positions[symbol] = pos
            # 开仓不扣余额(用保证金模式), 只在平仓时结算

            side_text = "做多" if side == "long" else "做空"
            logger.info(f"[模拟盘] [{symbol}] 开{side_text} | 价格={price} | 数量={size:.6f} | 金额={self.order_amount_usdt}U | 杠杆={self.leverage}x | 止损={stop_loss_price:.2f} | 止盈={take_profit_price:.2f}")

    def _close_position(self, symbol: str, price: float, reason: str, info: Optional[Dict[str, Any]] = None):
        """模拟平仓"""
        with self._lock:
            if symbol not in self.positions:
                return
            pos = self.positions[symbol]
            pnl = pos.calc_pnl_usdt(price)
            pnl_pct = pos.calc_pnl_pct(price)
            self.balance += pnl

            trade = {
                "symbol": symbol,
                "side": pos.side,
                "entry_price": pos.entry_price,
                "exit_price": price,
                "amount_usdt": pos.amount_usdt,
                "leverage": pos.leverage,
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "open_time": pos.open_time,
                "close_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "reason": reason,
                "signal_reason": info.get("reason", "") if info else "",
                "confidence": info.get("confidence", 0) if info else 0,
            }
            self.trades.append(trade)
            del self.positions[symbol]

            result = "盈利" if pnl > 0 else "亏损"
            logger.info(f"[模拟盘] [{symbol}] 平仓 | {result}{abs(pnl):.2f}U({pnl_pct:.2f}%) | 原因: {reason} | 余额: {self.balance:.2f}U")

    def _check_stop_loss_take_profit(self, symbol: str, current_price: float):
        """检查止损止盈"""
        pos = self.positions.get(symbol)
        if not pos:
            return

        # 更新最高最低价
        if current_price > pos.highest_price:
            pos.highest_price = current_price
        if current_price < pos.lowest_price:
            pos.lowest_price = current_price

        pnl_pct = pos.calc_pnl_pct(current_price)
        logger.info(f"[模拟盘] [{symbol}] 持仓监控 | 方向={pos.side} | 入场={pos.entry_price} | 当前={current_price} | 盈亏={pnl_pct:.2f}% | 止损={pos.stop_loss_price:.2f} | 止盈={pos.take_profit_price:.2f}")

        # 止损
        if pos.side == "long" and current_price <= pos.stop_loss_price:
            self._close_position(symbol, current_price, "止损触发")
            return
        if pos.side == "short" and current_price >= pos.stop_loss_price:
            self._close_position(symbol, current_price, "止损触发")
            return

        # 止盈
        if pos.side == "long" and current_price >= pos.take_profit_price:
            self._close_position(symbol, current_price, "止盈触发")
            return
        if pos.side == "short" and current_price <= pos.take_profit_price:
            self._close_position(symbol, current_price, "止盈触发")
            return

        # 移动止损(盈利2%上移到成本价, 盈利5%上移到盈利2%)
        if not pos.breakeven_done:
            if pnl_pct >= 5 * pos.leverage / 10:
                if pos.side == "long":
                    pos.stop_loss_price = pos.entry_price * 1.02
                else:
                    pos.stop_loss_price = pos.entry_price * 0.98
                pos.breakeven_done = True
                logger.info(f"[模拟盘] [{symbol}] 移动止损: 盈利{pnl_pct:.1f}%, 止损上移到{pos.stop_loss_price:.2f}")
            elif pnl_pct >= 2 * pos.leverage / 10:
                pos.stop_loss_price = pos.entry_price
                pos.breakeven_done = True
                logger.info(f"[模拟盘] [{symbol}] 保本止损: 盈利{pnl_pct:.1f}%, 止损上移到成本价{pos.entry_price}")

    # ==================================================================
    # 状态查询
    # ==================================================================
    def get_status(self) -> Dict[str, Any]:
        """获取模拟盘状态"""
        # 计算总权益
        total_equity = self.balance
        positions_info = []
        for symbol, pos in self.positions.items():
            try:
                from okx_client import okx_client
                from config import config as _cfg
                ccxt_sym = _cfg.trading.get_ccxt_symbol(symbol)
                ticker = okx_client.get_ticker(ccxt_sym)
                current_price = float(ticker.get("last") or ticker.get("close") or 0)
            except Exception:
                current_price = pos.entry_price
            pnl = pos.calc_pnl_usdt(current_price)
            pnl_pct = pos.calc_pnl_pct(current_price)
            total_equity += pnl
            positions_info.append({
                **pos.to_dict(),
                "current_price": round(current_price, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
            })

        # 统计
        closed_trades = [t for t in self.trades]
        wins = [t for t in closed_trades if t["pnl"] > 0]
        losses = [t for t in closed_trades if t["pnl"] <= 0]
        win_rate = len(wins) / len(closed_trades) * 100 if closed_trades else 0
        total_pnl = sum(t["pnl"] for t in closed_trades)
        total_return = (total_equity - self.initial_balance) / self.initial_balance * 100

        return {
            "running": self._running,
            "iteration": self._iteration,
            "initial_balance": round(self.initial_balance, 2),
            "balance": round(self.balance, 2),
            "total_equity": round(total_equity, 2),
            "total_pnl": round(total_pnl, 2),
            "total_return_pct": round(total_return, 2),
            "win_rate": round(win_rate, 1),
            "total_trades": len(closed_trades),
            "win_count": len(wins),
            "loss_count": len(losses),
            "positions_count": len(self.positions),
            "positions": positions_info,
            "symbols": self.symbols,
            "order_amount_usdt": self.order_amount_usdt,
            "leverage": self.leverage,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "last_error": self._last_error,
        }

    def get_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取交易历史"""
        return self.trades[-limit:][::-1]

    def get_equity_curve(self, limit: int = 200) -> List[Dict[str, Any]]:
        """获取资金曲线"""
        return self.equity_curve[-limit:]


# 全局单例
paper_trading = PaperTradingEngine()
