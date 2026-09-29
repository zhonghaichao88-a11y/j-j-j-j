"""
主交易调度器(多币种并行版)
- 支持同时监控多个币种, 每个币种独立运算/独立持仓/独立止损止盈
- 全局统一风控: 最大同时持仓数、信号置信度阈值
- 每一步动作都打印日志(带币种前缀)
- 现货模式使用手动止盈止损(机器人监控价格+市价平仓)
"""
import time
import threading
import traceback
from typing import Optional, Dict, Any, List
from loguru import logger
from config import config
from okx_client import okx_client
from risk_manager import risk_manager
from strategy import create_strategy, Signal, BaseStrategy
from learning import learner


class Trader:
    """交易调度器(多币种并行)"""

    def __init__(self):
        self._strategy: Optional[BaseStrategy] = None
        self._strategies: Dict[str, BaseStrategy] = {}
        self._running: bool = False
        self._thread: Optional[threading.Thread] = None
        self._trade_history: List[Dict[str, Any]] = []
        self._iteration_count: int = 0
        self._consecutive_errors: int = 0
        self._last_error_msg: str = ""
        self._symbol_states: Dict[str, Dict[str, Any]] = {}
        self._last_decisions: Dict[str, Dict[str, Any]] = {}

    def _get_state(self, symbol: str) -> Dict[str, Any]:
        if symbol not in self._symbol_states:
            self._symbol_states[symbol] = {
                "entry_price": 0.0, "position_side": "", "stop_price": 0.0, "take_profit_price": 0.0,
                "partial_tp_done": False, "breakeven_done": False, "current_price": 0.0,
            }
        return self._symbol_states[symbol]

    def _reset_state(self, symbol: str):
        state = self._get_state(symbol)
        logger.info(f"[{symbol}] [状态重置] 清仓入场价/止损价/止盈状态")
        state["entry_price"] = 0.0
        state["position_side"] = ""
        state["stop_price"] = 0.0
        state["take_profit_price"] = 0.0
        state["partial_tp_done"] = False
        state["breakeven_done"] = False

    def _count_open_positions(self) -> int:
        count = 0
        for sym, state in self._symbol_states.items():
            if state["entry_price"] > 0 and state["position_side"]:
                count += 1
        return count

    def _get_strategy(self, symbol: str) -> BaseStrategy:
        if symbol not in self._strategies:
            self._strategies[symbol] = create_strategy(config.trading.strategy_name)
        return self._strategies[symbol]

    def start(self, strategy_name: Optional[str] = None) -> bool:
        if self._running:
            logger.warning("交易机器人已在运行")
            return False
        try:
            logger.info("=" * 60)
            logger.info("[启动] 开始启动交易机器人(多币种并行版)...")
            if not okx_client.connect():
                logger.error("[启动] 连接欧易失败, 无法启动")
                return False
            logger.info("[启动] OKX连接成功")
            name = strategy_name or config.trading.strategy_name
            symbols = config.trading.get_active_symbols()
            logger.info(f"[启动] 监控币种: {symbols}")
            logger.info(f"[启动] 策略: {name} | 最大同时持仓: {config.risk.max_open_positions} | 置信度阈值: {config.risk.signal_confidence_threshold}")
            for sym in symbols:
                if sym not in self._strategies:
                    self._strategies[sym] = create_strategy(name)
            self._running = True
            self._iteration_count = 0
            self._consecutive_errors = 0
            self._thread = threading.Thread(target=self._main_loop, daemon=True)
            self._thread.start()
            type_text = {"swap": "永续合约", "spot": "现货"}.get(config.trading.trading_type, config.trading.trading_type)
            sl_mode = "手动止盈止损" if config.trading.trading_type == "spot" else "交易所条件单+手动双重保护"
            logger.info(f"[启动完成] 类型={type_text} | 杠杆={config.trading.leverage}x | 金额={config.trading.order_amount_usdt}U | 周期={config.trading.timeframe} | 止盈止损={sl_mode}")
            # 学习模块: 打印历史学习报告
            try:
                report = learner.get_learning_report()
                ov = report["overall"]
                logger.info(f"[学习系统] 历史交易{ov['count']}笔 | 胜率{ov['win_rate']}% | 平均盈亏{ov['avg_pnl_pct']}% | 总盈亏{ov['total_pnl']}U")
                if report["best_signals"]:
                    best = report["best_signals"][0]
                    logger.info(f"[学习系统] 最佳信号: {best['signal']}({best['direction']}) 胜率{best['win_rate']}% 平均{best['avg_pnl_pct']}%")
            except Exception as e:
                logger.info(f"[学习系统] 暂无历史数据: {e}")
            logger.info("=" * 60)
            return True
        except Exception as e:
            logger.error(f"[启动] 启动异常: {type(e).__name__}: {e}")
            logger.error(f"[启动] 堆栈: {traceback.format_exc()}")
            self._running = False
            return False

    def stop(self):
        logger.info("[停止] 正在停止交易机器人...")
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("[停止] 交易机器人已停止")

    @property
    def is_running(self) -> bool:
        return self._running

    def _main_loop(self):
        logger.info("[主循环] 开始运行(多币种并行)")
        while self._running:
            try:
                self._iteration_count += 1
                symbols = config.trading.get_active_symbols()
                logger.info(f"====== 第{self._iteration_count}轮开始 | 监控{len(symbols)}个币种 | 当前持仓{self._count_open_positions()}个 ======")
                # ===== 第一步: 快速止损止盈检查(用实时最新价, 优先于策略计算, 确保止损不延迟) =====
                self._fast_stop_loss_check(symbols)
                # ===== 第二步: 完整策略信号判断 =====
                for symbol in symbols:
                    try:
                        self._run_once_for_symbol(symbol)
                    except Exception as e:
                        logger.error(f"[{symbol}] 处理异常: {e}")
                        logger.error(f"[{symbol}] 堆栈: {traceback.format_exc()}")
                self._consecutive_errors = 0
                self._last_error_msg = ""
                logger.info(f"====== 第{self._iteration_count}轮完成 ======")
            except Exception as e:
                self._consecutive_errors += 1
                self._last_error_msg = str(e)
                logger.error(f"[主循环异常] 第{self._iteration_count}轮 | 连续失败{self._consecutive_errors}次 | 错误: {e}")
                logger.error(f"[堆栈] {traceback.format_exc()}")
                if self._consecutive_errors >= 20:
                    logger.error(f"[紧急] 连续失败{self._consecutive_errors}次, 自动停止")
                    self._running = False
                    break
                time.sleep(5)
            sleep_sec = self._get_sleep_seconds()
            logger.info(f"[休眠] {sleep_sec}秒后进入下一轮")
            time.sleep(sleep_sec)

    def _fast_stop_loss_check(self, symbols: List[str]):
        """快速止损止盈检查(用实时最新价, 不获取K线, 确保止损不延迟)
        在每轮策略计算之前执行, 有持仓的币种用实时ticker价格检查止损止盈
        """
        for symbol in symbols:
            try:
                state = self._get_state(symbol)
                if not state["entry_price"] or not state["position_side"]:
                    continue
                # 用实时ticker获取最新成交价(比K线收盘价更实时)
                try:
                    old_sym = config.trading.symbol
                    config.trading.symbol = symbol
                    ticker = okx_client.get_ticker()
                    config.trading.symbol = old_sym
                    last_price = float(ticker.get("last") or ticker.get("close") or 0)
                except Exception:
                    last_price = state.get("current_price", 0)
                if not last_price or last_price <= 0:
                    continue
                sl_pct = config.risk.stop_loss_pct
                tp_pct = config.risk.take_profit_pct
                if state["position_side"] == "long":
                    pnl_pct = (last_price - state["entry_price"]) / state["entry_price"] * 100
                    stop_triggered = last_price <= state["stop_price"]
                    take_profit_price = state["entry_price"] * (1 + tp_pct)
                    take_profit_triggered = last_price >= take_profit_price
                else:
                    pnl_pct = (state["entry_price"] - last_price) / state["entry_price"] * 100
                    stop_triggered = last_price >= state["stop_price"]
                    take_profit_price = state["entry_price"] * (1 - tp_pct)
                    take_profit_triggered = last_price <= take_profit_price
                # 触发止损或止盈, 立即平仓(重试1次)
                if stop_triggered or take_profit_triggered:
                    trigger_type = "止损" if stop_triggered else "止盈"
                    logger.info(f"[{symbol}] [快速{trigger_type}触发] 实时价={last_price} | 盈亏={pnl_pct:.2f}% | 止损价={state['stop_price']} | 止盈价={take_profit_price:.2f} | 立即市价平仓")
                    old_sym = config.trading.symbol
                    config.trading.symbol = symbol
                    close_success = False
                    for retry in range(2):  # 平仓失败重试1次
                        try:
                            okx_client.close_position()
                            close_success = True
                            break
                        except Exception as e:
                            logger.error(f"[{symbol}] [快速{trigger_type}] 平仓失败(第{retry+1}次): {e}")
                            if retry == 0:
                                import time as _t
                                _t.sleep(0.5)
                    config.trading.symbol = old_sym
                    if close_success:
                        self._reset_state(symbol)
                        logger.info(f"[{symbol}] [快速{trigger_type}] 市价平仓完成")
                        try:
                            learner.record_close(trade_id="", close_price=last_price, close_reason=f"快速{trigger_type}触发")
                        except Exception:
                            pass
                    else:
                        logger.error(f"[{symbol}] [快速{trigger_type}] 平仓重试2次均失败, 下一轮继续尝试")
            except Exception as e:
                logger.error(f"[{symbol}] 快速止损检查异常: {e}")

    def _run_once_for_symbol(self, symbol: str):
        state = self._get_state(symbol)
        strategy = self._get_strategy(symbol)
        dec = {
            "iteration": self._iteration_count, "symbol": symbol,
            "signal": "未运行", "signal_reason": "",
            "intercepted": False, "intercept_reason": "",
            "action": "无动作", "confidence": 0,
        }
        self._last_decisions[symbol] = dec
        old_symbol = config.trading.symbol
        config.trading.symbol = symbol
        try:
            ohlcv = okx_client.get_ohlcv(limit=200)
            if not ohlcv or len(ohlcv) < 30:
                dec["action"] = "K线不足跳过"
                logger.info(f"[{symbol}] [K线] 数据不足({len(ohlcv) if ohlcv else 0}根), 跳过")
                return
            current_price = ohlcv[-1][4]
            state["current_price"] = current_price
            dec["current_price"] = current_price

            positions = okx_client.get_positions()
            has_position = len(positions) > 0
            position_side = positions[0].get("side") if has_position else None
            if not has_position and state["entry_price"] > 0 and state["position_side"]:
                logger.info(f"[{symbol}] [持仓] 余额查询为空但有入场价({state['entry_price']}), 按有持仓处理")
                has_position = True
                position_side = state["position_side"]
            dec["has_position"] = has_position
            dec["position_side"] = position_side
            if has_position:
                logger.info(f"[{symbol}] [持仓] 有持仓 | 方向={position_side} | 入场价={state['entry_price']}")
                # 如果有持仓但状态字典里没有入场价(比如机器人启动前手动开的仓), 自动获取入场价并设置止损止盈
                if state["entry_price"] <= 0 and positions:
                    pos_entry = float(positions[0].get("entry_price", 0) or 0)
                    if pos_entry > 0:
                        state["entry_price"] = pos_entry
                        state["position_side"] = position_side or "long"
                        sl_pct = config.risk.stop_loss_pct
                        tp_pct = config.risk.take_profit_pct
                        if state["position_side"] == "long":
                            state["stop_price"] = state["entry_price"] * (1 - sl_pct)
                            state["take_profit_price"] = state["entry_price"] * (1 + tp_pct)
                        else:
                            state["stop_price"] = state["entry_price"] * (1 + sl_pct)
                            state["take_profit_price"] = state["entry_price"] * (1 - tp_pct)
                        logger.info(f"[{symbol}] [持仓] 检测到已有持仓, 自动设置入场价={pos_entry} | 止损价={state['stop_price']} | 止盈价={state['take_profit_price']}")
            else:
                logger.info(f"[{symbol}] [持仓] 无持仓 | 当前价={current_price}")

            if has_position and state["entry_price"] > 0:
                self._manage_positions(symbol, current_price, state)
                positions = okx_client.get_positions()
                has_position = len(positions) > 0
                if not has_position and state["entry_price"] == 0:
                    position_side = None
                    dec["has_position"] = False

            signal, info = strategy.generate_signal(ohlcv, has_position, position_side)
            dec["signal"] = signal
            dec["signal_reason"] = info.get("reason", "")
            dec["confidence"] = info.get("confidence", 100)
            signal_text = {
                Signal.BUY: "买入/开多", Signal.SELL: "卖出/开空",
                Signal.CLOSE_LONG: "平多", Signal.CLOSE_SHORT: "平空",
                Signal.HOLD: "持有/不动",
            }.get(signal, signal)
            logger.info(f"[{symbol}] [策略信号] {signal_text} | 置信度={info.get('confidence', 100)} | 原因: {info.get('reason', '无')}")

            if signal in (Signal.BUY, Signal.SELL):
                if state["entry_price"] > 0:
                    logger.info(f"[{symbol}] [防重复开仓] 已有入场价({state['entry_price']}), 跳过")
                    dec["intercepted"] = True
                    dec["intercept_reason"] = "已有持仓"
                    dec["action"] = "防重复开仓"
                    return
                open_count = self._count_open_positions()
                if open_count >= config.risk.max_open_positions:
                    logger.info(f"[{symbol}] [风控拦截] 达到最大同时持仓数({open_count}/{config.risk.max_open_positions}), 跳过开仓")
                    dec["intercepted"] = True
                    dec["intercept_reason"] = f"达到最大持仓数{config.risk.max_open_positions}"
                    dec["action"] = "风控拦截(最大持仓)"
                    return
                confidence = info.get("confidence", 100)
                if confidence < config.risk.signal_confidence_threshold:
                    logger.info(f"[{symbol}] [置信度拦截] 信号置信度{confidence}低于阈值{config.risk.signal_confidence_threshold}, 跳过")
                    dec["intercepted"] = True
                    dec["intercept_reason"] = f"置信度{confidence}<阈值{config.risk.signal_confidence_threshold}"
                    dec["action"] = "置信度不足"
                    return
                if config.trading.trade_direction == "long" and signal == Signal.SELL:
                    logger.info(f"[{symbol}] [方向过滤] 强制做多, 忽略开空信号")
                    dec["action"] = "方向过滤"
                    return
                if config.trading.trade_direction == "short" and signal == Signal.BUY:
                    logger.info(f"[{symbol}] [方向过滤] 强制做空, 忽略开多信号")
                    dec["action"] = "方向过滤"
                    return
                self._open_position(symbol, signal, info, state)
                dec["action"] = f"开仓{signal_text}"
            elif signal in (Signal.CLOSE_LONG, Signal.CLOSE_SHORT):
                if state["entry_price"] > 0:
                    self._close_position(symbol, signal, info, state)
                    dec["action"] = f"平仓{signal_text}"
                else:
                    logger.info(f"[{symbol}] [平仓] 无入场价, 跳过")
                    dec["action"] = "无持仓跳过"
            else:
                dec["action"] = "策略无信号,持有不动"
                logger.info(f"[{symbol}] [本轮完成] 最终动作: {dec['action']}")
        except Exception as e:
            logger.error(f"[{symbol}] 决策异常: {e}")
            logger.error(f"[{symbol}] 堆栈: {traceback.format_exc()}")
            dec["action"] = f"异常:{e}"
        finally:
            config.trading.symbol = old_symbol

    def _open_position(self, symbol: str, signal: str, info: Dict, state: Dict):
        side = "buy" if signal == Signal.BUY else "sell"
        side_text = "做多" if signal == Signal.BUY else "做空"
        logger.info(f"[{symbol}] [开{side_text}] 开始 | 金额={config.trading.order_amount_usdt}U | 杠杆={config.trading.leverage}x")
        allowed, reason = risk_manager.can_trade(side, config.trading.order_amount_usdt)
        if not allowed:
            logger.warning(f"[{symbol}] [开{side_text}] 被风控拦截: {reason}")
            return
        logger.info(f"[{symbol}] [开{side_text}] 风控检查通过")
        # 开仓前为当前币种单独设置杠杆(多币运行时每个币种都要设, 否则用交易所默认杠杆)
        if config.trading.trading_type != "spot" or config.trading.leverage > 1:
            okx_client.set_leverage_for_symbol(symbol)
        logger.info(f"[{symbol}] [开{side_text}] 正在下单...")
        try:
            order = okx_client.place_order(side=side, order_type="market", symbol=config.trading.get_ccxt_symbol(symbol))
        except Exception as e:
            logger.error(f"[{symbol}] [开{side_text}] 下单失败: {e}")
            return
        logger.info(f"[{symbol}] [开{side_text}] 下单成功 | 订单ID={order.get('id', 'N/A')} | 数量={order.get('amount', 0)} | 委托价={order.get('average', order.get('price', 0))}")
        # 获取实际成交价(解决入场价不准): 先看order里的average, 没有则等一下fetch_order拉详情
        fill_price = okx_client.get_fill_price(order, config.trading.get_ccxt_symbol(symbol))
        if fill_price <= 0:
            fill_price = float(order.get("average") or order.get("price") or 0)
        state["entry_price"] = fill_price or info.get("price", 0) or state["current_price"]
        logger.info(f"[{symbol}] [开{side_text}] 实际成交价={state['entry_price']} (用于计算止损止盈)")
        state["position_side"] = "long" if signal == Signal.BUY else "short"
        sl_pct = config.risk.stop_loss_pct
        tp_pct = config.risk.take_profit_pct
        if state["position_side"] == "long":
            state["stop_price"] = state["entry_price"] * (1 - sl_pct)
            state["take_profit_price"] = state["entry_price"] * (1 + tp_pct)
        else:
            state["stop_price"] = state["entry_price"] * (1 + sl_pct)
            state["take_profit_price"] = state["entry_price"] * (1 - tp_pct)
        state["partial_tp_done"] = False
        state["breakeven_done"] = False
        logger.info(f"[{symbol}] [开{side_text}完成] 入场价={state['entry_price']} | 止损价={state['stop_price']}({sl_pct*100:.0f}%) | 止盈价={state['take_profit_price']:.4f}({tp_pct*100:.0f}%) | 方向={side_text}")
        # 学习模块: 记录开仓信号(等平仓时更新结果)
        try:
            learner.record_open(
                trade_id=str(order.get("id", "")),
                symbol=symbol,
                strategy=config.trading.strategy_name,
                signal=signal,
                signal_reason=info.get("reason", ""),
                direction=state["position_side"],
                entry_price=state["entry_price"],
                amount_usdt=config.trading.order_amount_usdt,
                confidence=info.get("confidence", 0),
            )
        except Exception as e:
            logger.warning(f"[{symbol}] 学习模块记录开仓失败: {e}")
        if config.trading.trading_type == "spot":
            logger.info(f"[{symbol}] [止盈止损] 现货模式: 使用手动止盈止损(机器人每轮监控价格, 达到止损/止盈价主动市价平仓)")
        risk_manager.record_trade(
            trade_id=str(order.get("id", "")), side=side,
            amount=order.get("amount", 0), price=order.get("price", info.get("price", 0)),
            reason=info.get("reason", f"开{side_text}"),
        )
        self._trade_history.append({"symbol": symbol, "action": f"open_{state['position_side']}", **order, **info})
        try:
            okx_client.set_stop_loss_take_profit(state["position_side"], state["entry_price"])
        except Exception as e:
            logger.warning(f"[{symbol}] 设置交易所止盈止损失败(不影响手动止盈止损): {e}")

    def _close_position(self, symbol: str, signal: str, info: Dict, state: Dict):
        side_text = "多单" if signal == Signal.CLOSE_LONG else "空单"
        logger.info(f"[{symbol}] [平仓] 开始平{side_text} | 原因: {info.get('reason', '无')}")
        logger.info(f"[{symbol}] [平仓] 正在执行市价平仓...")
        try:
            order = okx_client.close_position()
        except Exception as e:
            logger.error(f"[{symbol}] [平仓] 失败: {e}")
            return
        logger.info(f"[{symbol}] [平仓完成] 平{side_text}成功 | 订单ID={order.get('id', 'N/A')} | 数量={order.get('amount', 0)} | 价格={order.get('price', 0)}")
        # 学习模块: 记录平仓结果
        try:
            close_price = float(order.get("average") or order.get("price") or state.get("current_price", 0))
            learner.record_close(
                trade_id=str(order.get("id", "")),
                close_price=close_price,
                close_reason=info.get("reason", "策略平仓"),
            )
        except Exception as e:
            logger.warning(f"[{symbol}] 学习模块记录平仓失败: {e}")
        self._reset_state(symbol)
        risk_manager.record_trade(
            trade_id=str(order.get("id", "")),
            side="sell" if signal == Signal.CLOSE_LONG else "buy",
            amount=order.get("amount", 0), price=order.get("price", 0),
            reason=info.get("reason", f"平{side_text}"),
        )
        self._trade_history.append({"symbol": symbol, "action": f"close_{'long' if signal == Signal.CLOSE_LONG else 'short'}", **order, **info})

    def manual_buy(self, amount_usdt: Optional[float] = None) -> Dict[str, Any]:
        symbol = config.trading.symbol
        logger.info(f"[{symbol}] [手动买入] 开始 | 金额={amount_usdt or config.trading.order_amount_usdt}U | 杠杆={config.trading.leverage}x")
        if amount_usdt:
            old = config.trading.order_amount_usdt
            config.trading.order_amount_usdt = amount_usdt
        try:
            # 手动下单前也设置杠杆, 确保币种杠杆正确
            if config.trading.trading_type != "spot" or config.trading.leverage > 1:
                okx_client.set_leverage_for_symbol(symbol)
            order = okx_client.place_order(side="buy", order_type="market")
            logger.info(f"[{symbol}] [手动买入] 成功 | 订单ID={order.get('id', 'N/A')}")
            return {"success": True, "order": order}
        except Exception as e:
            logger.error(f"[{symbol}] [手动买入] 失败: {e}")
            return {"success": False, "error": str(e)}
        finally:
            if amount_usdt:
                config.trading.order_amount_usdt = old

    def manual_sell(self, amount_usdt: Optional[float] = None) -> Dict[str, Any]:
        symbol = config.trading.symbol
        logger.info(f"[{symbol}] [手动卖出] 开始 | 金额={amount_usdt or config.trading.order_amount_usdt}U | 杠杆={config.trading.leverage}x")
        if amount_usdt:
            old = config.trading.order_amount_usdt
            config.trading.order_amount_usdt = amount_usdt
        try:
            # 手动下单前也设置杠杆, 确保币种杠杆正确
            if config.trading.trading_type != "spot" or config.trading.leverage > 1:
                okx_client.set_leverage_for_symbol(symbol)
            order = okx_client.place_order(side="sell", order_type="market")
            logger.info(f"[{symbol}] [手动卖出] 成功 | 订单ID={order.get('id', 'N/A')}")
            return {"success": True, "order": order}
        except Exception as e:
            logger.error(f"[{symbol}] [手动卖出] 失败: {e}")
            return {"success": False, "error": str(e)}
        finally:
            if amount_usdt:
                config.trading.order_amount_usdt = old

    def manual_close(self) -> Dict[str, Any]:
        symbol = config.trading.symbol
        logger.info(f"[{symbol}] [手动平仓] 开始全平...")
        try:
            result = okx_client.close_position()
            self._reset_state(symbol)
            logger.info(f"[{symbol}] [手动平仓] 成功")
            return {"success": True, "result": result}
        except Exception as e:
            logger.error(f"[{symbol}] [手动平仓] 失败: {e}")
            return {"success": False, "error": str(e)}

    def _manage_positions(self, symbol: str, current_price: float, state: Dict):
        if not state["entry_price"] or not state["position_side"]:
            return
        sl_pct = config.risk.stop_loss_pct
        tp_pct = config.risk.take_profit_pct
        if state["position_side"] == "long":
            pnl_pct = (current_price - state["entry_price"]) / state["entry_price"] * 100
            take_profit_price = state["entry_price"] * (1 + tp_pct)
            stop_triggered = current_price <= state["stop_price"]
            take_profit_triggered = current_price >= take_profit_price
        else:
            pnl_pct = (state["entry_price"] - current_price) / state["entry_price"] * 100
            take_profit_price = state["entry_price"] * (1 - tp_pct)
            stop_triggered = current_price >= state["stop_price"]
            take_profit_triggered = current_price <= take_profit_price
        logger.info(f"[{symbol}] [止损止盈检查] 盈亏={pnl_pct:.2f}% | 入场价={state['entry_price']} | 当前价={current_price} | 止损价={state['stop_price']}({sl_pct*100:.0f}%) | 止盈价={take_profit_price:.4f}({tp_pct*100:.0f}%)")
        if state["stop_price"] > 0 and stop_triggered:
            logger.info(f"[{symbol}] [止损触发] {state['position_side']} | 当前价{current_price} 触发止损价{state['stop_price']}, 执行市价全平")
            try:
                okx_client.close_position()
                self._reset_state(symbol)
                logger.info(f"[{symbol}] [止损触发] 市价平仓完成")
                # 学习模块: 记录止损结果
                try:
                    learner.record_close(trade_id="", close_price=current_price, close_reason="止损触发")
                except Exception:
                    pass
            except Exception as e:
                logger.error(f"[{symbol}] [止损触发] 平仓失败: {e}")
            return
        if take_profit_triggered:
            logger.info(f"[{symbol}] [止盈触发] {state['position_side']} | 当前价{current_price} 达到止盈价{take_profit_price:.4f}, 执行市价全平")
            try:
                okx_client.close_position()
                self._reset_state(symbol)
                logger.info(f"[{symbol}] [止盈触发] 市价平仓完成")
                try:
                    learner.record_close(trade_id="", close_price=current_price, close_reason="止盈触发")
                except Exception:
                    pass
            except Exception as e:
                logger.error(f"[{symbol}] [止盈触发] 平仓失败: {e}")
            return
        if pnl_pct >= 3 and not state["partial_tp_done"]:
            logger.info(f"[{symbol}] [分批止盈触发] 盈利{pnl_pct:.1f}% >= 3%, 先平一半")
            try:
                positions = okx_client.get_positions()
                if positions:
                    half = float(positions[0].get("contracts", 0)) / 2
                    if half > 0:
                        side = "sell" if state["position_side"] == "long" else "buy"
                        okx_client.place_order(side=side, order_type="market", amount=half, reduce_only=True)
                        state["partial_tp_done"] = True
                        logger.info(f"[{symbol}] [分批止盈] 完成")
            except Exception as e:
                logger.error(f"[{symbol}] [分批止盈] 失败: {e}")
        if pnl_pct >= 5 and not state["breakeven_done"]:
            if state["position_side"] == "long":
                state["stop_price"] = state["entry_price"] * 1.02
            else:
                state["stop_price"] = state["entry_price"] * 0.98
            state["breakeven_done"] = True
            logger.info(f"[{symbol}] [移动止损] 盈利{pnl_pct:.1f}% >= 5%, 止损上移到盈利2%: {state['stop_price']}")
        elif pnl_pct >= 2 and not state["breakeven_done"]:
            state["stop_price"] = state["entry_price"]
            state["breakeven_done"] = True
            logger.info(f"[{symbol}] [移动止损] 盈利{pnl_pct:.1f}% >= 2%, 止损上移到成本价(保本): {state['stop_price']}")

    def get_status(self) -> Dict[str, Any]:
        symbols = config.trading.get_active_symbols()
        symbol_statuses = {}
        for sym in symbols:
            state = self._get_state(sym)
            symbol_statuses[sym] = {
                "entry_price": state["entry_price"],
                "position_side": state["position_side"],
                "stop_price": state["stop_price"],
                "take_profit_price": state["take_profit_price"],
                "current_price": state["current_price"],
                "has_position": state["entry_price"] > 0 and bool(state["position_side"]),
            }
        return {
            "running": self._running,
            "iteration": self._iteration_count,
            "consecutive_errors": self._consecutive_errors,
            "last_error": self._last_error_msg,
            "okx_connected": okx_client.is_connected,
            "okx_last_error": getattr(okx_client, "last_error", ""),
            "open_positions_count": self._count_open_positions(),
            "max_open_positions": config.risk.max_open_positions,
            "symbols": symbol_statuses,
            "last_decisions": self._last_decisions,
            "strategy": config.trading.strategy_name,
            "risk": risk_manager.get_status(),
            "config": {
                "symbols": symbols,
                "type": config.trading.trading_type,
                "leverage": config.trading.leverage,
                "timeframe": config.trading.timeframe,
                "order_amount_usdt": config.trading.order_amount_usdt,
                "trade_direction": config.trading.trade_direction,
                "demo_mode": config.okx.is_demo,
                "dry_run": config.risk.dry_run,
                "stop_loss_pct": config.risk.stop_loss_pct,
                "take_profit_pct": config.risk.take_profit_pct,
                "signal_confidence_threshold": config.risk.signal_confidence_threshold,
                "high_tf_confirm": config.risk.high_tf_confirm,
            },
        }

    def get_trade_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self._trade_history[-limit:]

    def _get_sleep_seconds(self) -> int:
        # 实时监控: 固定3秒一轮, 不再按K线周期长休眠
        # 策略信号本身按K线周期判断(不会重复开仓), 3秒一轮只影响价格监控和止损止盈响应速度
        return 3


trader = Trader()
