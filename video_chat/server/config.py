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

# 对外访问地址（支付回跳、推送里的链接用），例：https://seeu.example.com
PUBLIC_URL = os.environ.get("SEEU_PUBLIC_URL", "http://localhost:8000").rstrip("/")
DATA_DIR = Path(os.environ.get("SEEU_DATA_DIR", BASE))

# ---- 阿里云短信 ----
ALIYUN_AK = os.environ.get("SEEU_ALIYUN_AK", "")
ALIYUN_SK = os.environ.get("SEEU_ALIYUN_SK", "")
SMS_SIGN = os.environ.get("SEEU_SMS_SIGN", "")            # 短信签名，如「SeeU」
SMS_TEMPLATE = os.environ.get("SEEU_SMS_TEMPLATE", "")    # 模板 CODE，模板内容需含 ${code}

# ---- 实名核验（阿里云「身份证二要素核验」等，接入后在 providers.verify_identity 里调用） ----
REALNAME_APPCODE = os.environ.get("SEEU_REALNAME_APPCODE", "")
REALNAME_URL = os.environ.get("SEEU_REALNAME_URL", "")    # 服务商接口地址

# ---- 支付宝（手机网站支付） ----
ALIPAY_APP_ID = os.environ.get("SEEU_ALIPAY_APP_ID", "")
ALIPAY_PRIVATE_KEY = os.environ.get("SEEU_ALIPAY_PRIVATE_KEY_FILE", "")   # 应用私钥 PEM 文件路径
ALIPAY_PUBLIC_KEY = os.environ.get("SEEU_ALIPAY_PUBLIC_KEY_FILE", "")     # 支付宝公钥 PEM 文件路径
ALIPAY_GATEWAY = os.environ.get("SEEU_ALIPAY_GATEWAY", "https://openapi.alipay.com/gateway.do")

# ---- 微信支付 APIv3（H5 支付） ----
WXPAY_APPID = os.environ.get("SEEU_WXPAY_APPID", "")
WXPAY_MCHID = os.environ.get("SEEU_WXPAY_MCHID", "")
WXPAY_SERIAL = os.environ.get("SEEU_WXPAY_SERIAL", "")              # 商户证书序列号
WXPAY_PRIVATE_KEY = os.environ.get("SEEU_WXPAY_PRIVATE_KEY_FILE", "")  # 商户私钥 apiclient_key.pem
WXPAY_PUBLIC_KEY = os.environ.get("SEEU_WXPAY_PUBLIC_KEY_FILE", "")    # 微信支付公钥 / 平台证书公钥
WXPAY_APIV3_KEY = os.environ.get("SEEU_WXPAY_APIV3_KEY", "")          # 32 位 APIv3 密钥

# ---- 推送 ----
VAPID_SUB = os.environ.get("SEEU_VAPID_SUB", "mailto:admin@example.com")
FCM_SERVICE_ACCOUNT = os.environ.get("SEEU_FCM_SERVICE_ACCOUNT", "")   # Firebase 服务账号 JSON 文件路径
APNS_KEY_FILE = os.environ.get("SEEU_APNS_KEY_FILE", "")               # AuthKey_XXXX.p8
APNS_KEY_ID = os.environ.get("SEEU_APNS_KEY_ID", "")
APNS_TEAM_ID = os.environ.get("SEEU_APNS_TEAM_ID", "")
APNS_TOPIC = os.environ.get("SEEU_APNS_TOPIC", "com.seeu.videochat")
APNS_SANDBOX = os.environ.get("SEEU_APNS_SANDBOX", "0") == "1"

# ---- 陪玩 ----
GAME_SHARE = float(os.environ.get("SEEU_GAME_SHARE", "0.8"))        # 陪玩师分成
GAME_AUTO_CONFIRM_HOURS = int(os.environ.get("SEEU_GAME_AUTO_CONFIRM_HOURS", "24"))
GAME_ACCEPT_TIMEOUT_MIN = int(os.environ.get("SEEU_GAME_ACCEPT_TIMEOUT_MIN", "30"))
