"""离线推送：对方不在线时，来电和新消息通过系统通知送达。

- webpush：浏览器 / 「添加到主屏幕」的网页 App（安卓 Chrome、iPhone iOS 16.4+）。按 RFC 8291 + VAPID 自己实现加密和签名。
- fcm：安卓 App（Firebase Cloud Messaging HTTP v1）。注意：国内大部分安卓手机没有谷歌服务，收不到 FCM，
  国内上线需要再接厂商推送（华为 / 小米 / OPPO / vivo）或极光、个推这类聚合推送。
- apns：苹果 App（Apple Push Notification service，HTTP/2 + JWT）。

没有配置的通道自动跳过，不影响其它功能。
"""
import base64
import hashlib
import json
import logging
import os
import time
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import config
from .core import current_user, get_db
from .db import Device, SessionLocal, User

log = logging.getLogger("seeu.push")
router = APIRouter(prefix="/api")


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _es256_jwt(header: dict, claims: dict, key) -> str:
    signing_input = f"{b64u(json.dumps(header, separators=(',', ':')).encode())}.{b64u(json.dumps(claims, separators=(',', ':')).encode())}"
    r, s = decode_dss_signature(key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
    return f"{signing_input}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


# ======================= Web Push =======================
_vapid_key = None


def vapid_key():
    """VAPID 密钥：优先读 SEEU_VAPID_KEY_FILE，否则在数据目录生成一份并保存（换了密钥，旧订阅会失效）。"""
    global _vapid_key
    if _vapid_key:
        return _vapid_key
    path = os.environ.get("SEEU_VAPID_KEY_FILE") or str(config.DATA_DIR / "vapid_private.pem")
    if os.path.exists(path):
        _vapid_key = serialization.load_pem_private_key(open(path, "rb").read(), None)
    else:
        _vapid_key = ec.generate_private_key(ec.SECP256R1())
        with open(path, "wb") as fh:
            fh.write(_vapid_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()))
    return _vapid_key


def vapid_public_key() -> str:
    return b64u(vapid_key().public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def webpush_encrypt(payload: bytes, p256dh: str, auth: str, salt: bytes | None = None, as_key=None) -> bytes:
    """RFC 8291 消息加密（aes128gcm），返回请求体。"""
    ua_pub_bytes = b64u_dec(p256dh)
    ua_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub_bytes)
    as_key = as_key or ec.generate_private_key(ec.SECP256R1())
    as_pub_bytes = as_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    secret = as_key.exchange(ec.ECDH(), ua_pub)
    ikm = _hkdf(b64u_dec(auth), secret, b"WebPush: info\x00" + ua_pub_bytes + as_pub_bytes, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + (4096).to_bytes(4, "big") + bytes([len(as_pub_bytes)]) + as_pub_bytes + ciphertext


def vapid_header(endpoint: str) -> str:
    u = urlparse(endpoint)
    token = _es256_jwt({"typ": "JWT", "alg": "ES256"},
                       {"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": config.VAPID_SUB},
                       vapid_key())
    return f"vapid t={token}, k={vapid_public_key()}"


async def send_webpush(sub: dict, data: dict, client: httpx.AsyncClient) -> bool:
    """返回 False 表示订阅已失效，应删除。"""
    body = webpush_encrypt(json.dumps(data, ensure_ascii=False).encode(), sub["keys"]["p256dh"], sub["keys"]["auth"])
    r = await client.post(sub["endpoint"], content=body, headers={
        "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": "60",
        "Urgency": "high", "Authorization": vapid_header(sub["endpoint"])})
    if r.status_code in (404, 410):
        return False
    if r.status_code >= 400:
        log.warning("webpush %s: %s", r.status_code, r.text[:200])
    return True


# ======================= FCM（安卓） =======================
_fcm_token = {"value": "", "exp": 0}


async def _fcm_access_token(client) -> tuple[str, str]:
    sa = json.load(open(config.FCM_SERVICE_ACCOUNT))
    if _fcm_token["exp"] > time.time() + 60:
        return _fcm_token["value"], sa["project_id"]
    key = serialization.load_pem_private_key(sa["private_key"].encode(), None)
    now = int(time.time())
    signing_input = f"{b64u(json.dumps({'alg': 'RS256', 'typ': 'JWT'}).encode())}.{b64u(json.dumps({'iss': sa['client_email'], 'scope': 'https://www.googleapis.com/auth/firebase.messaging', 'aud': 'https://oauth2.googleapis.com/token', 'iat': now, 'exp': now + 3600}).encode())}"
    jwt = f"{signing_input}.{b64u(key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256()))}"
    r = await client.post("https://oauth2.googleapis.com/token",
                          data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": jwt})
    r.raise_for_status()
    _fcm_token.update(value=r.json()["access_token"], exp=now + 3500)
    return _fcm_token["value"], sa["project_id"]


async def send_fcm(token: str, data: dict, client) -> bool:
    access, project = await _fcm_access_token(client)
    msg = {"message": {"token": token,
                       "notification": {"title": data["title"], "body": data["body"]},
                       "data": {k: str(v) for k, v in data.items()},
                       "android": {"priority": "high", "ttl": "60s",
                                   "notification": {"channel_id": "calls" if data.get("type") == "call" else "messages", "sound": "default"}}}}
    r = await client.post(f"https://fcm.googleapis.com/v1/projects/{project}/messages:send", json=msg,
                          headers={"Authorization": f"Bearer {access}"})
    if r.status_code == 404 or "UNREGISTERED" in r.text:
        return False
    if r.status_code >= 400:
        log.warning("fcm %s: %s", r.status_code, r.text[:200])
    return True


# ======================= APNs（苹果） =======================
_apns_token = {"value": "", "iat": 0}


def _apns_jwt() -> str:
    if _apns_token["iat"] > time.time() - 50 * 60:
        return _apns_token["value"]
    key = serialization.load_pem_private_key(open(config.APNS_KEY_FILE, "rb").read(), None)
    now = int(time.time())
    _apns_token.update(value=_es256_jwt({"alg": "ES256", "kid": config.APNS_KEY_ID}, {"iss": config.APNS_TEAM_ID, "iat": now}, key), iat=now)
    return _apns_token["value"]


async def send_apns(token: str, data: dict, client) -> bool:
    host = "api.sandbox.push.apple.com" if config.APNS_SANDBOX else "api.push.apple.com"
    payload = {"aps": {"alert": {"title": data["title"], "body": data["body"]}, "sound": "default",
                       "interruption-level": "time-sensitive" if data.get("type") == "call" else "active"},
               **{k: v for k, v in data.items() if k not in ("title", "body")}}
    r = await client.post(f"https://{host}/3/device/{token}", json=payload, headers={
        "authorization": f"bearer {_apns_jwt()}", "apns-topic": config.APNS_TOPIC, "apns-push-type": "alert",
        "apns-priority": "10", "apns-expiration": str(int(time.time()) + 60)})
    if r.status_code == 410 or "BadDeviceToken" in r.text or "Unregistered" in r.text:
        return False
    if r.status_code >= 400:
        log.warning("apns %s: %s", r.status_code, r.text[:200])
    return True


def enabled_providers() -> set:
    p = {"webpush"}
    if config.FCM_SERVICE_ACCOUNT and os.path.exists(config.FCM_SERVICE_ACCOUNT):
        p.add("fcm")
    if config.APNS_KEY_FILE and config.APNS_KEY_ID and config.APNS_TEAM_ID and os.path.exists(config.APNS_KEY_FILE):
        p.add("apns")
    return p


def push_later(*args, **kw):
    """在后台发推送，出错只记日志，不影响主流程。"""
    import asyncio

    async def run():
        try:
            await push_user(*args, **kw)
        except Exception:
            log.exception("push failed")
    return asyncio.create_task(run())


async def push_user(uid: int, title: str, body: str, url: str = "/", kind: str = "message", tag: str = "") -> int:
    """给某个用户的所有设备发通知，返回成功发送的设备数。用户关闭了「新消息通知」时只推来电。"""
    from .core import settings_of
    with SessionLocal() as db:
        u = db.get(User, uid)
        if not u or (kind != "call" and not settings_of(u)["notify"]):
            return 0
        devices = db.scalars(select(Device).where(Device.user_id == uid)).all()
    if not devices:
        return 0
    data = {"title": title, "body": body, "url": url, "type": kind, "tag": tag or kind}
    ok, dead = 0, []
    providers = enabled_providers()
    try:
        import h2  # noqa: F401  APNs 必须 HTTP/2；没装 h2 时其它通道照常用 HTTP/1.1
        http2 = True
    except ImportError:
        http2 = False
        if "apns" in providers:
            log.warning("没有安装 h2（pip install 'httpx[http2]'），苹果推送不可用")
            providers = providers - {"apns"}
    async with httpx.AsyncClient(timeout=10, http2=http2) as client:
        for d in devices:
            if d.provider not in providers:
                continue
            try:
                if d.provider == "webpush":
                    alive = await send_webpush(json.loads(d.token), data, client)
                elif d.provider == "fcm":
                    alive = await send_fcm(d.token, data, client)
                else:
                    alive = await send_apns(d.token, data, client)
                ok += alive
                if not alive:
                    dead.append(d.id)
            except Exception as e:  # 推送失败不能影响主流程
                log.warning("push to device %s failed: %s", d.id, e)
    if dead:
        with SessionLocal() as db:
            db.execute(delete(Device).where(Device.id.in_(dead)))
            db.commit()
    return ok


def has_devices(uid: int) -> bool:
    providers = enabled_providers()
    with SessionLocal() as db:
        return db.scalar(select(Device.id).where(Device.user_id == uid, Device.provider.in_(providers))) is not None


# ======================= 设备注册 =======================
class DeviceIn(BaseModel):
    provider: str = Field(pattern="^(webpush|fcm|apns)$")
    token: str = Field(min_length=10, max_length=4000)   # webpush 传订阅 JSON 字符串


@router.post("/devices")
def register_device(body: DeviceIn, me: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.provider == "webpush":
        try:
            sub = json.loads(body.token)
            assert sub["endpoint"].startswith("https://") and sub["keys"]["p256dh"] and sub["keys"]["auth"]
        except Exception:
            raise HTTPException(400, "推送订阅格式不对")
    h = hashlib.sha256(body.token.encode()).hexdigest()
    d = db.scalar(select(Device).where(Device.token_hash == h))
    if d:
        d.user_id = me.id   # 同一台设备换了账号登录
    else:
        db.add(Device(user_id=me.id, provider=body.provider, token=body.token, token_hash=h))
    db.commit()
    return {"ok": True, "enabled": body.provider in enabled_providers()}


class DeviceDel(BaseModel):
    token: str


@router.post("/devices/remove")
def remove_device(body: DeviceDel, me: User = Depends(current_user), db: Session = Depends(get_db)):
    db.execute(delete(Device).where(Device.user_id == me.id, Device.token_hash == hashlib.sha256(body.token.encode()).hexdigest()))
    db.commit()
    return {"ok": True}
