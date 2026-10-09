"""第三方服务对接：实名核验、阿里云短信、支付宝手机网站支付、微信支付 APIv3 H5 支付。

每个服务都是「配置齐全才启用」：没配置时开发模式走本地模拟，正式模式抛出明确的错误，不会假装成功。
签名、验签算法按官方文档实现，并在 tests/test_providers.py 里用自己生成的密钥做了往返校验。
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
import urllib.parse
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException

from . import config


class ProviderError(HTTPException):
    def __init__(self, msg, code="provider"):
        super().__init__(503, {"code": code, "msg": msg})


def _load_key(path: str, private: bool):
    data = Path(path).read_bytes()
    if b"-----BEGIN" not in data:   # 支付宝后台给的是去掉头尾的一行 base64
        tag = "PRIVATE KEY" if private else "PUBLIC KEY"
        data = f"-----BEGIN {tag}-----\n{data.decode().strip()}\n-----END {tag}-----\n".encode()
    return serialization.load_pem_private_key(data, None) if private else serialization.load_pem_public_key(data)


# ======================= 实名核验 =======================
_ID_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
_ID_CHECK = "10X98765432"


def check_id_number(id_no: str) -> date:
    """校验 18 位身份证号（格式、出生日期、校验位），返回出生日期。不对就抛 ValueError。"""
    id_no = id_no.strip().upper()
    if len(id_no) != 18 or not id_no[:17].isdigit() or id_no[17] not in "0123456789X":
        raise ValueError("身份证号格式不对")
    if _ID_CHECK[sum(int(c) * w for c, w in zip(id_no[:17], _ID_WEIGHTS)) % 11] != id_no[17]:
        raise ValueError("身份证号校验位不对，请检查是否输错")
    try:
        birth = date(int(id_no[6:10]), int(id_no[10:12]), int(id_no[12:14]))
    except ValueError:
        raise ValueError("身份证号里的出生日期不对")
    if birth > date.today() or birth.year < 1900:
        raise ValueError("身份证号里的出生日期不对")
    return birth


def age_on(birth: date, today: date | None = None) -> int:
    t = today or date.today()
    return t.year - birth.year - ((t.month, t.day) < (birth.month, birth.day))


def verify_identity(name: str, id_no: str) -> bool:
    """调用实名核验服务（姓名 + 身份证号是否一致）。

    接入方式：在阿里云市场 / 腾讯云购买「身份证二要素核验」，把接口地址和 AppCode 写进
    SEEU_REALNAME_URL / SEEU_REALNAME_APPCODE。不同服务商返回格式略有差异，按需调整下面的判断。
    """
    if not config.REALNAME_URL or not config.REALNAME_APPCODE:
        if config.DEV_MODE:
            return True   # 开发模式：只做本地格式校验
        raise ProviderError("实名核验服务未配置", "realname_not_configured")
    r = httpx.post(config.REALNAME_URL, data={"name": name, "idcard": id_no},
                   headers={"Authorization": f"APPCODE {config.REALNAME_APPCODE}"}, timeout=10)
    if r.status_code != 200:
        raise ProviderError("实名核验服务暂时不可用，请稍后再试")
    body = r.json()
    result = body.get("result") or body.get("data") or body
    # 常见返回：{"result": {"res": "1"}} 或 {"data": {"result": 1}}，1 表示一致
    return str(result.get("res", result.get("result", ""))) in ("1", "true", "True")


# ======================= 阿里云短信 =======================
def _pct(s: str) -> str:
    return urllib.parse.quote(str(s), safe="~")


def aliyun_rpc_sign(params: dict, secret: str, method: str = "GET") -> str:
    """阿里云 RPC 风格 API 签名（SignatureVersion 1.0, HMAC-SHA1）。"""
    canon = "&".join(f"{_pct(k)}={_pct(v)}" for k, v in sorted(params.items()))
    to_sign = f"{method}&{_pct('/')}&{_pct(canon)}"
    return base64.b64encode(hmac.new(f"{secret}&".encode(), to_sign.encode(), hashlib.sha1).digest()).decode()


def send_sms_code(phone: str, code: str):
    if not (config.ALIYUN_AK and config.ALIYUN_SK and config.SMS_SIGN and config.SMS_TEMPLATE):
        raise ProviderError("短信服务未配置", "sms_not_configured")
    params = {
        "AccessKeyId": config.ALIYUN_AK, "Action": "SendSms", "Format": "JSON", "PhoneNumbers": phone,
        "RegionId": "cn-hangzhou", "SignName": config.SMS_SIGN, "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": uuid.uuid4().hex, "SignatureVersion": "1.0", "TemplateCode": config.SMS_TEMPLATE,
        "TemplateParam": json.dumps({"code": code}), "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Version": "2017-05-25",
    }
    params["Signature"] = aliyun_rpc_sign(params, config.ALIYUN_SK)
    r = httpx.get("https://dysmsapi.aliyuncs.com/", params=params, timeout=10)
    data = r.json()
    if data.get("Code") != "OK":
        raise ProviderError(f"短信发送失败：{data.get('Message', '未知错误')}")


# ======================= 支付宝（手机网站支付 alipay.trade.wap.pay） =======================
def alipay_ready() -> bool:
    return bool(config.ALIPAY_APP_ID and config.ALIPAY_PRIVATE_KEY and config.ALIPAY_PUBLIC_KEY)


def alipay_sign_content(params: dict) -> str:
    return "&".join(f"{k}={v}" for k, v in sorted(params.items()) if k not in ("sign", "sign_type") and v not in ("", None))


def alipay_sign(params: dict, private_key) -> str:
    sig = private_key.sign(alipay_sign_content(params).encode(), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode()


def alipay_verify(params: dict, public_key) -> bool:
    try:
        public_key.verify(base64.b64decode(params.get("sign", "")), alipay_sign_content(params).encode(),
                          padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        return False


def alipay_pay_url(order_id: str, yuan: float, subject: str, private_key=None, app_id=None) -> str:
    """生成跳转到支付宝收银台的地址。手机浏览器 / App 内网页打开后会自动拉起支付宝 App。"""
    params = {
        "app_id": app_id or config.ALIPAY_APP_ID, "method": "alipay.trade.wap.pay", "format": "JSON",
        "charset": "utf-8", "sign_type": "RSA2", "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": "1.0", "notify_url": f"{config.PUBLIC_URL}/api/pay/notify/alipay",
        "return_url": f"{config.PUBLIC_URL}/#/pay-result/{order_id}",
        "biz_content": json.dumps({"out_trade_no": order_id, "total_amount": f"{yuan:.2f}", "subject": subject,
                                   "product_code": "QUICK_WAP_WAY"}, ensure_ascii=False, separators=(",", ":")),
    }
    params["sign"] = alipay_sign(params, private_key or _load_key(config.ALIPAY_PRIVATE_KEY, True))
    return f"{config.ALIPAY_GATEWAY}?{urllib.parse.urlencode(params)}"


def alipay_parse_notify(form: dict, public_key=None) -> dict | None:
    """校验支付宝异步通知。成功返回 {order_id, yuan}，否则 None。"""
    if not alipay_verify(form, public_key or _load_key(config.ALIPAY_PUBLIC_KEY, False)):
        return None
    if form.get("app_id") != config.ALIPAY_APP_ID and public_key is None:
        return None
    if form.get("trade_status") not in ("TRADE_SUCCESS", "TRADE_FINISHED"):
        return None
    return {"order_id": form.get("out_trade_no"), "yuan": float(form.get("total_amount", 0))}


# ======================= 微信支付 APIv3（H5 支付） =======================
def wxpay_ready() -> bool:
    return bool(config.WXPAY_APPID and config.WXPAY_MCHID and config.WXPAY_SERIAL and config.WXPAY_PRIVATE_KEY
                and config.WXPAY_PUBLIC_KEY and len(config.WXPAY_APIV3_KEY) == 32)


def wxpay_auth_header(method: str, path: str, body: str, private_key, mchid: str, serial: str,
                      nonce: str | None = None, ts: int | None = None) -> str:
    nonce = nonce or secrets.token_hex(16)
    ts = ts or int(time.time())
    msg = f"{method}\n{path}\n{ts}\n{nonce}\n{body}\n"
    sig = base64.b64encode(private_key.sign(msg.encode(), padding.PKCS1v15(), hashes.SHA256())).decode()
    return (f'WECHATPAY2-SHA256-RSA2048 mchid="{mchid}",nonce_str="{nonce}",signature="{sig}",'
            f'timestamp="{ts}",serial_no="{serial}"')


def wxpay_h5_url(order_id: str, yuan: float, subject: str, client_ip: str) -> str:
    body = json.dumps({
        "appid": config.WXPAY_APPID, "mchid": config.WXPAY_MCHID, "description": subject, "out_trade_no": order_id,
        "notify_url": f"{config.PUBLIC_URL}/api/pay/notify/wechat",
        "amount": {"total": int(round(yuan * 100)), "currency": "CNY"},
        "scene_info": {"payer_client_ip": client_ip, "h5_info": {"type": "Wap"}},
    }, ensure_ascii=False, separators=(",", ":"))
    path = "/v3/pay/transactions/h5"
    auth = wxpay_auth_header("POST", path, body, _load_key(config.WXPAY_PRIVATE_KEY, True), config.WXPAY_MCHID, config.WXPAY_SERIAL)
    r = httpx.post("https://api.mch.weixin.qq.com" + path, content=body.encode(), timeout=10,
                   headers={"Authorization": auth, "Content-Type": "application/json", "Accept": "application/json"})
    if r.status_code != 200:
        raise ProviderError(f"微信支付下单失败：{r.json().get('message', r.status_code)}")
    redirect = urllib.parse.quote(f"{config.PUBLIC_URL}/#/pay-result/{order_id}", safe="")
    return f"{r.json()['h5_url']}&redirect_url={redirect}"


def wxpay_parse_notify(headers: dict, body: bytes, public_key=None, apiv3_key: str | None = None) -> dict | None:
    """验签 + 解密微信支付回调。成功返回 {order_id, yuan}，否则 None。"""
    h = {k.lower(): v for k, v in headers.items()}
    msg = f"{h.get('wechatpay-timestamp', '')}\n{h.get('wechatpay-nonce', '')}\n{body.decode()}\n"
    try:
        (public_key or _load_key(config.WXPAY_PUBLIC_KEY, False)).verify(
            base64.b64decode(h.get("wechatpay-signature", "")), msg.encode(), padding.PKCS1v15(), hashes.SHA256())
    except Exception:
        return None
    if abs(time.time() - int(h.get("wechatpay-timestamp", "0") or 0)) > 300:
        return None   # 防重放
    res = json.loads(body)["resource"]
    plain = AESGCM((apiv3_key or config.WXPAY_APIV3_KEY).encode()).decrypt(
        res["nonce"].encode(), base64.b64decode(res["ciphertext"]), (res.get("associated_data") or "").encode())
    data = json.loads(plain)
    if data.get("trade_state") != "SUCCESS":
        return None
    return {"order_id": data["out_trade_no"], "yuan": data["amount"]["total"] / 100}
