"""
机器学习交易引擎 - 参数配置模块
- 保存/加载ML交易引擎的所有参数
- 内置3个预设方案: 保守型/平衡型/激进型
- 配置保存在 ml_config.json, 重启不丢失
"""
import os
import json
from typing import Dict, Any, Optional
from loguru import logger

# 配置文件路径
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "ml_config.json")

# ======================================================================
# 默认配置
# ======================================================================
DEFAULT_CONFIG: Dict[str, Any] = {
    # ===== 信号过滤 =====
    "trend_filter_enabled": True,           # 趋势过滤开关
    "trend_filter_ma_period": 200,          # 趋势过滤均线周期
    "multi_timeframe_enabled": True,         # 多周期共振开关
    "multi_timeframe_tfs": ["15m", "1h"],   # 多周期列表
    "volume_filter_enabled": False,           # 成交量过滤开关
    "volume_filter_ratio": 1.5,              # 成交量放大倍数(相对20日均量)

    # ===== 出场逻辑 =====
    "stop_loss_pct": 0.02,                   # 固定止损比例
    "take_profit_pct": 0.04,                 # 固定止盈比例
    "trailing_stop_enabled": True,            # 移动止损开关
    "trailing_stop_trigger_pct": 0.02,       # 移动止损触发比例(赚2%触发)
    "trailing_stop_move_to_pct": 0.0,        # 第一档移到的比例(0=成本价)
    "trailing_stop_step2_trigger_pct": 0.04, # 第二档触发比例(赚4%触发)
    "trailing_stop_step2_move_to_pct": 0.02, # 第二档移到的比例(保证赚2%)
    "partial_take_profit_enabled": True,      # 分批止盈开关
    "partial_take_profit_trigger_pct": 0.02, # 分批止盈触发比例(赚2%)
    "partial_take_profit_ratio": 0.5,        # 分批止盈平仓比例(平一半)

    # ===== 交易参数 =====
    "order_amount_usdt": 100,                 # 每笔下单金额(U)
    "leverage": 10,                            # 杠杆倍数
    "max_open_positions": 3,                   # 最大同时持仓数
    "entry_threshold": 0.65,                   # 开仓概率阈值
    "poll_interval": 15,                       # 轮询间隔(秒)

    # ===== 干跑模式(假钱测试) =====
    "dry_run_enabled": False,                  # 干跑模式开关(开启后用假钱，不扣真钱)
    "dry_run_initial_balance": 10000,         # 干跑初始资金(U)
    "dry_run_slippage": 0.001,                 # 干跑滑点(0.1%)
    # ===== 模型训练参数 =====
    "predict_horizon": 10,                      # 预测未来N根K线涨跌
    "label_threshold": 0.008,                    # 标签阈值: 涨跌超过这个比例才算信号
    "model_expire_days": 3,                      # 模型过期天数(超过后建议重训)
    "train_limit_15m": 8000,                     # 15m周期训练用K线数量
    "train_limit_1h": 2000,                      # 1h周期训练用K线数量
    "rf_max_depth": 8,                           # 随机森林最大深度
    "rf_n_estimators": 150,                      # 随机森林树数量
    "xgb_max_depth": 5,                          # XGBoost最大深度
    # ===== ADX震荡市过滤 =====
    "adx_filter_enabled": False,                  # ADX震荡市过滤开关
    "adx_threshold": 20,                          # ADX阈值(低于此值为震荡市, 不开仓)
    # ===== 自动重训 =====
    "auto_retrain_enabled": False,                # 自动重训开关
    "auto_retrain_interval_hours": 24,            # 自动重训间隔(小时)
}

# ======================================================================
# 预设方案
# ======================================================================
PRESETS: Dict[str, Dict[str, Any]] = {
    "conservative": {
        "name": "保守型",
        "description": "求稳, 交易机会少但胜率高",
        "config": {
            "trend_filter_enabled": True,
            "trend_filter_ma_period": 200,
            "multi_timeframe_enabled": True,
            "multi_timeframe_tfs": ["15m", "1h"],
            "volume_filter_enabled": True,
            "volume_filter_ratio": 2.0,
            "stop_loss_pct": 0.015,
            "take_profit_pct": 0.03,
            "trailing_stop_enabled": True,
            "trailing_stop_trigger_pct": 0.015,
            "trailing_stop_move_to_pct": 0.0,
            "trailing_stop_step2_trigger_pct": 0.03,
            "trailing_stop_step2_move_to_pct": 0.015,
            "partial_take_profit_enabled": True,
            "partial_take_profit_trigger_pct": 0.015,
            "partial_take_profit_ratio": 0.5,
            "order_amount_usdt": 50,
            "leverage": 5,
            "max_open_positions": 2,
            "entry_threshold": 0.70,
            "poll_interval": 20,
            "dry_run_enabled": False,
            "dry_run_initial_balance": 10000,
            "dry_run_slippage": 0.001,
            "predict_horizon": 10,
            "label_threshold": 0.01,
            "model_expire_days": 2,
            "train_limit_15m": 8000,
            "train_limit_1h": 2000,
            "rf_max_depth": 8,
            "rf_n_estimators": 150,
            "xgb_max_depth": 5,
            "adx_filter_enabled": True,
            "adx_threshold": 25,
            "auto_retrain_enabled": True,
            "auto_retrain_interval_hours": 12,
        }
    },
    "balanced": {
        "name": "平衡型",
        "description": "平衡, 推荐用这个",
        "config": {
            "trend_filter_enabled": True,
            "trend_filter_ma_period": 200,
            "multi_timeframe_enabled": True,
            "multi_timeframe_tfs": ["15m", "1h"],
            "volume_filter_enabled": True,
            "volume_filter_ratio": 1.5,
            "stop_loss_pct": 0.02,
            "take_profit_pct": 0.04,
            "trailing_stop_enabled": True,
            "trailing_stop_trigger_pct": 0.02,
            "trailing_stop_move_to_pct": 0.0,
            "trailing_stop_step2_trigger_pct": 0.04,
            "trailing_stop_step2_move_to_pct": 0.02,
            "partial_take_profit_enabled": True,
            "partial_take_profit_trigger_pct": 0.02,
            "partial_take_profit_ratio": 0.5,
            "order_amount_usdt": 100,
            "leverage": 10,
            "max_open_positions": 3,
            "entry_threshold": 0.65,
            "poll_interval": 15,
            "dry_run_enabled": False,
            "dry_run_initial_balance": 10000,
            "dry_run_slippage": 0.001,
            "predict_horizon": 10,
            "label_threshold": 0.008,
            "model_expire_days": 3,
            "train_limit_15m": 8000,
            "train_limit_1h": 2000,
            "rf_max_depth": 8,
            "rf_n_estimators": 150,
            "xgb_max_depth": 5,
            "adx_filter_enabled": False,
            "adx_threshold": 20,
            "auto_retrain_enabled": True,
            "auto_retrain_interval_hours": 24,
        }
    },
    "aggressive": {
        "name": "激进型",
        "description": "交易机会多, 波动大",
        "config": {
            "trend_filter_enabled": False,
            "trend_filter_ma_period": 200,
            "multi_timeframe_enabled": False,
            "multi_timeframe_tfs": ["15m"],
            "volume_filter_enabled": False,
            "volume_filter_ratio": 1.2,
            "stop_loss_pct": 0.03,
            "take_profit_pct": 0.06,
            "trailing_stop_enabled": True,
            "trailing_stop_trigger_pct": 0.03,
            "trailing_stop_move_to_pct": 0.0,
            "trailing_stop_step2_trigger_pct": 0.06,
            "trailing_stop_step2_move_to_pct": 0.03,
            "partial_take_profit_enabled": True,
            "partial_take_profit_trigger_pct": 0.03,
            "partial_take_profit_ratio": 0.4,
            "order_amount_usdt": 200,
            "leverage": 20,
            "max_open_positions": 5,
            "entry_threshold": 0.60,
            "poll_interval": 10,
            "dry_run_enabled": False,
            "dry_run_initial_balance": 10000,
            "dry_run_slippage": 0.001,
            "predict_horizon": 5,
            "label_threshold": 0.005,
            "model_expire_days": 5,
            "train_limit_15m": 8000,
            "train_limit_1h": 2000,
            "rf_max_depth": 10,
            "rf_n_estimators": 200,
            "xgb_max_depth": 6,
            "adx_filter_enabled": False,
            "adx_threshold": 15,
            "auto_retrain_enabled": False,
            "auto_retrain_interval_hours": 24,
        }
    },
}


def load_ml_config() -> Dict[str, Any]:
    """加载ML配置, 不存在则返回默认配置"""
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            # 合并默认配置(防止新增字段缺失)
            merged = DEFAULT_CONFIG.copy()
            merged.update(saved)
            return merged
    except Exception as e:
        logger.error(f"加载ML配置失败, 使用默认配置: {e}")
    return DEFAULT_CONFIG.copy()


def save_ml_config(config: Dict[str, Any]) -> bool:
    """保存ML配置到文件"""
    try:
        # 只保存已知字段, 防止脏数据
        to_save = {k: v for k, v in config.items() if k in DEFAULT_CONFIG}
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(to_save, f, indent=2, ensure_ascii=False)
        logger.info(f"ML配置已保存到 {CONFIG_FILE}")
        return True
    except Exception as e:
        logger.error(f"保存ML配置失败: {e}")
        return False


def apply_preset(preset_key: str) -> Optional[Dict[str, Any]]:
    """应用预设方案, 返回预设配置"""
    if preset_key not in PRESETS:
        logger.error(f"预设方案不存在: {preset_key}")
        return None
    preset = PRESETS[preset_key]
    config = preset["config"].copy()
    save_ml_config(config)
    logger.info(f"已应用预设方案: {preset['name']}")
    return config


def list_presets() -> Dict[str, Any]:
    """获取所有预设方案列表"""
    return {
        key: {
            "name": val["name"],
            "description": val["description"],
        }
        for key, val in PRESETS.items()
    }


def get_default_config() -> Dict[str, Any]:
    """获取默认配置"""
    return DEFAULT_CONFIG.copy()
