"""
机器学习学习模块(独立新增, 不改动原有代码)
功能:
  1. 特征工程: 从K线计算40+技术指标特征
  2. 数据获取: 从OKX拉历史K线
  3. 模型训练: 随机森林/XGBoost分类模型, 预测未来N根K线涨跌
  4. 模型保存/加载: 每个币种单独保存到 ml_models/ 目录
  5. 回测: 训练完自动回测, 计算胜率/盈亏比/最大回撤
  6. 训练队列: 后台线程异步训练, 不阻塞主程序
  7. 特征重要性: 返回模型主要靠哪些指标判断
"""
import os
import time
import json
import threading
import traceback
from typing import List, Dict, Any, Optional, Tuple
from loguru import logger
import pandas as pd
import numpy as np

# 模型保存目录
MODEL_DIR = os.path.join(os.path.dirname(__file__), "ml_models")
os.makedirs(MODEL_DIR, exist_ok=True)

# 元数据保存文件(记录每个模型的训练时间、回测结果等)
META_FILE = os.path.join(MODEL_DIR, "models_meta.json")

# 支持的模型类型
SUPPORTED_MODELS = ["random_forest", "xgboost", "logistic_regression"]
MODEL_NAMES = {
    "random_forest": "随机森林",
    "xgboost": "XGBoost",
    "logistic_regression": "逻辑回归",
}

# 模型选择配置文件(用户选了哪些模型)
MODEL_CONFIG_FILE = os.path.join(MODEL_DIR, "model_config.json")
# 默认选中的模型
DEFAULT_SELECTED_MODELS = ["random_forest", "xgboost"]


def _normalize_symbol(symbol: str) -> str:
    """标准化symbol格式，统一用于模型保存和加载
    把 CRV-USDT-SWAP / CRV/USDT / CRV_USDT 都统一成 CRV-USDT
    """
    s = symbol.upper().strip()
    # 去掉合约后缀
    for suffix in ["-SWAP", "_SWAP", "-PERP", "_PERP"]:
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    # 统一分隔符为 -
    s = s.replace("/", "-").replace("_", "-").replace(":", "-")
    return s


def _model_safe_name(symbol: str) -> str:
    """生成模型文件名用的安全名称"""
    return _normalize_symbol(symbol).replace("-", "_")


def _model_file_name(symbol: str, model_type: str, timeframe: str = "15m") -> str:
    """生成带模型类型和周期的文件名, 如 BTC_USDT_15m_random_forest.pkl"""
    return f"{_model_safe_name(symbol)}_{timeframe}_{model_type}.pkl"


def load_model_config() -> Dict[str, Any]:
    """加载模型选择配置(用户选了哪些模型)"""
    try:
        if os.path.exists(MODEL_CONFIG_FILE):
            with open(MODEL_CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
                selected = config.get("selected_models", DEFAULT_SELECTED_MODELS)
                # 过滤掉不支持的模型
                selected = [m for m in selected if m in SUPPORTED_MODELS]
                return {"selected_models": selected if selected else DEFAULT_SELECTED_MODELS}
    except Exception:
        pass
    return {"selected_models": DEFAULT_SELECTED_MODELS}


def save_model_config(selected_models: List[str]) -> bool:
    """保存模型选择配置"""
    try:
        os.makedirs(MODEL_DIR, exist_ok=True)
        selected = [m for m in selected_models if m in SUPPORTED_MODELS]
        if not selected:
            selected = DEFAULT_SELECTED_MODELS
        config = {"selected_models": selected}
        with open(MODEL_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"[ML] 保存模型配置失败: {e}")
        return False

# 预测未来几根K线
PREDICT_HORIZON = 10
# 标签阈值: 涨跌幅超过这个比例才算涨/跌
LABEL_THRESHOLD = 0.008
# 训练用K线数量(按周期配置)
TRAIN_LIMIT_15M = 8000
TRAIN_LIMIT_1H = 2000
TRAIN_LIMIT_DEFAULT = 2000
# 模型过期天数
MODEL_EXPIRE_DAYS = 3

# 开仓概率阈值
ENTRY_THRESHOLD = 0.65

# 平仓反向概率阈值
CLOSE_THRESHOLD = 0.60

def get_ml_settings():
    """从ml_config读取最新的ML参数配置"""
    try:
        from ml_config import load_ml_config
        cfg = load_ml_config()
        return {
            "predict_horizon": cfg.get("predict_horizon", PREDICT_HORIZON),
            "label_threshold": cfg.get("label_threshold", LABEL_THRESHOLD),
            "model_expire_days": cfg.get("model_expire_days", MODEL_EXPIRE_DAYS),
            "rf_max_depth": cfg.get("rf_max_depth", 8),
            "rf_n_estimators": cfg.get("rf_n_estimators", 150),
            "xgb_max_depth": cfg.get("xgb_max_depth", 5),
            "train_limit_15m": cfg.get("train_limit_15m", TRAIN_LIMIT_15M),
            "train_limit_1h": cfg.get("train_limit_1h", TRAIN_LIMIT_1H),
            "take_profit_pct": cfg.get("take_profit_pct", 0.04),
            "stop_loss_pct": cfg.get("stop_loss_pct", 0.02),
        }
    except Exception:
        return {
            "predict_horizon": PREDICT_HORIZON,
            "label_threshold": LABEL_THRESHOLD,
            "model_expire_days": MODEL_EXPIRE_DAYS,
            "rf_max_depth": 8,
            "rf_n_estimators": 150,
            "xgb_max_depth": 5,
            "train_limit_15m": TRAIN_LIMIT_15M,
            "train_limit_1h": TRAIN_LIMIT_1H,
            "take_profit_pct": 0.04,
            "stop_loss_pct": 0.02,
        }

def get_train_limit(timeframe="15m"):
    settings = get_ml_settings()
    if timeframe == "15m":
        return settings.get("train_limit_15m", TRAIN_LIMIT_15M)
    elif timeframe == "1h":
        return settings.get("train_limit_1h", TRAIN_LIMIT_1H)
    return TRAIN_LIMIT_DEFAULT


# ======================================================================
# 特征工程
# ======================================================================
def calc_features(df: pd.DataFrame) -> pd.DataFrame:
    """从K线数据计算技术指标特征
    输入: df包含 open/high/low/close/volume
    输出: 新增40+特征列
    """
    try:
        import ta
    except ImportError:
        logger.error("[ML] ta库未安装, 无法计算特征")
        return df

    df = df.copy()
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # ===== 1. 价格特征 =====
    df["ret_1"] = close.pct_change(1)
    df["ret_3"] = close.pct_change(3)
    df["ret_5"] = close.pct_change(5)
    df["ret_10"] = close.pct_change(10)
    df["ret_20"] = close.pct_change(20)

    # 波动率
    df["volatility_5"] = df["ret_1"].rolling(5).std()
    df["volatility_10"] = df["ret_1"].rolling(10).std()
    df["volatility_20"] = df["ret_1"].rolling(20).std()

    # 高低价差
    df["high_low_ratio"] = (high - low) / close
    df["body_ratio"] = (close - df["open"]).abs() / close
    df["upper_shadow"] = (high - close) / close
    df["lower_shadow"] = (close - low) / close

    # ===== 2. 均线特征 =====
    for period in [5, 10, 20, 50]:
        df[f"ma_{period}"] = close.rolling(period).mean()
        df[f"ma_{period}_dist"] = (close - df[f"ma_{period}"]) / df[f"ma_{period}"]

    # 均线排列
    df["ma_trend"] = np.where(
        (df["ma_5"] > df["ma_10"]) & (df["ma_10"] > df["ma_20"]), 1,
        np.where((df["ma_5"] < df["ma_10"]) & (df["ma_10"] < df["ma_20"]), -1, 0)
    )

    # EMA
    df["ema_12"] = close.ewm(span=12, adjust=False).mean()
    df["ema_26"] = close.ewm(span=26, adjust=False).mean()
    df["ema_dist"] = (df["ema_12"] - df["ema_26"]) / close

    # ===== 3. RSI =====
    df["rsi_6"] = ta.momentum.RSIIndicator(close, window=6).rsi()
    df["rsi_14"] = ta.momentum.RSIIndicator(close, window=14).rsi()
    df["rsi_28"] = ta.momentum.RSIIndicator(close, window=28).rsi()
    df["rsi_change"] = df["rsi_14"] - df["rsi_14"].shift(3)

    # ===== 4. MACD =====
    macd = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_hist"] = macd.macd_diff()
    df["macd_hist_change"] = df["macd_hist"] - df["macd_hist"].shift(2)

    # ===== 5. KDJ =====
    stoch = ta.momentum.StochasticOscillator(high, low, close, window=14, smooth_window=3)
    df["kdj_k"] = stoch.stoch()
    df["kdj_d"] = stoch.stoch_signal()
    df["kdj_j"] = 3 * df["kdj_k"] - 2 * df["kdj_d"]

    # ===== 6. 布林带 =====
    bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_middle"] = bb.bollinger_mavg()
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_middle"]
    df["bb_position"] = (close - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"])

    # ===== 7. ATR =====
    atr = ta.volatility.AverageTrueRange(high, low, close, window=14)
    df["atr_14"] = atr.average_true_range()
    df["atr_ratio"] = df["atr_14"] / close

    # ===== 8. CCI =====
    df["cci_14"] = ta.trend.CCIIndicator(high, low, close, window=14).cci()

    # ===== 9. 成交量特征 =====
    df["vol_ma5"] = volume.rolling(5).mean()
    df["vol_ma10"] = volume.rolling(10).mean()
    df["vol_ma20"] = volume.rolling(20).mean()
    df["vol_ratio_5"] = volume / df["vol_ma5"]
    df["vol_ratio_10"] = volume / df["vol_ma10"]
    df["vol_change"] = volume.pct_change(3)

    # 量价配合
    df["vol_price_corr"] = df["ret_1"].rolling(10).corr(volume.pct_change(1))

    # ===== 10. 支撑阻力位置 =====
    df["high_20"] = high.rolling(20).max()
    df["low_20"] = low.rolling(20).min()
    df["high_50"] = high.rolling(50).max()
    df["low_50"] = low.rolling(50).min()
    df["position_20"] = (close - df["low_20"]) / (df["high_20"] - df["low_20"])
    df["position_50"] = (close - df["low_50"]) / (df["high_50"] - df["low_50"])

    # 替换inf和nan
    df = df.replace([np.inf, -np.inf], np.nan)
    # 不允许用未来K线回填历史缺失值，避免训练/回测偷看未来。
    # 保留NaN，训练阶段统一dropna；最新预测数据不足时返回None。

    return df


def get_feature_columns() -> List[str]:
    """获取用于训练的特征列名"""
    return [
        "ret_1", "ret_3", "ret_5", "ret_10", "ret_20",
        "volatility_5", "volatility_10", "volatility_20",
        "high_low_ratio", "body_ratio", "upper_shadow", "lower_shadow",
        "ma_5_dist", "ma_10_dist", "ma_20_dist", "ma_50_dist",
        "ma_trend", "ema_dist",
        "rsi_6", "rsi_14", "rsi_28", "rsi_change",
        "macd", "macd_signal", "macd_hist", "macd_hist_change",
        "kdj_k", "kdj_d", "kdj_j",
        "bb_width", "bb_position",
        "atr_ratio", "cci_14",
        "vol_ratio_5", "vol_ratio_10", "vol_change", "vol_price_corr",
        "position_20", "position_50",
    ]


# ======================================================================
# 数据准备
# ======================================================================
def prepare_training_data(ohlcv: List[List[float]],
                          predict_horizon: Optional[int] = None,
                          label_threshold: Optional[float] = None) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """从已收盘K线准备训练数据。

    标签与真实交易方式一致：在未来N根K线内，判断先触发TP还是SL。
    1=先到多头TP，0=先到空头TP；同一根K线同时触发TP/SL的样本直接丢弃，
    避免用OHLC无法还原的盘中先后顺序制造虚假标签。
    """
    settings = get_ml_settings()
    horizon = int(predict_horizon if predict_horizon is not None else settings["predict_horizon"])
    tp = float(settings.get("take_profit_pct", 0.04))
    sl = float(settings.get("stop_loss_pct", 0.02))
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = calc_features(df)

    labels = np.full(len(df), np.nan)
    max_i = len(df) - horizon
    for i in range(max(0, max_i)):
        entry = float(df.iloc[i]["close"])
        if entry <= 0:
            continue
        tp_price = entry * (1.0 + tp)
        sl_price = entry * (1.0 - sl)
        short_tp_price = entry * (1.0 - tp)
        short_sl_price = entry * (1.0 + sl)
        long_hit = None
        short_hit = None
        for j in range(i + 1, min(i + horizon + 1, len(df))):
            hi = float(df.iloc[j]["high"])
            lo = float(df.iloc[j]["low"])
            if long_hit is None:
                long_tp = hi >= tp_price
                long_sl = lo <= sl_price
                if long_tp and long_sl:
                    long_hit = "ambiguous"
                elif long_tp:
                    long_hit = True
                elif long_sl:
                    long_hit = False
            if short_hit is None:
                short_tp = lo <= short_tp_price
                short_sl = hi >= short_sl_price
                if short_tp and short_sl:
                    short_hit = "ambiguous"
                elif short_tp:
                    short_hit = True
                elif short_sl:
                    short_hit = False
            if long_hit is not None and short_hit is not None:
                break
        # 只保留能明确判断交易结果的样本；多头先TP=1，空头先TP=0。
        if long_hit is True:
            labels[i] = 1.0
        elif short_hit is True:
            labels[i] = 0.0

    df["label"] = labels
    df = df.dropna(subset=get_feature_columns() + ["label"])
    if len(df) == 0:
        return np.empty((0, len(get_feature_columns()))), np.empty((0,), dtype=int), get_feature_columns()

    feature_cols = get_feature_columns()
    X = df[feature_cols].values
    y = df["label"].astype(int).values
    return X, y, feature_cols


def prepare_latest_features(ohlcv: List[List[float]]) -> Optional[np.ndarray]:
    """准备最新一根K线的特征用于预测"""
    if len(ohlcv) < 60:
        return None
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = calc_features(df)
    feature_cols = get_feature_columns()
    latest = df[feature_cols].iloc[-1].values.reshape(1, -1)
    # 严格与训练保持一致：最新特征不完整时不预测，绝不把缺失值伪装成0。
    if not np.isfinite(latest).all():
        logger.warning("[ML] 最新K线特征不完整，跳过本次预测")
        return None
    return latest


# ======================================================================
# 模型训练与保存
# ======================================================================
def train_model(ohlcv: List[List[float]], symbol: str,
                model_type: str = "random_forest", timeframe: str = "15m") -> Tuple[Optional[Any], Dict[str, Any]]:
    """训练模型
    Args:
        ohlcv: K线数据
        symbol: 币种
        model_type: 模型类型 random_forest / xgboost / logistic_regression
        timeframe: K线周期, 如 15m / 1h / 4h
    Returns:
        (model, meta信息)
    """
    if model_type not in SUPPORTED_MODELS:
        return None, {"error": f"不支持的模型类型: {model_type}, 支持: {SUPPORTED_MODELS}"}

    try:
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import accuracy_score
    except ImportError:
        logger.error("[ML] scikit-learn未安装, 请运行: pip install scikit-learn joblib")
        return None, {"error": "scikit-learn not installed"}

    if len(ohlcv) < 100:
        return None, {"error": f"K线数据不足({len(ohlcv)}根), 至少需要100根"}

    logger.info(f"[ML] [{symbol}] 开始准备训练数据...")
    settings = get_ml_settings()
    X, y, feature_names = prepare_training_data(ohlcv)

    if len(X) < 50:
        return None, {"error": f"有效训练样本不足({len(X)}条), 至少需要50条"}

    logger.info(f"[ML] [{symbol}] 训练样本: {len(X)}条, 特征: {len(feature_names)}个, 上涨占比: {y.mean()*100:.1f}%")

    # 严格按时间切分，并在训练集末端留出一个预测窗口，避免标签跨越训练/测试边界。
    split_idx = int(len(X) * 0.8)
    purge = min(int(settings["predict_horizon"]), max(0, split_idx - 1))
    train_end = split_idx - purge
    if train_end < 25 or len(X) - split_idx < 10:
        return None, {"error": f"时间切分后有效训练/测试样本不足: train={train_end}, test={len(X)-split_idx}"}
    X_train, X_test = X[:train_end], X[split_idx:]
    y_train, y_test = y[:train_end], y[split_idx:]

    # 根据模型类型训练
    model = None
    model_name = MODEL_NAMES.get(model_type, model_type)

    if model_type == "random_forest":
        try:
            from sklearn.ensemble import RandomForestClassifier
        except ImportError:
            return None, {"error": "RandomForestClassifier导入失败"}
        logger.info(f"[ML] [{symbol}] 开始训练随机森林模型...")
        model = RandomForestClassifier(
            n_estimators=settings["rf_n_estimators"],
            max_depth=settings["rf_max_depth"],
            min_samples_leaf=3,
            min_samples_split=5,
            random_state=42,
            n_jobs=-1,
            class_weight="balanced",
        )
        model.fit(X_train, y_train)

    elif model_type == "xgboost":
        try:
            from xgboost import XGBClassifier
        except ImportError:
            logger.error("[ML] xgboost未安装, 请运行: pip install xgboost")
            return None, {"error": "xgboost not installed, 请运行: pip install xgboost"}
        logger.info(f"[ML] [{symbol}] 开始训练XGBoost模型...")
        # 计算样本权重，处理类别不平衡
        from sklearn.utils.class_weight import compute_sample_weight
        sample_weights = compute_sample_weight(class_weight="balanced", y=y_train)
        model = XGBClassifier(
            n_estimators=300,
            max_depth=settings["xgb_max_depth"],
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            n_jobs=-1,
            eval_metric="logloss",
            verbosity=0,
        )
        model.fit(X_train, y_train, sample_weight=sample_weights)

    elif model_type == "logistic_regression":
        try:
            from sklearn.linear_model import LogisticRegression
            from sklearn.preprocessing import StandardScaler
        except ImportError:
            return None, {"error": "LogisticRegression导入失败"}
        logger.info(f"[ML] [{symbol}] 开始训练逻辑回归模型...")
        # 逻辑回归需要特征标准化
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_scaled = scaler.transform(X_test)
        model = LogisticRegression(
            C=1.0,
            max_iter=1000,
            class_weight="balanced",
            random_state=42,
            n_jobs=-1,
        )
        model.fit(X_train_scaled, y_train)
        # 把scaler存到model里，预测时用
        model.scaler = scaler
        model._X_test_scaled = X_test_scaled

    if model is None:
        return None, {"error": f"模型训练失败: {model_type}"}

    # 测试集准确率
    if model_type == "logistic_regression":
        y_pred = model.predict(model._X_test_scaled)
    else:
        y_pred = model.predict(X_test)
    test_accuracy = accuracy_score(y_test, y_pred)
    logger.info(f"[ML] [{symbol}] {model_name} 测试集准确率: {test_accuracy*100:.1f}%")

    # 特征重要性
    feature_imp = []
    if model_type == "random_forest" or model_type == "xgboost":
        importances = model.feature_importances_
        feature_imp = sorted(
            [{"name": name, "importance": round(float(imp), 4)} for name, imp in zip(feature_names, importances)],
            key=lambda x: x["importance"], reverse=True
        )
    elif model_type == "logistic_regression":
        # 逻辑回归用系数绝对值作为特征重要性
        coefs = abs(model.coef_[0])
        feature_imp = sorted(
            [{"name": name, "importance": round(float(c), 4)} for name, c in zip(feature_names, coefs)],
            key=lambda x: x["importance"], reverse=True
        )

    meta = {
        "symbol": symbol,
        "train_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "train_timestamp": time.time(),
        "train_samples": len(X),
        "train_data_count": len(ohlcv),
        "feature_count": len(feature_names),
        "test_accuracy": round(float(test_accuracy), 4),
        "feature_importance": feature_imp[:20],
        "predict_horizon": settings["predict_horizon"],
        "label_threshold": settings["label_threshold"],
        "model_type": model_type,
        "model_name": model_name,
        "timeframe": timeframe,
        "target_type": "未来N根内TP/SL先触发结果",
        "target_take_profit_pct": float(settings.get("take_profit_pct", 0.04)),
        "target_stop_loss_pct": float(settings.get("stop_loss_pct", 0.02)),
    }

    return model, meta


def save_model(model: Any, meta: Dict[str, Any], symbol: str,
               model_type: Optional[str] = None, timeframe: Optional[str] = None) -> bool:
    """保存模型和元数据
    Args:
        model: 训练好的模型
        meta: 元数据
        symbol: 币种
        model_type: 模型类型, 不传则从meta里取
        timeframe: K线周期, 不传则从meta里取
    """
    try:
        import joblib
    except ImportError:
        logger.error("[ML] joblib未安装, 无法保存模型")
        return False

    try:
        if model_type is None:
            model_type = meta.get("model_type", "random_forest")
        if model_type not in SUPPORTED_MODELS:
            model_type = "random_forest"
        if timeframe is None:
            timeframe = meta.get("timeframe", "15m")

        # 文件名包含周期和模型类型, 如 BTC_USDT_15m_random_forest.pkl
        model_path = os.path.join(MODEL_DIR, _model_file_name(symbol, model_type, timeframe))
        os.makedirs(MODEL_DIR, exist_ok=True)
        # 原子保存: 先写临时文件, 再替换, 防止预测时读到半写的文件
        tmp_path = model_path + f".tmp.{os.getpid()}.{int(time.time()*1000)}"
        joblib.dump(model, tmp_path)
        # Windows下文件被占用时replace可能失败, 重试5次
        saved = False
        for attempt in range(5):
            try:
                os.replace(tmp_path, model_path)
                saved = True
                break
            except Exception:
                time.sleep(0.3)
        if not saved:
            # 重试失败, 直接写(可能有风险但比丢模型好)
            try:
                os.replace(tmp_path, model_path)
            except Exception:
                joblib.dump(model, model_path)
        logger.info(f"[ML] [{symbol}] [{MODEL_NAMES.get(model_type, model_type)}] 模型已保存: {model_path}")

        # 保存元数据, key包含周期和模型类型, 如 BTC-USDT_15m_random_forest
        all_meta = load_all_meta()
        norm_symbol = _normalize_symbol(symbol)
        meta_key = f"{norm_symbol}_{timeframe}_{model_type}"
        meta["symbol"] = norm_symbol
        meta["model_type"] = model_type
        meta["timeframe"] = timeframe
        meta["model_name"] = MODEL_NAMES.get(model_type, model_type)
        all_meta[meta_key] = meta
        # meta文件也用原子写入
        meta_tmp = META_FILE + f".tmp.{os.getpid()}"
        with open(meta_tmp, "w", encoding="utf-8") as f:
            json.dump(all_meta, f, ensure_ascii=False, indent=2)
        try:
            os.replace(meta_tmp, META_FILE)
        except Exception:
            with open(META_FILE, "w", encoding="utf-8") as f:
                json.dump(all_meta, f, ensure_ascii=False, indent=2)

        return True
    except Exception as e:
        logger.error(f"[ML] [{symbol}] 保存模型失败: {e}")
        logger.error(traceback.format_exc())
        return False


def load_model(symbol: str, model_type: str = "random_forest", timeframe: str = "15m") -> Optional[Any]:
    """加载指定币种的模型
    Args:
        symbol: 币种
        model_type: 模型类型 random_forest / xgboost / logistic_regression
        timeframe: K线周期, 如 15m / 1h / 4h
    """
    try:
        import joblib
    except ImportError:
        return None

    try:
        if model_type not in SUPPORTED_MODELS:
            model_type = "random_forest"

        # 新标准格式: 带周期和模型类型, 如 BTC_USDT_15m_random_forest.pkl
        model_path = os.path.join(MODEL_DIR, _model_file_name(symbol, model_type, timeframe))
        if os.path.exists(model_path):
            model = joblib.load(model_path)
            return model

        # 兼容旧格式: 不带周期, 如 BTC_USDT_random_forest.pkl (只对15m兼容)
        if timeframe == "15m":
            old_path = os.path.join(MODEL_DIR, f"{_model_safe_name(symbol)}_{model_type}.pkl")
            if os.path.exists(old_path):
                logger.info(f"[ML] [{symbol}] [{timeframe}] 使用旧格式模型(不带周期): {old_path}")
                model = joblib.load(old_path)
                return model

        # 兼容更旧格式: 不带模型类型, 如 BTC_USDT.pkl (只对random_forest兼容)
        if model_type == "random_forest":
            safe_name = _model_safe_name(symbol)
            old_path = os.path.join(MODEL_DIR, f"{safe_name}.pkl")
            if os.path.exists(old_path):
                logger.info(f"[ML] [{symbol}] 使用旧格式模型: {old_path}")
                model = joblib.load(old_path)
                return model
            # 兼容更旧的格式: 带_SWAP后缀
            old_path2 = os.path.join(MODEL_DIR, f"{safe_name}_SWAP.pkl")
            if os.path.exists(old_path2):
                logger.info(f"[ML] [{symbol}] 使用旧格式模型: {old_path2}")
                model = joblib.load(old_path2)
                return model

        return None
    except Exception as e:
        logger.warning(f"[ML] [{symbol}] 加载模型失败: {e}")
        return None


def load_all_meta() -> Dict[str, Any]:
    """加载所有模型元数据"""
    try:
        if os.path.exists(META_FILE):
            with open(META_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def get_model_meta(symbol: str, model_type: Optional[str] = None, timeframe: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """获取指定币种的模型元数据
    Args:
        symbol: 币种
        model_type: 模型类型, 不传则返回第一个找到的
        timeframe: K线周期, 不传则返回第一个找到的
    """
    all_meta = load_all_meta()
    norm_symbol = _normalize_symbol(symbol)

    # 指定了模型类型和周期
    if model_type and timeframe:
        if model_type not in SUPPORTED_MODELS:
            model_type = "random_forest"
        meta_key = f"{norm_symbol}_{timeframe}_{model_type}"
        if meta_key in all_meta:
            return all_meta[meta_key]
        # 兼容旧格式: 不带周期
        if timeframe == "15m":
            old_key = f"{norm_symbol}_{model_type}"
            if old_key in all_meta:
                meta = all_meta[old_key].copy()
                meta["timeframe"] = "15m"
                return meta

    # 指定了模型类型, 没指定周期
    if model_type:
        if model_type not in SUPPORTED_MODELS:
            model_type = "random_forest"
        # 先找带周期的
        for tf in ["15m", "1h", "4h", "1d"]:
            meta_key = f"{norm_symbol}_{tf}_{model_type}"
            if meta_key in all_meta:
                return all_meta[meta_key]
        # 兼容旧格式: 不带周期
        old_key = f"{norm_symbol}_{model_type}"
        if old_key in all_meta:
            meta = all_meta[old_key].copy()
            meta["timeframe"] = "15m"
            return meta

    # 没指定模型类型, 按优先级找
    for mt in SUPPORTED_MODELS:
        meta_key = f"{norm_symbol}_{mt}"
        if meta_key in all_meta:
            return all_meta[meta_key]

    # 兼容旧格式: 不带模型类型的key
    if norm_symbol in all_meta:
        return all_meta[norm_symbol]
    old_symbol = norm_symbol + "-SWAP"
    if old_symbol in all_meta:
        return all_meta[old_symbol]

    return None


def delete_model(symbol: str, model_type: Optional[str] = None, timeframe: Optional[str] = None) -> bool:
    """删除指定币种的模型
    Args:
        symbol: 币种
        model_type: 模型类型, 不传则删除该币所有模型
        timeframe: K线周期, 不传则删除该币所有周期的模型
    """
    try:
        norm_symbol = _normalize_symbol(symbol)
        all_meta = load_all_meta()
        deleted = False

        if model_type and model_type in SUPPORTED_MODELS and timeframe:
            # 删除指定模型指定周期
            model_path = os.path.join(MODEL_DIR, _model_file_name(symbol, model_type, timeframe))
            if os.path.exists(model_path):
                os.remove(model_path)
                deleted = True
            meta_key = f"{norm_symbol}_{timeframe}_{model_type}"
            if meta_key in all_meta:
                del all_meta[meta_key]
                deleted = True
        elif model_type and model_type in SUPPORTED_MODELS:
            # 删除指定模型所有周期
            for tf in ["15m", "1h", "4h", "1d"]:
                model_path = os.path.join(MODEL_DIR, _model_file_name(symbol, model_type, tf))
                if os.path.exists(model_path):
                    os.remove(model_path)
                    deleted = True
                meta_key = f"{norm_symbol}_{tf}_{model_type}"
                if meta_key in all_meta:
                    del all_meta[meta_key]
                    deleted = True
            # 兼容旧格式: 不带周期
            model_path = os.path.join(MODEL_DIR, f"{_model_safe_name(symbol)}_{model_type}.pkl")
            if os.path.exists(model_path):
                os.remove(model_path)
                deleted = True
            meta_key = f"{norm_symbol}_{model_type}"
            if meta_key in all_meta:
                del all_meta[meta_key]
                deleted = True
        else:
            # 删除该币所有模型所有周期
            for mt in SUPPORTED_MODELS:
                for tf in ["15m", "1h", "4h", "1d"]:
                    model_path = os.path.join(MODEL_DIR, _model_file_name(symbol, mt, tf))
                    if os.path.exists(model_path):
                        os.remove(model_path)
                        deleted = True
                    meta_key = f"{norm_symbol}_{tf}_{mt}"
                    if meta_key in all_meta:
                        del all_meta[meta_key]
                        deleted = True
                # 兼容旧格式: 不带周期
                model_path = os.path.join(MODEL_DIR, f"{_model_safe_name(symbol)}_{mt}.pkl")
                if os.path.exists(model_path):
                    os.remove(model_path)
                    deleted = True
                meta_key = f"{norm_symbol}_{mt}"
                if meta_key in all_meta:
                    del all_meta[meta_key]
                    deleted = True
            # 兼容旧格式
            safe_name = _model_safe_name(symbol)
            for old_file in [f"{safe_name}.pkl", f"{safe_name}_SWAP.pkl"]:
                old_path = os.path.join(MODEL_DIR, old_file)
                if os.path.exists(old_path):
                    os.remove(old_path)
                    deleted = True
            for old_key in [norm_symbol, norm_symbol + "-SWAP"]:
                if old_key in all_meta:
                    del all_meta[old_key]
                    deleted = True

        if deleted:
            with open(META_FILE, "w", encoding="utf-8") as f:
                json.dump(all_meta, f, ensure_ascii=False, indent=2)
            logger.info(f"[ML] [{symbol}] 模型已删除")
        return True
    except Exception as e:
        logger.error(f"[ML] [{symbol}] 删除模型失败: {e}")
        return False
        logger.error(f"[ML] [{symbol}] 删除模型失败: {e}")
        return False


def is_model_expired(symbol: str, model_type: Optional[str] = None,
                     expire_days: Optional[int] = None, timeframe: Optional[str] = None) -> bool:
    """检查模型是否过期
    Args:
        symbol: 币种
        model_type: 模型类型, 不传则检查第一个找到的
        expire_days: 过期天数, 不传则从配置读取
        timeframe: K线周期, 不传则检查第一个找到的
    """
    if expire_days is None:
        expire_days = get_ml_settings()["model_expire_days"]
    meta = get_model_meta(symbol, model_type, timeframe)
    if not meta:
        return True
    train_time = meta.get("train_timestamp", 0)
    if not train_time:
        return True
    # 刚训练完的模型(1小时内)强制不过期, 避免训练耗时导致立刻标记过期
    if time.time() - train_time < 3600:
        return False
    return (time.time() - train_time) > expire_days * 86400


def is_model_compatible(symbol: str, model_type: Optional[str] = None,
                        timeframe: Optional[str] = None) -> Tuple[bool, str]:
    """检查模型是否与当前配置兼容(预测周期、标签阈值)
    Returns:
        (is_compatible, reason)
    """
    meta = get_model_meta(symbol, model_type, timeframe)
    if not meta:
        return False, "模型不存在"
    settings = get_ml_settings()
    # 旧模型meta里没有这些字段, 直接判定不兼容
    meta_horizon = meta.get("predict_horizon")
    meta_threshold = meta.get("label_threshold")
    if meta_horizon is None or meta_threshold is None:
        return False, "旧版本模型(缺少训练参数), 请重新训练"
    if meta_horizon != settings["predict_horizon"]:
        return False, f"预测周期不匹配(模型{meta_horizon}根 vs 当前{settings['predict_horizon']}根), 请重新训练"
    if abs(meta_threshold - settings["label_threshold"]) > 1e-6:
        return False, f"标签阈值不匹配(模型{meta_threshold} vs 当前{settings['label_threshold']}), 请重新训练"
    meta_tp = meta.get("target_take_profit_pct")
    meta_sl = meta.get("target_stop_loss_pct")
    if meta_tp is None or meta_sl is None:
        return False, "旧版本模型(缺少TP/SL训练参数), 请重新训练"
    if abs(float(meta_tp) - float(settings.get("take_profit_pct", 0.04))) > 1e-6:
        return False, f"训练TP不匹配(模型{meta_tp} vs 当前{settings.get('take_profit_pct', 0.04)}), 请重新训练"
    if abs(float(meta_sl) - float(settings.get("stop_loss_pct", 0.02))) > 1e-6:
        return False, f"训练SL不匹配(模型{meta_sl} vs 当前{settings.get('stop_loss_pct', 0.02)}), 请重新训练"
    return True, "兼容"


def calc_adx(ohlcv: List[List[float]], period: int = 14) -> Optional[float]:
    """计算ADX指标(平均趋向指数), 用于判断趋势强度
    ADX>25: 强趋势; ADX<20: 震荡市; 20-25之间: 弱趋势
    Returns:
        ADX值, 计算失败返回None
    """
    try:
        import ta
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        adx_indicator = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=period)
        adx = float(adx_indicator.adx().iloc[-1])
        if np.isnan(adx):
            return None
        return adx
    except Exception as e:
        logger.warning(f"[ML] 计算ADX失败: {e}")
        return None


# ======================================================================
# 回测
# ======================================================================
def backtest_model(ohlcv: List[List[float]], model: Any,
                   stop_loss_pct: float = 0.02, take_profit_pct: float = 0.04) -> Dict[str, Any]:
    """严格时间外样本回测：只使用模型未参与训练的后20%数据。

    信号在一根已完成K线收盘后产生，下一根K线开盘成交，并模拟滑点/手续费。
    止盈止损使用下一根及之后K线的high/low；同一根同时触发时按先后未知处理为SL优先，
    防止回测虚高。
    """
    if len(ohlcv) < 150 or model is None:
        return {"error": "数据不足或模型为空"}
    try:
        BACKTEST_THRESHOLD = 0.52
        FEE_RATE = 0.001
        SLIPPAGE = 0.0005
        POSITION_PCT = 0.2
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df = calc_features(df)
        feature_cols = get_feature_columns()
        split_idx = int(len(df) * 0.8)
        test_df = df.iloc[split_idx:].reset_index(drop=True)
        if len(test_df) < 10:
            return {"error": "严格OOS回测数据不足"}

        initial_balance = 10000.0
        balance = initial_balance
        position = None
        trades = []
        equity_curve = [initial_balance]

        for i in range(len(test_df) - 1):
            row = test_df.iloc[i]
            next_row = test_df.iloc[i + 1]

            # 先用当前已收盘K线发信号，下一根开盘成交。
            if position:
                hi = float(next_row["high"]); lo = float(next_row["low"])
                ep = position["entry_price"]
                if position["side"] == "long":
                    tp_hit = hi >= ep * (1 + take_profit_pct)
                    sl_hit = lo <= ep * (1 - stop_loss_pct)
                    if sl_hit or tp_hit:
                        exit_price = ep * (1 - stop_loss_pct) if sl_hit else ep * (1 + take_profit_pct)
                        exit_price *= (1 - SLIPPAGE)
                        pnl = position["size"] * (exit_price - ep)
                        exit_fee = position["size"] * exit_price * FEE_RATE
                        balance += pnl - exit_fee
                        trades.append({"side":"long","entry_price":ep,"exit_price":exit_price,"pnl_pct":round((pnl-exit_fee)/(position["size"]*ep)*100,2),"pnl":round(pnl-exit_fee,2)})
                        position = None
                else:
                    tp_hit = lo <= ep * (1 - take_profit_pct)
                    sl_hit = hi >= ep * (1 + stop_loss_pct)
                    if sl_hit or tp_hit:
                        exit_price = ep * (1 + stop_loss_pct) if sl_hit else ep * (1 - take_profit_pct)
                        exit_price *= (1 + SLIPPAGE)
                        pnl = position["size"] * (ep - exit_price)
                        exit_fee = position["size"] * exit_price * FEE_RATE
                        balance += pnl - exit_fee
                        trades.append({"side":"short","entry_price":ep,"exit_price":exit_price,"pnl_pct":round((pnl-exit_fee)/(position["size"]*ep)*100,2),"pnl":round(pnl-exit_fee,2)})
                        position = None

            if not position:
                features = row[feature_cols].values.reshape(1, -1)
                if np.isnan(features).any():
                    continue
                proba = model.predict_proba(features)[0]
                up_prob = float(proba[1]) if len(proba) > 1 else float(proba[0])
                raw_open = float(next_row["open"])
                if raw_open <= 0:
                    continue
                if up_prob >= BACKTEST_THRESHOLD:
                    entry_price = raw_open * (1 + SLIPPAGE)
                    order_value = balance * POSITION_PCT
                    size = order_value / entry_price
                    balance -= order_value * FEE_RATE
                    position = {"side":"long","entry_price":entry_price,"size":size,"entry_fee":order_value*FEE_RATE}
                elif up_prob <= (1 - BACKTEST_THRESHOLD):
                    entry_price = raw_open * (1 - SLIPPAGE)
                    order_value = balance * POSITION_PCT
                    size = order_value / entry_price
                    balance -= order_value * FEE_RATE
                    position = {"side":"short","entry_price":entry_price,"size":size,"entry_fee":order_value*FEE_RATE}
            equity_curve.append(balance)

        if position:
            last_price = float(test_df.iloc[-1]["close"])
            exit_price = last_price * (1 - SLIPPAGE) if position["side"] == "long" else last_price * (1 + SLIPPAGE)
            pnl = position["size"] * (exit_price-position["entry_price"]) if position["side"] == "long" else position["size"] * (position["entry_price"]-exit_price)
            exit_fee = position["size"] * exit_price * FEE_RATE
            balance += pnl - exit_fee
            trades.append({"side":position["side"],"entry_price":position["entry_price"],"exit_price":exit_price,"pnl_pct":round((pnl-exit_fee)/(position["size"]*position["entry_price"])*100,2),"pnl":round(pnl-exit_fee,2)})

        wins=[t for t in trades if t["pnl"]>0]; losses=[t for t in trades if t["pnl"]<=0]
        win_rate=len(wins)/len(trades)*100 if trades else 0
        gross_win=sum(t["pnl"] for t in wins); gross_loss=abs(sum(t["pnl"] for t in losses))
        profit_factor=gross_win/gross_loss if gross_loss>0 else 0
        peak=initial_balance; max_dd=0
        for eq in equity_curve:
            peak=max(peak,eq); max_dd=max(max_dd,(peak-eq)/peak*100)
        return {"total_trades":len(trades),"win_count":len(wins),"loss_count":len(losses),"win_rate":round(win_rate,1),"avg_win":round(gross_win/len(wins),2) if wins else 0,"avg_loss":round(gross_loss/len(losses),2) if losses else 0,"profit_factor":round(profit_factor,2),"total_return_pct":round((balance-initial_balance)/initial_balance*100,2),"max_drawdown_pct":round(max_dd,2),"final_balance":round(balance,2),"low_trade_warning":len(trades)<5,"oos_start_index":split_idx,"oos_ratio":0.2,"slippage":SLIPPAGE,"fee_rate":FEE_RATE,"trades":trades}
    except Exception as e:
        logger.exception(f"[ML] 回测失败: {e}")
        return {"error": f"回测异常: {e}"}


# ======================================================================
# 训练队列：后台异步执行，严格按“用户选择的模型”逐模型训练
# ======================================================================
class TrainingQueue:
    def __init__(self):
        self._queue = []
        self._current = None
        self._history = []
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._worker = threading.Thread(target=self._worker_loop, name="MLTrainingWorker", daemon=True)
        self._worker.start()

    def add_task(self, symbol: str, selected_models: Optional[List[str]] = None, timeframe: str = "15m") -> bool:
        symbol = _normalize_symbol(symbol)
        timeframe = timeframe or "15m"
        if selected_models is None:
            selected_models = load_model_config().get("selected_models", DEFAULT_SELECTED_MODELS)
        selected_models = list(dict.fromkeys([m for m in selected_models if m in SUPPORTED_MODELS]))
        if not selected_models:
            logger.error(f"[ML训练队列] [{symbol}] 未选择有效模型")
            return False
        task = {"symbol": symbol, "selected_models": selected_models, "timeframe": timeframe,
                "created_at": time.time(), "status": "queued"}
        with self._lock:
            # 同一币种+周期只保留一个队列任务，避免重复拉K线/重复训练。
            keys = {(t["symbol"], t["timeframe"]) for t in self._queue}
            if self._current and (self._current.get("symbol"), self._current.get("timeframe")) == (symbol, timeframe):
                return False
            if (symbol, timeframe) in keys:
                return False
            self._queue.append(task)
        logger.info(f"[ML训练队列] 加入任务 [{symbol}] [{timeframe}] 模型={selected_models}")
        self._wake.set()
        return True

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "current": dict(self._current) if self._current else None,
                "queue": [dict(x) for x in self._queue],
                "history": [dict(x) for x in self._history[-20:]],
                "queue_length": len(self._queue),
            }

    def _worker_loop(self):
        while True:
            self._wake.wait(1.0)
            self._wake.clear()
            while True:
                with self._lock:
                    if not self._queue:
                        self._current = None
                        break
                    task = self._queue.pop(0)
                    self._current = {**task, "status": "training", "progress": 0,
                                     "message": "准备获取已收盘K线", "models_done": 0,
                                     "models_required": len(task["selected_models"])}
                try:
                    self._run_task(task)
                except Exception as e:
                    logger.exception(f"[ML训练队列] [{task['symbol']}] 任务异常: {e}")
                    self._finish(task, "failed", f"训练任务异常: {e}", None)

    def _set_current(self, **updates):
        with self._lock:
            if self._current:
                self._current.update(updates)

    def _finish(self, task, status, message, result):
        record = {**task, "status": status, "message": message, "result": result,
                  "finished_at": time.time()}
        with self._lock:
            self._history.append(record)
            self._history = self._history[-20:]
            self._current = None

    def _run_task(self, task):
        symbol = task["symbol"]
        tf = task["timeframe"]
        selected = task["selected_models"]
        from okx_client import okx_client
        limit = get_train_limit(tf)
        self._set_current(message=f"获取K线中：目标{limit}根已收盘K线", progress=5,
                          kline_requested=limit, kline_actual=0, selected_models=selected)
        ccxt_sym = symbol
        try:
            # 训练使用统一的K线入口，保证数量、分页、未收盘过滤规则完全一致。
            ohlcv = okx_client.get_ohlcv(symbol=ccxt_sym, timeframe=tf, limit=limit)
        except Exception as e:
            self._finish(task, "failed", f"获取K线失败: {e}", None)
            return
        actual = len(ohlcv)
        self._set_current(kline_actual=actual)
        if actual != limit:
            self._finish(task, "failed", f"K线数量不足：请求{limit}根，实际{actual}根；未开始训练", {
                "kline_requested": limit, "kline_actual": actual})
            return

        results = []
        for idx, model_type in enumerate(selected):
            name = MODEL_NAMES.get(model_type, model_type)
            progress = 10 + int(idx * 80 / max(1, len(selected)))
            self._set_current(progress=progress, message=f"训练{name}（{idx+1}/{len(selected)}）")
            model, meta = train_model(ohlcv, symbol, model_type=model_type, timeframe=tf)
            if model is None:
                self._finish(task, "failed", f"{name}训练失败：{meta.get('error', '未知错误')}", {
                    "models_required": len(selected), "models_done": idx,
                    "kline_requested": limit, "kline_actual": actual, "results": results,
                    "failed_model": model_type})
                return
            # 训练完成后立即保存；任何一个选中的模型失败，任务整体标记失败。
            if not save_model(model, meta, symbol, model_type=model_type, timeframe=tf):
                self._finish(task, "failed", f"{name}保存失败", {
                    "models_required": len(selected), "models_done": idx,
                    "kline_requested": limit, "kline_actual": actual, "results": results,
                    "failed_model": model_type})
                return
            # 自动回测使用同一份K线，写回该模型自己的meta。
            bt = backtest_model(ohlcv, model,
                                stop_loss_pct=float(meta.get("target_stop_loss_pct", get_ml_settings()["stop_loss_pct"])),
                                take_profit_pct=float(meta.get("target_take_profit_pct", get_ml_settings()["take_profit_pct"])))
            # 保存带回测结果的meta（覆盖刚保存的同一模型）
            meta["backtest"] = bt
            save_model(model, meta, symbol, model_type=model_type, timeframe=tf)
            results.append({"model_type": model_type, "model_name": name,
                            "test_accuracy": meta.get("test_accuracy", 0),
                            "win_rate": bt.get("win_rate", 0), "profit_factor": bt.get("profit_factor", 0)})
            self._set_current(models_done=idx+1, progress=10 + int((idx+1) * 85 / len(selected)),
                              message=f"{name}训练完成（{idx+1}/{len(selected)}）")

        self._finish(task, "completed", f"训练完成：{len(selected)}/{len(selected)}个模型；K线{actual}/{limit}根",
                     {"models_required": len(selected), "models_done": len(selected),
                      "models_used": len(selected), "model_consistent": True,
                      "kline_requested": limit, "kline_actual": actual, "results": results})


training_queue = TrainingQueue()

def predict_single_model(symbol: str, ohlcv: List[List[float]],
                         model_type: str = "random_forest",
                         timeframe: str = "15m") -> Optional[Dict[str, Any]]:
    """用单个模型预测
    Args:
        symbol: 币种
        ohlcv: K线数据
        model_type: 模型类型 random_forest / xgboost / logistic_regression
        timeframe: K线周期, 如 15m / 1h / 4h
    Returns:
        {up_prob, down_prob, model_type} 或 None(模型不存在)
    """
    model = load_model(symbol, model_type, timeframe)
    if model is None:
        return None

    features = prepare_latest_features(ohlcv)
    if features is None:
        return None

    try:
        # 逻辑回归需要用scaler标准化
        if model_type == "logistic_regression" and hasattr(model, "scaler"):
            features = model.scaler.transform(features)

        proba = model.predict_proba(features)[0]
        up_prob = float(proba[1]) if len(proba) > 1 else float(proba[0])
        down_prob = 1 - up_prob

        return {
            "model_type": model_type,
            "model_name": MODEL_NAMES.get(model_type, model_type),
            "up_prob": round(up_prob, 4),
            "down_prob": round(down_prob, 4),
        }
    except Exception as e:
        logger.warning(f"[ML] [{symbol}] [{model_type}] 预测失败: {e}")
        return None


def predict(symbol: str, ohlcv: List[List[float]], entry_threshold: Optional[float] = None,
            timeframe: str = "15m") -> Dict[str, Any]:
    """用训练好的模型预测最新行情(多模型投票融合)
    自动加载用户选中的所有模型, 分别预测后取概率平均
    Args:
        symbol: 币种
        ohlcv: K线数据
        entry_threshold: 开仓阈值, 不传则用默认的ENTRY_THRESHOLD
        timeframe: K线周期, 如 15m / 1h / 4h
    Returns:
        {up_prob, down_prob, signal, confidence, models_details...}
    """
    # 加载用户选中的模型
    config = load_model_config()
    selected_models = config.get("selected_models", DEFAULT_SELECTED_MODELS)

    # 逐个模型预测：严格要求“后台选择几个，实盘就必须加载几个”。
    # 不允许静默跳过缺失/损坏模型，也不允许自动回退到 random_forest，
    # 否则会出现后台选3个、实盘实际只用1~2个模型的情况。
    selected_models = list(dict.fromkeys(selected_models))
    if not selected_models:
        return {"error": "未选择任何模型，请先在后台选择模型"}

    predictions = []
    missing_models = []
    for mt in selected_models:
        compatible, reason = is_model_compatible(symbol, mt, timeframe)
        if not compatible:
            logger.warning(f"[ML] [{symbol}] [{mt}] 模型不兼容，拒绝参与预测: {reason}")
            missing_models.append(mt)
            continue
        pred = predict_single_model(symbol, ohlcv, mt, timeframe)
        if pred is None:
            missing_models.append(mt)
            continue

        # 从meta读取回测胜率, 用于加权
        meta = get_model_meta(symbol, mt, timeframe)
        backtest = meta.get("backtest", {}) if meta else {}
        win_rate = backtest.get("win_rate", 50) / 100.0 if backtest else 0.5
        pred["weight"] = max(win_rate, 0.2)
        predictions.append(pred)

    # 严格一致性：只要有一个后台选中的模型无法参与预测，就不产生交易信号。
    # 这样“选择数量”和“实际参与数量”永远一致。
    if missing_models:
        missing_names = [MODEL_NAMES.get(m, m) for m in missing_models]
        selected_names = [MODEL_NAMES.get(m, m) for m in selected_models]
        return {
            "error": f"模型不完整，无法交易: {symbol}",
            "selected_models": selected_models,
            "selected_model_names": selected_names,
            "missing_models": missing_models,
            "missing_model_names": missing_names,
            "models_used": len(predictions),
            "models_required": len(selected_models),
            "model_consistent": False,
        }

    if len(predictions) != len(selected_models):
        return {
            "error": f"模型数量不一致，无法交易: {symbol}",
            "models_used": len(predictions),
            "models_required": len(selected_models),
            "model_consistent": False,
        }

    # 多模型加权投票: 按回测胜率加权(保底20%)
    total_weight = sum(p["weight"] for p in predictions)
    avg_up_prob = sum(p["up_prob"] * p["weight"] for p in predictions) / total_weight
    avg_down_prob = 1 - avg_up_prob

    # 用传入的阈值, 没有则用默认
    threshold = entry_threshold if entry_threshold is not None else ENTRY_THRESHOLD

    # 信号判断
    if avg_up_prob >= threshold:
        signal = "buy"
        confidence = int(avg_up_prob * 100)
    elif avg_down_prob >= threshold:
        signal = "sell"
        confidence = int(avg_down_prob * 100)
    else:
        signal = "hold"
        confidence = int(max(avg_up_prob, avg_down_prob) * 100)

    return {
        "symbol": symbol,
        "up_prob": round(avg_up_prob, 4),
        "down_prob": round(avg_down_prob, 4),
        "signal": signal,
        "confidence": confidence,
        "entry_threshold": threshold,
        "models_used": len(predictions),
        "models_required": len(selected_models),
        "selected_models": selected_models,
        "model_consistent": len(predictions) == len(selected_models),
        "models_details": predictions,
    }
