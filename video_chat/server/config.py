"""所有可配置项都从环境变量读，部署时写在 .env 或服务器环境里。"""
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent
APP_DIR = BASE.parent / "app"                      # 前端页面
UPLOAD_DIR = Path(os.environ.get("SEEU_UPLOAD_DIR", BASE / "uploads"))
DATABASE_URL = os.environ.get("SEEU_DATABASE_URL", f"sqlite:///{BASE / 'seeu.db'}")
SECRET = os.environ.get("SEEU_SECRET", "dev-secret-change-me")  # 上线必须改成随机长字符串
DEV_MODE = os.environ.get("SEEU_DEV", "1") == "1"   # 开发模式：短信验证码直接返回、可模拟支付
ADMIN_TOKEN = os.environ.get("SEEU_ADMIN_TOKEN", "")  # 管理接口（审核认证等）用

# 通话
STUN_URLS = os.environ.get("SEEU_STUN", "stun:stun.l.google.com:19302").split(",")
TURN_URL = os.environ.get("SEEU_TURN_URL", "")      # 例：turn:turn.example.com:3478
TURN_USER = os.environ.get("SEEU_TURN_USER", "")
TURN_PASS = os.environ.get("SEEU_TURN_PASS", "")
RING_TIMEOUT = int(os.environ.get("SEEU_RING_TIMEOUT", "45"))   # 秒，无人接听自动取消
BILL_INTERVAL = int(os.environ.get("SEEU_BILL_INTERVAL", "60"))  # 秒，每分钟扣一次
HOST_SHARE = float(os.environ.get("SEEU_HOST_SHARE", "0.5"))    # 主播分成比例

# 业务参数
COINS_PER_YUAN = 10
MAX_UPLOAD_MB = int(os.environ.get("SEEU_MAX_UPLOAD_MB", "30"))
