"""
配置管理模块
从 .env 文件加载所有配置, 提供类型安全的访问
"""
import os
from dotenv import load_dotenv
from dataclasses import dataclass, field
from typing import Optional, List
from loguru import logger
# 加载 .env 文件 (同目录下)
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
def _get_bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key, str(default)).lower()
    return val in ("1", "true", "yes", "on")
def _get_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, default))
    except (ValueError, TypeError):
        return default
def _get_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except (ValueError, TypeError):
        return default
def _get_list(key: str, default: str = "") -> List[str]:
    """逗号分隔的列表, 自动去空格去空"""
    raw = os.getenv(key, default)
    return [s.strip() for s in raw.split(",") if s.strip()]
@dataclass
class OKXConfig:
    """欧易 API 配置"""
    api_key: str = field(default_factory=lambda: os.getenv("OKX_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("OKX_API_SECRET", ""))
    api_passphrase: str = field(default_factory=lambda: os.getenv("OKX_API_PASSPHRASE", ""))
    demo_mode: bool = field(default_factory=lambda: _get_bool("OKX_DEMO_MODE", False))
    base_url: str = field(default_factory=lambda: os.getenv("OKX_BASE_URL", "https://www.okx.com"))
    proxy_url: str = field(default_factory=lambda: os.getenv("PROXY_URL", ""))
    @property
    def is_configured(self) -> bool:
        """检查 API 凭证是否已填写"""
        return bool(self.api_key and self.api_secret and self.api_passphrase)
    @property
    def is_demo(self) -> bool:
        return self.demo_mode
def symbol_to_ccxt(sym: str, trading_type: str) -> str:
    """将任意交易对符号转换为 ccxt 格式
    永续合约: BTC-USDT-SWAP -> BTC/USDT:USDT
    现货: BTC/USDT 或 BTC-USDT -> BTC/USDT
    """
    if trading_type == "swap":
        if "-SWAP" in sym:
            base = sym.replace("-USDT-SWAP", "").replace("-USDC-SWAP", "")
            quote = "USDC" if "USDC" in sym else "USDT"
            return f"{base}/{quote}:{quote}"
        elif "/" in sym and ":" not in sym:
            base, quote = sym.split("/")
            return f"{base}/{quote}:{quote}"
        elif "-" in sym and "/" not in sym:
            # BTC-USDT 格式
            parts = sym.split("-")
            base, quote = parts[0], parts[1]
            return f"{base}/{quote}:{quote}"
        return sym
    else:
        if "-SWAP" in sym:
            base = sym.replace("-USDT-SWAP", "").replace("-USDC-SWAP", "")
            quote = "USDC" if "USDC" in sym else "USDT"
            return f"{base}/{quote}"
        elif "-" in sym and "/" not in sym:
            parts = sym.split("-")
            return f"{parts[0]}/{parts[1]}"
        return sym
@dataclass
class TradingConfig:
    """交易参数配置"""
    symbol: str = field(default_factory=lambda: os.getenv("TRADING_SYMBOL", "BTC-USDT-SWAP"))
    symbols: List[str] = field(default_factory=lambda: _get_list("TRADING_SYMBOLS", "BTC-USDT-SWAP"))
    trading_type: str = field(default_factory=lambda: os.getenv("TRADING_TYPE", "swap"))
    margin_mode: str = field(default_factory=lambda: os.getenv("MARGIN_MODE", "cross"))
    leverage: int = field(default_factory=lambda: _get_int("LEVERAGE", 10))
    order_amount_usdt: float = field(default_factory=lambda: _get_float("ORDER_AMOUNT_USDT", 100))
    timeframe: str = field(default_factory=lambda: os.getenv("TIMEFRAME", "15m").lower())
    strategy_name: str = field(default_factory=lambda: os.getenv("STRATEGY_NAME", "ma_cross"))
    trade_direction: str = field(default_factory=lambda: os.getenv("TRADE_DIRECTION", "auto"))  # auto/long/short
    @property
    def ccxt_symbol(self) -> str:
        """转换为 ccxt 格式的交易对符号(单币种兼容)"""
        return symbol_to_ccxt(self.symbol, self.trading_type)
    def get_ccxt_symbol(self, sym: str) -> str:
        """获取指定币种的 ccxt 格式符号"""
        return symbol_to_ccxt(sym, self.trading_type)
    def get_active_symbols(self) -> List[str]:
        """获取激活的币种列表: 优先用 symbols, 为空则用 symbol"""
        if self.symbols:
            return self.symbols
        return [self.symbol]
@dataclass
class RiskConfig:
    """风控配置"""
    max_loss_per_trade: float = field(default_factory=lambda: _get_float("MAX_LOSS_PER_TRADE", 20))
    daily_max_loss: float = field(default_factory=lambda: _get_float("DAILY_MAX_LOSS", 100))
    max_positions: int = field(default_factory=lambda: _get_int("MAX_POSITIONS", 3))
    max_open_positions: int = field(default_factory=lambda: _get_int("MAX_OPEN_POSITIONS", 3))
    signal_confidence_threshold: float = field(default_factory=lambda: _get_float("SIGNAL_CONFIDENCE_THRESHOLD", 60))
    high_tf_confirm: bool = field(default_factory=lambda: _get_bool("HIGH_TF_CONFIRM", True))
    stop_loss_pct: float = field(default_factory=lambda: _get_float("STOP_LOSS_PCT", 0.02))
    take_profit_pct: float = field(default_factory=lambda: _get_float("TAKE_PROFIT_PCT", 0.04))
    dry_run: bool = field(default_factory=lambda: _get_bool("DRY_RUN", False))
@dataclass
class APIConfig:
    """API 服务配置"""
    host: str = field(default_factory=lambda: os.getenv("API_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _get_int("API_PORT", 8000))
    secret_key: str = field(default_factory=lambda: os.getenv("API_SECRET_KEY", "change_me"))
@dataclass
class AppConfig:
    """应用总配置"""
    okx: OKXConfig = field(default_factory=OKXConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    api: APIConfig = field(default_factory=APIConfig)
# 全局单例
config = AppConfig()
