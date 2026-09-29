"""
运行模式管理模块
- 管理两种运行模式: 原来的模式(original) / ML模式(ml)
- 切换模式时自动停止当前引擎, 启动目标引擎
- 确保同一时间只有一个引擎在运行
- 支持紧急停止
"""
import time
import threading
from typing import Optional, Dict, Any
from loguru import logger

# 模式常量
MODE_ORIGINAL = "original"  # 原来的17种策略
MODE_ML = "ml"              # 机器学习模式
MODE_NONE = "none"          # 都没运行


class ModeManager:
    """运行模式管理器"""

    def __init__(self):
        self._current_mode: str = MODE_NONE
        self._lock = threading.Lock()
        self._last_switch_time: float = 0
        self._switch_history: list = []

    @property
    def current_mode(self) -> str:
        return self._current_mode

    def get_status(self) -> Dict[str, Any]:
        """获取模式管理器状态"""
        original_running = False
        ml_running = False
        try:
            from trader import trader
            original_running = trader.is_running
        except Exception:
            pass
        try:
            from ml_trader import ml_trader
            ml_running = ml_trader.is_running
        except Exception:
            pass

        return {
            "current_mode": self._current_mode,
            "original_running": original_running,
            "ml_running": ml_running,
            "last_switch_time": self._last_switch_time,
            "switch_count": len(self._switch_history),
        }

    def switch_to(self, mode: str, symbols: Optional[list] = None,
                  strategy_name: Optional[str] = None) -> Dict[str, Any]:
        """切换到指定模式

        Args:
            mode: original / ml
            symbols: 监控的币种列表(ML模式用)
            strategy_name: 策略名(原来的模式用)

        Returns:
            {success, message, mode}
        """
        if mode not in (MODE_ORIGINAL, MODE_ML):
            return {"success": False, "message": f"未知模式: {mode}"}

        with self._lock:
            logger.info("=" * 60)
            logger.info(f"[模式切换] 从 {self._current_mode} 切换到 {mode}")

            # 第一步: 停止当前所有引擎
            stop_result = self._stop_all()
            if not stop_result["success"]:
                logger.error(f"[模式切换] 停止当前引擎失败: {stop_result['message']}")
                return {"success": False, "message": f"停止当前引擎失败: {stop_result['message']}"}

            # 第二步: 启动目标引擎
            if mode == MODE_ORIGINAL:
                start_result = self._start_original(strategy_name)
            else:
                start_result = self._start_ml(symbols)

            if not start_result["success"]:
                logger.error(f"[模式切换] 启动{mode}引擎失败: {start_result['message']}")
                self._current_mode = MODE_NONE
                return {"success": False, "message": f"启动{mode}引擎失败: {start_result['message']}"}

            # 切换成功
            self._current_mode = mode
            self._last_switch_time = time.time()
            self._switch_history.append({
                "from": self._current_mode,
                "to": mode,
                "time": self._last_switch_time,
            })
            if len(self._switch_history) > 50:
                self._switch_history = self._switch_history[-50:]

            mode_name = {"original": "原来的模式(17种策略)", "ml": "机器学习模式"}.get(mode, mode)
            logger.info(f"[模式切换] 成功! 当前模式: {mode_name}")
            logger.info("=" * 60)
            return {"success": True, "message": f"已切换到{mode_name}", "mode": mode}

    def stop_all(self) -> Dict[str, Any]:
        """停止所有引擎(紧急停止)"""
        with self._lock:
            logger.warning("[模式管理] 紧急停止所有引擎!")
            result = self._stop_all()
            if result["success"]:
                self._current_mode = MODE_NONE
                logger.warning("[模式管理] 所有引擎已停止")
            return result

    def _stop_all(self) -> Dict[str, Any]:
        """停止所有引擎(内部方法, 不加锁)"""
        errors = []

        # 停止原来的引擎
        try:
            from trader import trader
            if trader.is_running:
                logger.info("[模式切换] 停止原来的交易引擎...")
                trader.stop()
                logger.info("[模式切换] 原来的交易引擎已停止")
        except Exception as e:
            errors.append(f"停止原来的引擎失败: {e}")
            logger.error(f"[模式切换] 停止原来的引擎异常: {e}")

        # 停止ML引擎
        try:
            from ml_trader import ml_trader
            if ml_trader.is_running:
                logger.info("[模式切换] 停止ML交易引擎...")
                ml_trader.stop()
                logger.info("[模式切换] ML交易引擎已停止")
        except Exception as e:
            errors.append(f"停止ML引擎失败: {e}")
            logger.error(f"[模式切换] 停止ML引擎异常: {e}")

        # 等待一下确保完全停止
        time.sleep(1)

        if errors:
            return {"success": False, "message": "; ".join(errors)}
        return {"success": True, "message": "所有引擎已停止"}

    def _start_original(self, strategy_name: Optional[str] = None) -> Dict[str, Any]:
        """启动原来的交易引擎"""
        try:
            from trader import trader
            logger.info("[模式切换] 启动原来的交易引擎...")
            success = trader.start(strategy_name=strategy_name)
            if success:
                logger.info("[模式切换] 原来的交易引擎启动成功")
                return {"success": True, "message": "原来的交易引擎启动成功"}
            else:
                return {"success": False, "message": "原来的交易引擎启动失败"}
        except Exception as e:
            logger.error(f"[模式切换] 启动原来的引擎异常: {e}")
            return {"success": False, "message": str(e)}

    def _start_ml(self, symbols: Optional[list] = None) -> Dict[str, Any]:
        """启动ML交易引擎"""
        try:
            from ml_trader import ml_trader
            logger.info("[模式切换] 启动ML交易引擎...")
            success = ml_trader.start(symbols=symbols)
            if success:
                logger.info("[模式切换] ML交易引擎启动成功")
                return {"success": True, "message": "ML交易引擎启动成功"}
            else:
                return {"success": False, "message": "ML交易引擎启动失败"}
        except Exception as e:
            logger.error(f"[模式切换] 启动ML引擎异常: {e}")
            return {"success": False, "message": str(e)}


# 全局单例
mode_manager = ModeManager()
