"""公共工具：登录令牌、当前用户、金币记账、对外输出格式、商品目录。"""
import base64
import hashlib
import hmac
import json
import time
from datetime import date

from fastapi import Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .db import Ledger, Notice, SessionLocal, User

# ---------------- 商品目录 ----------------
GIFTS = [
    {"id": 1, "name": "玫瑰", "icon": "🌹", "price": 1}, {"id": 2, "name": "爱心", "icon": "💖", "price": 10},
    {"id": 3, "name": "棒棒糖", "icon": "🍭", "price": 20}, {"id": 4, "name": "小熊", "icon": "🧸", "price": 52},
    {"id": 5, "name": "花束", "icon": "💐", "price": 99}, {"id": 6, "name": "钻戒", "icon": "💍", "price": 199},
    {"id": 7, "name": "跑车", "icon": "🏎️", "price": 520}, {"id": 8, "name": "城堡", "icon": "🏰", "price": 1314},
]
GIFT_BY_ID = {g["id"]: g for g in GIFTS}
PACKS = [
    {"coins": 60, "yuan": 6, "bonus": 0}, {"coins": 300, "yuan": 30, "bonus": 10},
    {"coins": 680, "yuan": 68, "bonus": 40, "hot": True}, {"coins": 1280, "yuan": 128, "bonus": 100},
    {"coins": 3280, "yuan": 328, "bonus": 300}, {"coins": 6480, "yuan": 648, "bonus": 700},
]
VIP_PLANS = [
    {"id": "m1", "title": "1个月", "yuan": 30, "origin": 45, "days": 30},
    {"id": "m3", "title": "3个月", "yuan": 78, "origin": 135, "days": 90},
    {"id": "m12", "title": "12个月", "yuan": 258, "origin": 540, "days": 365},
]
VIP_CALL_DISCOUNT = 0.9
VIP_DAILY_COINS = 10
BANNERS = [
    {"title": "邀请用户得奖励", "sub": "超值大奖等你拿", "tag": "长期有效", "go": "/invite",
     "bg": "linear-gradient(120deg,#8d5cff,#e45cff 60%,#ff7ab0)"},
    {"title": "首充加送", "sub": "充值最高送 700 金币", "tag": "限时活动", "go": "/recharge",
     "bg": "linear-gradient(120deg,#ffcf8a,#ffb26b)"},
    {"title": "完成视频认证", "sub": "获得已认证标识 +10 金币", "tag": "新人任务", "go": "/verify",
     "bg": "linear-gradient(120deg,#36c6f4,#5b8cff)"},
]
DEFAULT_SETTINGS = {"dnd": False, "notify": True, "hideDistance": False, "hideNearby": False, "stealth": False,
                    "lang": "简体中文", "beauty": {"smooth": 50, "white": 40, "ruddy": 30}, "beautyOn": False}


# ---------------- 数据库会话 ----------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------- 登录令牌（HMAC 签名，无需额外依赖） ----------------
TOKEN_TTL = 60 * 60 * 24 * 30


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def make_token(uid: int) -> str:
    body = _b64(json.dumps({"uid": uid, "exp": int(time.time()) + TOKEN_TTL}).encode())
    sig = _b64(hmac.new(config.SECRET.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_token(token: str) -> int | None:
    try:
        body, sig = token.split(".")
        good = _b64(hmac.new(config.SECRET.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return data["uid"] if data["exp"] > time.time() else None
    except Exception:
        return None


def current_user(authorization: str = Header(""), db: Session = Depends(get_db)) -> User:
    uid = read_token(authorization.removeprefix("Bearer ").strip())
    user = db.get(User, uid) if uid else None
    if not user:
        raise HTTPException(401, "请先登录")
    if user.banned:
        raise HTTPException(403, "账号已被封禁，如有疑问请联系客服")
    return user


def admin_only(x_admin_token: str = Header(""), token: str = Query("")):
    if not config.ADMIN_TOKEN or not hmac.compare_digest(x_admin_token or token, config.ADMIN_TOKEN):
        raise HTTPException(403, "无权限")


# ---------------- 金币记账 ----------------
class NotEnoughCoins(HTTPException):
    def __init__(self, need, have):
        super().__init__(402, {"code": "coins", "msg": f"金币不足：需要 {need}，余额 {have}", "need": need, "have": have})


def change(db: Session, user: User, amount: int, title: str, account: str = "coins"):
    """改余额并写流水。扣款不足抛 NotEnoughCoins。调用方负责 commit。"""
    if amount == 0:
        return
    cur = getattr(user, account)
    if amount < 0 and cur + amount < 0:
        raise NotEnoughCoins(-amount, cur)
    setattr(user, account, cur + amount)
    db.add(Ledger(user_id=user.id, account=account, amount=amount, title=title[:40], balance_after=cur + amount))


def notice(db: Session, user_id: int, text: str, channel: str = "system", from_user=False):
    db.add(Notice(user_id=user_id, channel=channel, text=text, from_user=from_user, read=from_user))


def settings_of(u: User) -> dict:
    s = {**DEFAULT_SETTINGS, **(u.settings or {})}
    s["beauty"] = {**DEFAULT_SETTINGS["beauty"], **(u.settings or {}).get("beauty", {})}
    return s


def is_blocked(db: Session, a: int, b: int) -> bool:
    """a 和 b 之间任意一方拉黑了对方。"""
    from .db import Block
    return db.scalar(select(Block.id).where(((Block.user_id == a) & (Block.blocked_id == b)) |
                                            ((Block.user_id == b) & (Block.blocked_id == a)))) is not None


def call_price(host: User, caller: User, media: str) -> int:
    base = host.price if media == "video" else (host.voice_price or max(1, host.price // 2))
    return max(1, int(base * VIP_CALL_DISCOUNT)) if caller.is_vip else base


# ---------------- 对外输出 ----------------
def pub_user(u: User, status: str = "offline", viewer: User | None = None) -> dict:
    s = settings_of(u)
    return {
        "id": u.id, "name": u.name, "sex": u.sex, "age": u.age, "city": u.city, "sign": u.sign,
        "avatar": u.avatar, "photos": u.photos or [], "labels": u.labels or [],
        "isHost": u.is_host, "price": u.price, "voicePrice": u.voice_price or max(1, u.price // 2) if u.is_host else 0,
        "rating": u.rating, "ratingCount": u.rating_count, "vip": u.is_vip, "level": u.level,
        "verified": u.verify_status == "approved", "status": status,
        "isNew": (date.today() - u.created_at.date()).days <= 7,
        "hideDistance": s["hideDistance"],
    }


def me_user(u: User, status="online") -> dict:
    d = pub_user(u, status)
    d.update({
        "phone": u.phone[:3] + "****" + u.phone[-4:], "coins": u.coins, "earnings": u.earnings,
        "vipUntil": u.vip_until.isoformat() if u.vip_until else None, "verifyStatus": u.verify_status,
        "settings": settings_of(u), "inviteCode": u.invite_code,
        "signedToday": u.last_sign == date.today(), "sharedToday": u.last_share == date.today(),
        "profileRewarded": u.profile_rewarded,
        "realname": bool(u.realname_at), "realName": (u.real_name[:1] + "*" * (len(u.real_name) - 1)) if u.real_name else "",
        "idMasked": u.id_masked,
    })
    return d


def fmt_time(dt) -> str:
    if not dt:
        return ""
    today = date.today()
    if dt.date() == today:
        return dt.strftime("%H:%M")
    if (today - dt.date()).days == 1:
        return "昨天"
    return dt.strftime("%m-%d") if dt.year == today.year else dt.strftime("%Y-%m-%d")
