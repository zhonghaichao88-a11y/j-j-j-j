"""
机器学习交易引擎 - 完全独立版
- 不依赖原来的 trader.py, 有自己完整的交易逻辑
- 信号过滤: 趋势过滤 / 多周期共振 / 成交量过滤
- 出场逻辑: 固定止损止盈 / 移动止损(两档) / 分批止盈
- 支持多币种同时监控, 每个持仓独立管理
- 启动时自动读取真实持仓并纳入监控
- 模拟盘和实盘共用同一套逻辑
"""
import time
import threading
import traceback
from typing import Optional, Dict, Any, List, Tuple
from loguru import logger
from config import config
from okx_client import okx_client
from ml_config import load_ml_config
from ml_dry_run import dry_run_account


class PositionState:
    """单个持仓的状态管理"""
    def __init__(self, symbol: str, side: str, entry_price: float, size: float):
        self.symbol = symbol
        self.side = side  # long / short
        self.entry_price = entry_price
        self.size = size  # 当前持仓数量
        self.initial_size = size  # 初始持仓数量(用于分批止盈计算)
        self.current_stop_price = 0.0  # 当前止损价(移动止损会更新)
        self.trailing_level = 0  # 移动止损档位: 0=未触发, 1=第一档, 2=第二档
        self.partial_tp_done = False  # 是否已经执行过分批止盈
        self.opened_at = time.time()

    def update_size(self, new_size: float):
        """更新持仓数量(分批止盈后调用)"""
        self.size = new_size

    def calc_pnl_pct(self, current_price: float) -> float:
        """计算当前盈亏百分比"""
        if self.side == "long":
            return (current_price - self.entry_price) / self.entry_price
        else:
            return (self.entry_price - current_price) / self.entry_price

    def should_stop_loss(self, current_price: float, stop_loss_pct: float) -> bool:
        """是否触发固定止损"""
        # 如果有移动止损价, 用移动止损价判断
        if self.current_stop_price > 0:
            if self.side == "long":
                return current_price <= self.current_stop_price
            else:
                return current_price >= self.current_stop_price
        # 否则用固定止损
        pnl = self.calc_pnl_pct(current_price)
        return pnl <= -stop_loss_pct

    def update_trailing_stop(self, current_price: float, cfg: Dict[str, Any]) -> Optional[str]:
        """更新移动止损, 返回触发的档位描述"""
        if not cfg.get("trailing_stop_enabled", False):
            return None
        pnl = self.calc_pnl_pct(current_price)
        triggered = None

        # 第二档(更高盈利)
        if (self.trailing_level < 2 and
                pnl >= cfg.get("trailing_stop_step2_trigger_pct", 0.04)):
            move_to = cfg.get("trailing_stop_step2_move_to_pct", 0.02)
            if self.side == "long":
                self.current_stop_price = self.entry_price * (1 + move_to)
            else:
                self.current_stop_price = self.entry_price * (1 - move_to)
            self.trailing_level = 2
            triggered = f"移动止损第二档触发, 止损上移到盈利{move_to*100:.1f}%"

        # 第一档
        elif (self.trailing_level < 1 and
              pnl >= cfg.get("trailing_stop_trigger_pct", 0.02)):
            move_to = cfg.get("trailing_stop_move_to_pct", 0.0)
            if self.side == "long":
                self.current_stop_price = self.entry_price * (1 + move_to)
            else:
                self.current_stop_price = self.entry_price * (1 - move_to)
            self.trailing_level = 1
            triggered = f"移动止损第一档触发, 止损上移到{'成本价' if move_to == 0 else f'盈利{move_to*100:.1f}%'}"

        return triggered


class MLTrader:
    """机器学习交易引擎(完全独立)"""

    def __init__(self):
        self._running: bool = False
        self._thread: Optional[threading.Thread] = None
        self._iteration_count: int = 0
        self._consecutive_errors: int = 0
        self._last_error: str = ""
        self._positions: Dict[str, PositionState] = {}  # symbol -> PositionState
        self._ohlcv_cache: Dict[str, Tuple[float, List[List[float]]]] = {}  # symbol -> (timestamp, ohlcv)
        self._symbols: List[str] = []
        self._start_time: float = 0
        self._trade_count: int = 0
        self._last_decisions: Dict[str, Dict[str, Any]] = {}
        self.dry_run: bool = False  # 干跑模式(假钱测试)
        self._retrained_today: set = set()  # 今天已自动重训的币种, 防止重复触发
        self._retrain_date: str = ""  # 记录当前日期, 跨天自动清空
        self._last_reconnect_attempt: float = 0  # 上次尝试重连的时间, 避免疯狂重连

    # ==================================================================
    # 启动/停止
    # ==================================================================
    def start(self, symbols: Optional[List[str]] = None) -> bool:
        """启动ML交易引擎"""
        if self._running:
            logger.warning("[ML引擎] 已在运行中")
            return False
        try:
            logger.info("=" * 60)
            logger.info("[ML引擎] 开始启动机器学习交易引擎...")
            if not okx_client.connect():
                logger.error("[ML引擎] 连接欧易失败, 无法启动")
                return False
            logger.info("[ML引擎] OKX连接成功")

            self._symbols = symbols or config.trading.get_active_symbols()
            logger.info(f"[ML引擎] 监控币种: {self._symbols}")

            cfg = load_ml_config()
            self.dry_run = cfg.get("dry_run_enabled", False)
            # 干跑模式: 同步滑点配置到虚拟账户
            if self.dry_run:
                dry_run_account.slippage = cfg.get("dry_run_slippage", 0.001)
                logger.info(f"[ML引擎] 干跑模式已开启 | 滑点={dry_run_account.slippage*100:.2f}% | 初始资金={cfg.get('dry_run_initial_balance', 10000)}U")
            logger.info(f"[ML引擎] 下单金额={cfg['order_amount_usdt']}U | 杠杆={cfg['leverage']}x | "
                        f"最大持仓={cfg['max_open_positions']} | 开仓阈值={cfg['entry_threshold']*100:.0f}% | "
                        f"轮询间隔={cfg['poll_interval']}秒 | 干跑模式={'开启(假钱)' if self.dry_run else '关闭(真钱)'}")

            # 同步ML配置到全局config, 确保okx_client用正确的杠杆和金额
            config.trading.leverage = cfg.get("leverage", 10)
            config.trading.order_amount_usdt = cfg.get("order_amount_usdt", 100)
            logger.info(f"[ML引擎] 已同步配置到全局: 杠杆={config.trading.leverage}x | 金额={config.trading.order_amount_usdt}U")

            # 启动时同步真实持仓
            self._sync_real_positions()

            self._running = True
            self._iteration_count = 0
            self._consecutive_errors = 0
            self._start_time = time.time()
            self._thread = threading.Thread(target=self._main_loop, daemon=True)
            self._thread.start()

            logger.info(f"[ML引擎] 启动完成, 当前持仓 {len(self._positions)} 个")
            logger.info("=" * 60)
            return True
        except Exception as e:
            logger.error(f"[ML引擎] 启动异常: {type(e).__name__}: {e}")
            logger.error(f"[ML引擎] 堆栈: {traceback.format_exc()}")
            return False

    def stop(self):
        """停止ML交易引擎(不平仓, 只停止监控)"""
        logger.info("[ML引擎] 正在停止...")
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("[ML引擎] 已停止")

    def emergency_stop(self):
        """紧急停止(立即停止, 不等待)"""
        logger.warning("[ML引擎] 紧急停止!")
        self._running = False

    def _try_reconnect(self) -> bool:
        """尝试重新连接OKX，至少间隔10秒才重连一次，避免疯狂重连"""
        now = time.time()
        if now - self._last_reconnect_attempt < 10:
            return False  # 距离上次重连不到10秒，跳过
        self._last_reconnect_attempt = now
        try:
            logger.warning("[ML引擎] 网络异常，尝试重新连接OKX...")
            okx_client.disconnect()
            time.sleep(1)
            if okx_client.connect():
                logger.info("[ML引擎] 重新连接OKX成功！")
                self._consecutive_errors = 0
                self._last_error = ""
                return True
            else:
                logger.error("[ML引擎] 重新连接OKX失败")
                return False
        except Exception as e:
            logger.error(f"[ML引擎] 重连异常: {e}")
            return False

    @property
    def is_running(self) -> bool:
        return self._running

    # ==================================================================
    # 主循环
    # ==================================================================
    def _main_loop(self):
        logger.info("[ML引擎] 主循环开始")
        while self._running:
            try:
                self._iteration_count += 1
                cfg = load_ml_config()

                # 同步真实持仓(防止外部手动开平仓)
                self._sync_real_positions()

                open_count = len(self._positions)
                logger.info(f"====== [ML引擎] 第{self._iteration_count}轮 | 监控{len(self._symbols)}币 | 当前持仓{open_count}个 ======")

                # 第一步: 对有持仓的币种检查出场
                for symbol in list(self._positions.keys()):
                    try:
                        self._check_exit(symbol, cfg)
                    except Exception as e:
                        logger.error(f"[ML引擎] [{symbol}] 出场检查异常: {e}")

                # 第二步: 对没持仓的币种检查开仓
                for symbol in self._symbols:
                    if symbol in self._positions:
                        continue
                    if len(self._positions) >= cfg.get("max_open_positions", 3):
                        logger.info(f"[ML引擎] 已达最大持仓数({cfg['max_open_positions']}), 跳过开仓检查")
                        break
                    try:
                        self._check_entry(symbol, cfg)
                    except Exception as e:
                        logger.error(f"[ML引擎] [{symbol}] 开仓检查异常: {e}")

                # 第三步: 自动重训检查
                self._check_auto_retrain(cfg)

                self._consecutive_errors = 0
                self._last_error = ""
                logger.info(f"====== [ML引擎] 第{self._iteration_count}轮完成 ======")

            except Exception as e:
                self._consecutive_errors += 1
                self._last_error = str(e)
                logger.error(f"[ML引擎] 主循环异常 第{self._iteration_count}轮 | 连续失败{self._consecutive_errors}次 | 错误: {e}")
                logger.error(f"[ML引擎] 堆栈: {traceback.format_exc()}")
                # 自动重连：网络断了会自己恢复，不会因为连续失败就停止
                self._try_reconnect()
                time.sleep(5)

            # 轮询间隔
            cfg = load_ml_config()
            time.sleep(cfg.get("poll_interval", 15))

    # ==================================================================
    # 持仓同步
    # ==================================================================
    def _sync_real_positions(self):
        """同步持仓到本地状态
        干跑模式: 用dry_run_account的虚拟持仓
        实盘模式: 从OKX读取真实持仓
        """
        if self.dry_run:
            # 干跑模式: 用虚拟账户的持仓
            self._positions = {}
            for pos in dry_run_account.get_positions_list():
                p = PositionState(
                    symbol=pos["symbol"],
                    side=pos["side"],
                    entry_price=pos["entry_price"],
                    size=pos["size"],
                )
                p.initial_size = pos.get("initial_size", pos["size"])
                p.current_stop_price = pos.get("stop_price", 0)
                p.trailing_level = pos.get("trailing_level", 0)
                p.partial_tp_done = pos.get("partial_tp_done", False)
                p.opened_at = pos.get("opened_at", time.time())
                self._positions[pos["symbol"]] = p
            return

        # 实盘模式: 从OKX读取真实持仓
        try:
            real_positions = okx_client.get_positions()
            real_symbols = set()
            for p in real_positions:
                sym = p.get("symbol", "")
                # 转换为我们的symbol格式
                our_symbol = self._ccxt_to_our_symbol(sym)
                if our_symbol not in self._symbols:
                    continue
                real_symbols.add(our_symbol)
                side = p.get("side", "")
                entry = float(p.get("entryPrice", 0) or p.get("entry_price", 0) or 0)
                size = float(p.get("contracts", 0) or p.get("amount", 0) or 0)
                if size == 0 or entry == 0:
                    continue

                if our_symbol not in self._positions:
                    # 新持仓, 创建状态
                    self._positions[our_symbol] = PositionState(our_symbol, side, entry, size)
                    logger.info(f"[ML引擎] [{our_symbol}] 同步到真实持仓: {side} | 入场价={entry} | 数量={size}")
                else:
                    # 已有持仓, 更新数量
                    pos = self._positions[our_symbol]
                    if abs(pos.size - size) > 1e-8:
                        logger.info(f"[ML引擎] [{our_symbol}] 持仓数量变化: {pos.size} -> {size}")
                        pos.update_size(size)
                    # 如果数量变成0, 删除
                    if size <= 0:
                        del self._positions[our_symbol]
                        logger.info(f"[ML引擎] [{our_symbol}] 持仓已平, 移除监控")

            # 检查本地有但真实没有的持仓(可能被外部平掉了)
            for sym in list(self._positions.keys()):
                if sym not in real_symbols:
                    logger.info(f"[ML引擎] [{sym}] 真实持仓已不存在, 移除本地监控")
                    del self._positions[sym]

        except Exception as e:
            logger.error(f"[ML引擎] 同步真实持仓失败: {e}")

    def _ccxt_to_our_symbol(self, ccxt_sym: str) -> str:
        """将ccxt格式的symbol转换为我们的格式"""
        # BTC/USDT:USDT -> BTC-USDT-SWAP
        # BTC/USDT -> BTC-USDT
        if ":" in ccxt_sym:
            base = ccxt_sym.split("/")[0]
            quote = ccxt_sym.split(":")[0].split("/")[1]
            return f"{base}-{quote}-SWAP"
        elif "/" in ccxt_sym:
            base, quote = ccxt_sym.split("/")
            return f"{base}-{quote}"
        return ccxt_sym

    # ==================================================================
    # K线获取(带缓存)
    # ==================================================================
    def _get_ohlcv(self, symbol: str, timeframe: str = "15m", limit: int = 200) -> Optional[List[List[float]]]:
        """获取K线(带1分钟缓存)"""
        cache_key = f"{symbol}_{timeframe}"
        now = time.time()
        if cache_key in self._ohlcv_cache:
            ts, data = self._ohlcv_cache[cache_key]
            if now - ts < 60:  # 1分钟缓存
                return data
        try:
            ccxt_sym = config.trading.get_ccxt_symbol(symbol)
            data = okx_client.get_ohlcv(symbol=ccxt_sym, timeframe=timeframe, limit=limit)
            if data and len(data) > 0:
                self._ohlcv_cache[cache_key] = (now, data)
                return data
        except Exception as e:
            logger.error(f"[ML引擎] [{symbol}] 获取{timeframe}K线失败: {e}")
        return None

    # ==================================================================
    # 信号过滤
    # ==================================================================
    def _trend_filter(self, ohlcv: List[List[float]], cfg: Dict[str, Any]) -> Optional[str]:
        """趋势过滤: 返回 'long'/'short'/None(不通过)
        价格在均线上方只做多, 下方只做空
        """
        if not cfg.get("trend_filter_enabled", False):
            return "both"  # 没开过滤, 多空都允许
        period = cfg.get("trend_filter_ma_period", 200)
        if len(ohlcv) < period:
            logger.info(f"[趋势过滤] K线不足{period}根, 跳过过滤")
            return "both"
        # 计算简单移动平均
        closes = [float(k[4]) for k in ohlcv[-period:]]
        ma = sum(closes) / len(closes)
        current_price = float(ohlcv[-1][4])
        if current_price > ma:
            logger.info(f"[趋势过滤] 当前价{current_price:.4f} > {period}日均线{ma:.4f}, 只允许多")
            return "long"
        else:
            logger.info(f"[趋势过滤] 当前价{current_price:.4f} <= {period}日均线{ma:.4f}, 只允许空")
            return "short"

    def _multi_timeframe_filter(self, symbol: str, cfg: Dict[str, Any], base_signal: str) -> bool:
        """多周期共振过滤: 所有周期信号一致才通过"""
        if not cfg.get("multi_timeframe_enabled", False):
            return True
        tfs = cfg.get("multi_timeframe_tfs", ["15m", "1h"])
        if len(tfs) <= 1:
            return True
        try:
            from ml_learning import predict
            for tf in tfs[1:]:  # 跳过第一个(主周期已经预测过了)
                ohlcv = self._get_ohlcv(symbol, tf, 200)
                if not ohlcv:
                    logger.info(f"[多周期共振] 获取{tf}K线失败, 过滤不通过")
                    return False
                pred = predict(symbol, ohlcv, cfg.get("entry_threshold", 0.65), tf)
                if pred.get("error"):
                    logger.info(f"[多周期共振] {tf}周期模型预测失败(请先训练{tf}周期模型): {pred.get('error')}")
                    return False
                tf_signal = pred.get("signal", "hold")
                logger.info(f"[多周期共振] {tf}周期信号: {tf_signal} (上涨概率{pred.get('up_prob', 0)*100:.1f}%)")
                if tf_signal != base_signal:
                    logger.info(f"[多周期共振] {tf}周期信号({tf_signal})与主周期({base_signal})不一致, 过滤不通过")
                    return False
            logger.info("[多周期共振] 所有周期信号一致, 通过")
            return True
        except Exception as e:
            logger.error(f"[多周期共振] 检查异常: {e}")
            return False

    def _volume_filter(self, ohlcv: List[List[float]], cfg: Dict[str, Any]) -> bool:
        """成交量过滤: 最近成交量大于均量的指定倍数才通过"""
        if not cfg.get("volume_filter_enabled", False):
            return True
        ratio = cfg.get("volume_filter_ratio", 1.5)
        if len(ohlcv) < 21:
            return True
        # 最近一根K线成交量
        recent_vol = float(ohlcv[-1][5]) if len(ohlcv[-1]) > 5 else 0
        # 前20根平均成交量
        avg_vol = sum(float(k[5]) for k in ohlcv[-21:-1] if len(k) > 5) / 20
        if avg_vol == 0:
            return True
        actual_ratio = recent_vol / avg_vol
        logger.info(f"[成交量过滤] 最近量{recent_vol:.0f} / 20日均量{avg_vol:.0f} = {actual_ratio:.2f}倍 (阈值{ratio}倍)")
        if actual_ratio >= ratio:
            logger.info("[成交量过滤] 放量, 通过")
            return True
        else:
            logger.info("[成交量过滤] 缩量, 不通过")
            return False

    # ==================================================================
    # 开仓检查
    # ==================================================================
    def _check_entry(self, symbol: str, cfg: Dict[str, Any]):
        """检查是否可以开仓"""
        logger.info(f"[{symbol}] ===== 开仓检查 =====")

        # 1. 获取主周期K线
        ohlcv = self._get_ohlcv(symbol, "15m", 200)
        if not ohlcv or len(ohlcv) < 50:
            logger.info(f"[{symbol}] K线不足, 跳过")
            return

        # 现在get_ohlcv只返回已收盘K线；同一根K线只允许一次正式开仓判断。
        candle_ts = int(ohlcv[-1][0])
        if self._last_signal_candle.get(symbol) == candle_ts:
            logger.info(f"[{symbol}] 本根K线已产生过正式信号，跳过重复判断")
            return
        self._last_signal_candle[symbol] = candle_ts

        # 2. 模型预测
        try:
            from ml_learning import predict
            pred = predict(symbol, ohlcv, cfg.get("entry_threshold", 0.65), "15m")
        except Exception as e:
            logger.error(f"[{symbol}] 模型预测异常: {e}")
            return

        if pred.get("error"):
            logger.info(f"[{symbol}] 模型预测失败: {pred.get('error')}")
            return

        signal = pred.get("signal", "hold")
        up_prob = pred.get("up_prob", 0)
        down_prob = pred.get("down_prob", 0)
        logger.info(f"[{symbol}] 模型信号: {signal} | 上涨概率{up_prob*100:.1f}% | 下跌概率{down_prob*100:.1f}%")

        if signal == "hold":
            logger.info(f"[{symbol}] 信号为hold, 不开仓")
            self._last_decisions[symbol] = {"action": "hold", "reason": "概率不足", "up_prob": up_prob}
            return

        # 统一持仓方向格式: 模型信号 buy/sell -> 持仓方向 long/short
        position_side = "long" if signal == "buy" else "short"

        # 3. 趋势过滤
        trend_allowed = self._trend_filter(ohlcv, cfg)
        if trend_allowed != "both" and trend_allowed != position_side:
            logger.info(f"[{symbol}] 趋势过滤不通过(只允许{trend_allowed}, 信号是{position_side})")
            self._last_decisions[symbol] = {"action": "hold", "reason": f"趋势过滤不通过(只允许{trend_allowed})"}
            return

        # 4. 多周期共振过滤
        if not self._multi_timeframe_filter(symbol, cfg, signal):
            self._last_decisions[symbol] = {"action": "hold", "reason": "多周期共振不通过"}
            return

        # 5. ADX震荡市过滤(用1h周期判断大级别趋势强度)
        if cfg.get("adx_filter_enabled", False):
            adx_threshold = cfg.get("adx_threshold", 20)
            ohlcv_1h = self._get_ohlcv(symbol, "1h", 200)
            if ohlcv_1h and len(ohlcv_1h) >= 30:
                from ml_learning import calc_adx
                adx = calc_adx(ohlcv_1h)
                if adx is not None:
                    logger.info(f"[{symbol}] ADX(1h)={adx:.1f} (阈值{adx_threshold}, 低于阈值为震荡市)")
                    if adx < adx_threshold:
                        logger.info(f"[{symbol}] ADX过低, 震荡市, 不开仓")
                        self._last_decisions[symbol] = {"action": "hold", "reason": f"ADX震荡市过滤(ADX={adx:.1f}<{adx_threshold})"}
                        return
                else:
                    logger.info(f"[{symbol}] ADX计算失败, 跳过ADX过滤")
            else:
                logger.info(f"[{symbol}] 1hK线不足, 跳过ADX过滤")

        # 6. 成交量过滤
        if not self._volume_filter(ohlcv, cfg):
            self._last_decisions[symbol] = {"action": "hold", "reason": "成交量过滤不通过"}
            return

        # 7. 所有过滤通过, 开仓
        logger.info(f"[{symbol}] 所有过滤通过, 准备开{position_side}仓(信号{signal})")
        self._open_position(symbol, position_side, cfg)

    def _check_auto_retrain(self, cfg: Dict[str, Any]):
        """自动重训检查: 模型年龄超过配置间隔就自动加入训练队列"""
        if not cfg.get("auto_retrain_enabled", False):
            return
        # 跨天清空记录
        today = time.strftime("%Y-%m-%d")
        if today != self._retrain_date:
            self._retrain_date = today
            self._retrained_today.clear()
            logger.info(f"[自动重训] 新的一天({today}), 清空重训记录")
        interval_hours = cfg.get("auto_retrain_interval_hours", 24)
        try:
            from ml_learning import training_queue, get_model_meta, get_ml_settings
            for symbol in self._symbols:
                if symbol in self._retrained_today:
                    continue
                # 检查15m模型年龄
                meta = get_model_meta(symbol, timeframe="15m")
                if not meta:
                    continue  # 模型不存在, 跳过(用户自己训练)
                train_ts = meta.get("train_timestamp", 0)
                age_hours = (time.time() - train_ts) / 3600 if train_ts else 999
                if age_hours >= interval_hours:
                    if training_queue.add_task(symbol, timeframe="15m"):
                        self._retrained_today.add(symbol)
                        logger.info(f"[自动重训] [{symbol}] 模型已{age_hours:.1f}小时, 自动加入15m重训队列")
                    # 同时重训1h模型(如果多周期共振开了)
                    if cfg.get("multi_timeframe_enabled", False):
                        training_queue.add_task(symbol, timeframe="1h")
        except Exception as e:
            logger.error(f"[自动重训] 检查异常: {e}")

    def _open_position(self, symbol: str, side: str, cfg: Dict[str, Any]):
        """开仓"""
        try:
            ccxt_sym = config.trading.get_ccxt_symbol(symbol)
            amount_usdt = cfg.get("order_amount_usdt", 100)
            # 计算下单数量
            ticker = okx_client.get_ticker(ccxt_sym)
            if not ticker:
                logger.error(f"[{symbol}] 获取ticker失败, 无法开仓")
                return
            price = float(ticker.get("last", 0) or ticker.get("close", 0))
            if price == 0:
                logger.error(f"[{symbol}] 价格为0, 无法开仓")
                return
            # 与后台“下单金额(USDT)”统一：该金额代表保证金；永续实际仓位名义价值=保证金×杠杆。
            leverage = max(1, int(cfg.get("leverage", config.trading.leverage) or 1))
            is_swap = config.trading.trading_type == "swap"
            position_value_usdt = amount_usdt * leverage if is_swap else amount_usdt
            amount = position_value_usdt / price

            order_side = "buy" if side == "long" else "sell"
            logger.info(f"[{symbol}] 开{side}仓: {order_side} {amount:.6f} @ {price:.4f} | 保证金={amount_usdt:.2f}U | 杠杆={leverage}x | 名义仓位={position_value_usdt:.2f}U")

            # 干跑模式: 用虚拟账户开仓
            if self.dry_run:
                # 计算止损价
                if side == "long":
                    stop_price = price * (1 - cfg.get("stop_loss_pct", 0.02))
                else:
                    stop_price = price * (1 + cfg.get("stop_loss_pct", 0.02))
                result = dry_run_account.open_position(symbol, side, price, amount, stop_price)
                if result["success"]:
                    logger.info(f"[{symbol}] 干跑开仓成功! 入场价={result['entry_price']:.4f}")
                    self._trade_count += 1
                    # 同步持仓状态
                    self._sync_real_positions()
                    self._last_decisions[symbol] = {"action": f"open_{side}_dry_run", "price": result["entry_price"], "amount": amount}
                else:
                    logger.error(f"[{symbol}] 干跑开仓失败: {result.get('message')}")
                    self._last_decisions[symbol] = {"action": "failed", "reason": result.get("message")}
                return

            # 实盘模式: 真的下单
            # 开仓前设置杠杆(确保币种杠杆正确)
            try:
                if config.trading.trading_type != "spot" or config.trading.leverage > 1:
                    okx_client.set_leverage_for_symbol(ccxt_sym, leverage=leverage)
            except Exception as e:
                logger.warning(f"[{symbol}] 设置杠杆失败(不影响下单): {e}")

            result = okx_client.place_order(
                side=order_side,
                order_type="market",
                amount=amount,
                symbol=ccxt_sym,
            )

            # 不把“下单请求成功”直接当成“已经成交”：等待订单进入明确状态。
            final_order = okx_client.wait_for_order_final(result, ccxt_sym, timeout_sec=6.0, poll_sec=0.4)
            final_status = str(final_order.get("status", result.get("status", "")) or "").lower()
            if final_status in ("closed", "filled") or result.get("dry_run"):
                actual_price = okx_client.get_fill_price(final_order, ccxt_sym, wait_sec=0.0) if not result.get("dry_run") else price
                filled_amount = okx_client.get_filled_amount(final_order, ccxt_sym) if not result.get("dry_run") else amount
                if actual_price <= 0 or filled_amount <= 0:
                    logger.error(f"[{symbol}] 开仓结果无法确认实际成交价/数量: status={final_status}, filled={filled_amount}, order={final_order}")
                    self._last_decisions[symbol] = {"action": "failed", "reason": "订单状态已完成但无法确认实际成交价或成交数量"}
                    return
                logger.info(f"[{symbol}] 开仓成功! 实际成交价={actual_price:.4f} | 实际成交数量={filled_amount:.8f} (预估数量={amount:.8f})")
                self._trade_count += 1
                # 创建持仓状态：严格使用实际成交均价和实际成交数量
                pos = PositionState(symbol, side, actual_price, filled_amount)
                # 初始化固定止损价
                if side == "long":
                    pos.current_stop_price = actual_price * (1 - cfg.get("stop_loss_pct", 0.02))
                else:
                    pos.current_stop_price = actual_price * (1 + cfg.get("stop_loss_pct", 0.02))
                self._positions[symbol] = pos
                self._last_decisions[symbol] = {"action": f"open_{side}", "price": actual_price, "amount": amount}
            elif final_status in ("canceled", "cancelled", "rejected", "expired"):
                logger.error(f"[{symbol}] 开仓失败: 订单最终状态={final_status} | {final_order}")
                self._last_decisions[symbol] = {"action": "failed", "reason": f"订单最终状态: {final_status}"}
            else:
                # 超时仍是live/open/partially_filled时，不假装完整开仓成功；如果已有部分成交，记录实际部分成交数量。
                filled_amount = okx_client.get_filled_amount(final_order, ccxt_sym)
                actual_price = okx_client.get_fill_price(final_order, ccxt_sym, wait_sec=0.0)
                if final_status == "partially_filled" and filled_amount > 0 and actual_price > 0:
                    logger.warning(f"[{symbol}] 开仓部分成交: 数量={filled_amount:.8f}, 均价={actual_price:.4f}, 订单仍可能未完全结束")
                    pos = PositionState(symbol, side, actual_price, filled_amount)
                    if side == "long":
                        pos.current_stop_price = actual_price * (1 - cfg.get("stop_loss_pct", 0.02))
                    else:
                        pos.current_stop_price = actual_price * (1 + cfg.get("stop_loss_pct", 0.02))
                    self._positions[symbol] = pos
                    self._trade_count += 1
                    self._last_decisions[symbol] = {"action": f"open_{side}_partial", "price": actual_price, "amount": filled_amount, "order_status": final_status}
                else:
                    logger.error(f"[{symbol}] 开仓未确认成交: 订单状态={final_status} | {final_order}")
                    self._last_decisions[symbol] = {"action": "failed", "reason": f"订单未确认成交，状态={final_status}"}

        except Exception as e:
            logger.error(f"[{symbol}] 开仓异常: {e}")
            logger.error(f"[{symbol}] 堆栈: {traceback.format_exc()}")
            self._last_decisions[symbol] = {"action": "failed", "reason": str(e)}

    # ==================================================================
    # 出场检查
    # ==================================================================
    def _check_exit(self, symbol: str, cfg: Dict[str, Any]):
        """检查是否需要出场"""
        if symbol not in self._positions:
            return
        pos = self._positions[symbol]
        logger.info(f"[{symbol}] ===== 出场检查 | {pos.side} | 入场价={pos.entry_price:.4f} | 数量={pos.size:.6f} =====")

        # 获取当前价格
        try:
            ccxt_sym = config.trading.get_ccxt_symbol(symbol)
            ticker = okx_client.get_ticker(ccxt_sym)
            if not ticker:
                logger.error(f"[{symbol}] 获取ticker失败")
                return
            current_price = float(ticker.get("last", 0) or ticker.get("close", 0))
            if current_price == 0:
                return
        except Exception as e:
            logger.error(f"[{symbol}] 获取当前价格失败: {e}")
            return

        pnl_pct = pos.calc_pnl_pct(current_price)
        logger.info(f"[{symbol}] 当前价={current_price:.4f} | 盈亏={pnl_pct*100:.2f}% | 当前止损价={pos.current_stop_price:.4f}")

        # 1. 检查止损(固定止损或移动止损)
        if pos.should_stop_loss(current_price, cfg.get("stop_loss_pct", 0.02)):
            reason = "移动止损触发" if pos.trailing_level > 0 else "固定止损触发"
            logger.info(f"[{symbol}] {reason}, 平仓!")
            self._close_position(symbol, reason)
            return

        # 2. 检查固定止盈
        take_profit_pct = cfg.get("take_profit_pct", 0.04)
        if pnl_pct >= take_profit_pct:
            logger.info(f"[{symbol}] 达到固定止盈{take_profit_pct*100:.1f}%, 平仓!")
            self._close_position(symbol, "固定止盈触发")
            return

        # 3. 更新移动止损
        trailing_msg = pos.update_trailing_stop(current_price, cfg)
        if trailing_msg:
            logger.info(f"[{symbol}] {trailing_msg} | 新止损价={pos.current_stop_price:.4f}")
            # 干跑模式: 同步止损价到虚拟账户
            if self.dry_run:
                dry_run_account.update_stop_price(symbol, pos.current_stop_price, pos.trailing_level)

        # 4. 分批止盈
        if (cfg.get("partial_take_profit_enabled", False) and
                not pos.partial_tp_done and
                pnl_pct >= cfg.get("partial_take_profit_trigger_pct", 0.02)):
            ratio = cfg.get("partial_take_profit_ratio", 0.5)
            logger.info(f"[{symbol}] 分批止盈触发, 平掉{ratio*100:.0f}%仓位")
            partial_ok = self._partial_close(symbol, ratio)
            # 只有确认实际成交后，才标记分批止盈已完成；失败/未确认时允许后续再次检查。
            if partial_ok:
                pos.partial_tp_done = True
                if self.dry_run:
                    dry_run_account.set_partial_tp_done(symbol, True)

    def _close_position(self, symbol: str, reason: str):
        """全平"""
        try:
            ccxt_sym = config.trading.get_ccxt_symbol(symbol)
            logger.info(f"[{symbol}] 执行全平: {reason}")

            # 干跑模式: 用虚拟账户平仓
            if self.dry_run:
                # 获取当前价格
                ticker = okx_client.get_ticker(ccxt_sym)
                if not ticker:
                    logger.error(f"[{symbol}] 获取ticker失败, 无法平仓")
                    return
                current_price = float(ticker.get("last", 0) or ticker.get("close", 0))
                result = dry_run_account.close_position(symbol, current_price, reason)
                if result["success"]:
                    logger.info(f"[{symbol}] 干跑平仓成功! 盈亏={result['net_pnl']:.2f}U ({result['pnl_pct']:.2f}%)")
                    self._trade_count += 1
                    self._sync_real_positions()
                    self._last_decisions[symbol] = {"action": "close_dry_run", "reason": reason, "pnl": result["net_pnl"]}
                else:
                    logger.error(f"[{symbol}] 干跑平仓失败: {result.get('message')}")
                return

            # 实盘模式: 真的平仓
            result = okx_client.close_position(symbol=ccxt_sym)
            final_order = result
            if not result.get("dry_run") and result.get("id"):
                final_order = okx_client.wait_for_order_final(result, ccxt_sym, timeout_sec=6.0, poll_sec=0.4)
            final_status = str(final_order.get("status", result.get("status", "")) or "").lower()
            if final_status in ("closed", "filled", "no_position") or result.get("dry_run"):
                logger.info(f"[{symbol}] 平仓成功! 原因: {reason} | 最终状态={final_status}")
                self._trade_count += 1
                if symbol in self._positions:
                    del self._positions[symbol]
                self._last_decisions[symbol] = {"action": "close", "reason": reason, "order_status": final_status}
            else:
                logger.error(f"[{symbol}] 平仓未确认完成: 最终状态={final_status} | {final_order}")
        except Exception as e:
            logger.error(f"[{symbol}] 平仓异常: {e}")
            logger.error(f"[{symbol}] 堆栈: {traceback.format_exc()}")

    def _partial_close(self, symbol: str, ratio: float) -> bool:
        """部分平仓(分批止盈用)"""
        try:
            if symbol not in self._positions:
                return False
            pos = self._positions[symbol]
            ccxt_sym = config.trading.get_ccxt_symbol(symbol)
            close_amount = pos.size * ratio
            order_side = "sell" if pos.side == "long" else "buy"
            logger.info(f"[{symbol}] 部分平仓: {order_side} {close_amount:.6f} ({ratio*100:.0f}%)")

            # 干跑模式: 用虚拟账户部分平仓
            if self.dry_run:
                ticker = okx_client.get_ticker(ccxt_sym)
                if not ticker:
                    logger.error(f"[{symbol}] 获取ticker失败")
                    return
                current_price = float(ticker.get("last", 0) or ticker.get("close", 0))
                result = dry_run_account.close_position(symbol, current_price, "分批止盈", close_ratio=ratio)
                if result["success"]:
                    logger.info(f"[{symbol}] 干跑部分平仓成功! 盈亏={result['net_pnl']:.2f}U")
                    self._sync_real_positions()
                    self._last_decisions[symbol] = {"action": "partial_close_dry_run", "pnl": result["net_pnl"]}
                    return True
                else:
                    logger.error(f"[{symbol}] 干跑部分平仓失败: {result.get('message')}")
                return False

            # 实盘模式: 真的部分平仓
            result = okx_client.place_order(
                side=order_side,
                order_type="market",
                amount=close_amount,
                symbol=ccxt_sym,
                reduce_only=True,
            )

            final_order = okx_client.wait_for_order_final(result, ccxt_sym, timeout_sec=6.0, poll_sec=0.4)
            final_status = str(final_order.get("status", result.get("status", "")) or "").lower()
            if final_status in ("closed", "filled") or result.get("dry_run"):
                filled_close = okx_client.get_filled_amount(final_order, ccxt_sym) if not result.get("dry_run") else close_amount
                if filled_close <= 0:
                    logger.error(f"[{symbol}] 部分平仓订单已结束但实际成交数量无法确认: {final_order}")
                    return False
                remaining = max(0.0, pos.size - filled_close)
                logger.info(f"[{symbol}] 部分平仓成功! 实际平仓={filled_close:.8f}, 剩余={remaining:.8f}")
                pos.update_size(remaining)
                self._last_decisions[symbol] = {"action": "partial_close", "closed": filled_close, "remaining": remaining}
                return True
            else:
                logger.error(f"[{symbol}] 部分平仓未确认完成: 状态={final_status} | {final_order}")
                return False
        except Exception as e:
            logger.error(f"[{symbol}] 部分平仓异常: {e}")
            logger.error(f"[{symbol}] 堆栈: {traceback.format_exc()}")
            return False

    # ==================================================================
    # 状态查询
    # ==================================================================
    def get_status(self) -> Dict[str, Any]:
        """获取引擎状态"""
        cfg = load_ml_config()
        positions_info = []
        for sym, pos in self._positions.items():
            positions_info.append({
                "symbol": sym,
                "side": pos.side,
                "entry_price": pos.entry_price,
                "size": pos.size,
                "current_stop_price": pos.current_stop_price,
                "trailing_level": pos.trailing_level,
                "partial_tp_done": pos.partial_tp_done,
                "opened_at": pos.opened_at,
            })
        return {
            "running": self._running,
            "dry_run": self.dry_run,
            "iteration": self._iteration_count,
            "consecutive_errors": self._consecutive_errors,
            "last_error": self._last_error,
            "symbols": self._symbols,
            "positions": positions_info,
            "position_count": len(self._positions),
            "trade_count": self._trade_count,
            "start_time": self._start_time,
            "uptime": time.time() - self._start_time if self._start_time > 0 else 0,
            "config": cfg,
            "last_decisions": self._last_decisions,
        }


# 全局单例
ml_trader = MLTrader()
