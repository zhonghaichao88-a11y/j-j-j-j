"""账号、资料、上传、设置、任务、邀请、认证、钱包、充值、VIP、提现、系统消息、客服。"""
import hashlib
import random
import re
import secrets
import string
import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config, providers
from .core import (PACKS, VIP_DAILY_COINS, VIP_PLANS, admin_only, change, current_user, get_db, make_token,
                   me_user, notice, settings_of)
from .db import Ledger, Notice, Order, Report, SmsCode, User, Withdrawal
from .hub import hub

router = APIRouter(prefix="/api")


# ================= 登录 =================
class SmsIn(BaseModel):
    phone: str


def send_sms(phone: str, code: str):
    """配置了阿里云短信就真发；开发模式下没配置则只把验证码返回给页面。"""
    if config.ALIYUN_AK and config.SMS_TEMPLATE:
        providers.send_sms_code(phone, code)
    elif not config.DEV_MODE:
        raise HTTPException(503, "短信服务未配置")


@router.post("/auth/sms")
def sms(body: SmsIn, db: Session = Depends(get_db)):
    if not re.fullmatch(r"1\d{10}", body.phone):
        raise HTTPException(400, "手机号格式不对")
    last = db.scalar(select(SmsCode).where(SmsCode.phone == body.phone).order_by(SmsCode.id.desc()))
    if last and last.expires - timedelta(minutes=4) > datetime.now():
        raise HTTPException(429, "发送太频繁，请 1 分钟后再试")
    code = f"{random.randint(0, 999999):06d}"
    db.add(SmsCode(phone=body.phone, code=code, expires=datetime.now() + timedelta(minutes=5)))
    db.commit()
    send_sms(body.phone, code)
    return {"ok": True, "devCode": code if config.DEV_MODE else None}


class LoginIn(BaseModel):
    phone: str
    code: str
    invite: str = ""


def _new_invite_code(db):
    while True:
        c = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
        if not db.scalar(select(User.id).where(User.invite_code == c)):
            return c


@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    rec = db.scalar(select(SmsCode).where(SmsCode.phone == body.phone, SmsCode.used.is_(False))
                    .order_by(SmsCode.id.desc()))
    if not rec or rec.expires < datetime.now() or not secrets.compare_digest(rec.code, body.code.strip()):
        raise HTTPException(400, "验证码错误或已过期")
    rec.used = True
    user = db.scalar(select(User).where(User.phone == body.phone))
    if not user:
        inviter = db.scalar(select(User).where(User.invite_code == body.invite.strip().upper())) if body.invite else None
        user = User(phone=body.phone, name=f"用户{body.phone[-4:]}", invite_code=_new_invite_code(db),
                    invited_by=inviter.id if inviter else None, settings={})
        db.add(user)
        db.flush()
        notice(db, user.id, "欢迎来到 SeeU！完成视频认证可获得 10 金币，平台不会以任何理由要求你私下转账。")
    if user.banned:
        raise HTTPException(403, "账号已被封禁")
    db.commit()
    return {"token": make_token(user.id), "me": me_user(user)}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return me_user(user, hub.status(user.id))


class ProfileIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=12)
    sex: str | None = Field(None, pattern="^[mf]$")
    age: int | None = Field(None, ge=18, le=80)
    city: str | None = Field(None, max_length=10)
    sign: str | None = Field(None, max_length=60)
    avatar: str | None = Field(None, max_length=255)
    photos: list[str] | None = Field(None, max_length=9)
    labels: list[str] | None = Field(None, max_length=8)


@router.put("/me")
def update_me(body: ProfileIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    for k, v in body.model_dump(exclude_none=True).items():
        if k in ("avatar",) and v and not v.startswith("/uploads/"):
            raise HTTPException(400, "头像地址无效")
        setattr(user, k, v.strip() if isinstance(v, str) else v)
    if not user.profile_rewarded and user.avatar and user.sign and user.city:
        user.profile_rewarded = True
        change(db, user, 5, "完善资料奖励")
    db.commit()
    return me_user(user, hub.status(user.id))


SETTING_KEYS = {"dnd", "notify", "hideDistance", "hideNearby", "stealth", "lang", "beauty", "beautyOn"}


@router.put("/me/settings")
def update_settings(body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    bad = set(body) - SETTING_KEYS
    if bad:
        raise HTTPException(400, f"未知设置：{', '.join(bad)}")
    if body.get("stealth") and not user.is_vip:
        raise HTTPException(403, {"code": "vip", "msg": "隐身访问是 VIP 特权"})
    if body.get("lang") and body["lang"] not in ("简体中文", "繁體中文", "English"):
        raise HTTPException(400, "不支持的语言")
    s = settings_of(user)
    if "beauty" in body:
        body["beauty"] = {**s["beauty"], **{k: max(0, min(100, int(v))) for k, v in body["beauty"].items()
                                            if k in ("smooth", "white", "ruddy")}}
    s.update(body)
    user.settings = s
    db.commit()
    return s


# ================= 上传 =================
UPLOAD_TYPES = {
    "image": ({".jpg", ".jpeg", ".png", ".webp", ".gif"}, 10),
    "voice": ({".webm", ".ogg", ".m4a", ".mp4", ".aac", ".wav", ".mp3"}, 5),
    "video": ({".mp4", ".webm", ".mov"}, config.MAX_UPLOAD_MB),
}


@router.post("/upload")
async def upload(kind: str = Form(...), file: UploadFile = File(...), user: User = Depends(current_user)):
    if kind not in UPLOAD_TYPES:
        raise HTTPException(400, "不支持的上传类型")
    exts, max_mb = UPLOAD_TYPES[kind]
    ext = ("." + file.filename.rsplit(".", 1)[-1].lower()) if "." in (file.filename or "") else ""
    if ext not in exts:
        raise HTTPException(400, f"文件格式不支持：{ext or '未知'}")
    folder = config.UPLOAD_DIR / kind
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    size = 0
    with open(folder / name, "wb") as fh:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > max_mb * 1024 * 1024:
                fh.close()
                (folder / name).unlink(missing_ok=True)
                raise HTTPException(413, f"文件不能超过 {max_mb}MB")
            fh.write(chunk)
    return {"url": f"/uploads/{kind}/{name}"}


# ================= 任务 / 邀请 =================
@router.post("/tasks/sign")
def sign_in(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.last_sign == date.today():
        raise HTTPException(400, "今天已经签到过了")
    user.last_sign = date.today()
    change(db, user, 1, "签到奖励")
    if user.is_vip:
        change(db, user, VIP_DAILY_COINS, "VIP 每日赠币")
    notice(db, user.id, "签到成功，获赠金币，快去打给钟意的人吧！", "service")
    db.commit()
    return me_user(user)


@router.post("/tasks/share")
def share_task(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.last_share != date.today():
        user.last_share = date.today()
        change(db, user, 2, "分享奖励")
        db.commit()
    return me_user(user)


@router.get("/invite")
def invite(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(User).where(User.invited_by == user.id).order_by(User.id.desc()).limit(100)).all()
    return {"code": user.invite_code, "list": [{"id": u.id, "name": u.name, "avatar": u.avatar,
                                               "paid": u.first_paid, "time": u.created_at.strftime("%m-%d")} for u in rows]}


# ================= 实名认证 =================
class RealnameIn(BaseModel):
    name: str = Field(min_length=2, max_length=20)
    idNo: str = Field(min_length=18, max_length=18)


@router.post("/realname")
def realname(body: RealnameIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.realname_at:
        raise HTTPException(400, "你已经完成实名认证")
    try:
        birth = providers.check_id_number(body.idNo)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if providers.age_on(birth) < 18:
        raise HTTPException(403, "未满 18 周岁不能使用本平台")
    id_hash = hashlib.sha256(f"{config.SECRET}:{body.idNo.upper()}".encode()).hexdigest()
    if db.scalar(select(User.id).where(User.id_hash == id_hash, User.id != user.id)):
        raise HTTPException(400, "这个身份证已经认证过其它账号")
    if not providers.verify_identity(body.name.strip(), body.idNo.upper()):
        raise HTTPException(400, "姓名和身份证号不一致")
    user.real_name, user.id_hash = body.name.strip(), id_hash
    user.id_masked = body.idNo[:3] + "*" * 11 + body.idNo[-4:]
    user.realname_at = datetime.now()
    db.commit()
    return me_user(user)


# ================= 视频认证 / 成为主播 =================
class VerifyIn(BaseModel):
    video: str


@router.post("/verify")
def submit_verify(body: VerifyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not body.video.startswith("/uploads/video/"):
        raise HTTPException(400, "请先上传认证视频")
    if user.verify_status == "approved":
        raise HTTPException(400, "你已经通过认证")
    user.verify_status, user.verify_video = "pending", body.video
    db.commit()
    return me_user(user)


class HostIn(BaseModel):
    price: int = Field(ge=5, le=500)
    voicePrice: int = Field(0, ge=0, le=500)
    on: bool = True


@router.put("/me/host")
def set_host(body: HostIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.on and user.verify_status != "approved":
        raise HTTPException(403, "完成视频认证后才能开通接听收费")
    if body.on and not user.realname_at:
        raise HTTPException(403, {"code": "realname", "msg": "开通接听收费前需要先完成实名认证"})
    user.is_host, user.price, user.voice_price = body.on, body.price, body.voicePrice
    db.commit()
    return me_user(user)


# ================= 钱包 / 充值 / VIP / 提现 =================
@router.get("/wallet")
def wallet(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Ledger).where(Ledger.user_id == user.id).order_by(Ledger.id.desc()).limit(100)).all()
    return {"coins": user.coins, "earnings": user.earnings, "packs": PACKS, "vipPlans": VIP_PLANS,
            "ledger": [{"title": r.title, "amount": r.amount, "account": r.account,
                        "time": r.created_at.strftime("%Y-%m-%d %H:%M")} for r in rows]}


class OrderIn(BaseModel):
    kind: str = Field(pattern="^(coins|vip)$")
    pack: int | None = None
    plan: str | None = None
    channel: str = Field(pattern="^(wechat|alipay|apple|mock)$")


@router.post("/orders")
def create_order(body: OrderIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.kind == "coins":
        if body.pack is None or not 0 <= body.pack < len(PACKS):
            raise HTTPException(400, "请选择充值档位")
        p = PACKS[body.pack]
        order = Order(id=uuid.uuid4().hex, user_id=user.id, kind="coins", yuan=p["yuan"], coins=p["coins"] + p["bonus"],
                      channel=body.channel)
    else:
        plan = next((x for x in VIP_PLANS if x["id"] == body.plan), None)
        if not plan:
            raise HTTPException(400, "请选择会员套餐")
        order = Order(id=uuid.uuid4().hex, user_id=user.id, kind="vip", yuan=plan["yuan"], vip_days=plan["days"],
                      channel=body.channel)
    if body.channel == "mock" and not config.DEV_MODE:
        raise HTTPException(400, "支付方式不可用")
    ready = {"mock": True, "alipay": providers.alipay_ready(), "wechat": providers.wxpay_ready(), "apple": False}[body.channel]
    if not ready:
        raise HTTPException(503, {"code": "pay_not_configured", "msg": "这个支付通道还没开通，请换一种方式或联系客服"})
    db.add(order)
    db.commit()
    subject = f"SeeU {order.coins} 金币" if order.kind == "coins" else f"SeeU VIP {order.vip_days} 天"
    if body.channel == "mock":
        pay = {"mock": True}
    elif body.channel == "alipay":
        pay = {"url": providers.alipay_pay_url(order.id, order.yuan, subject)}
    else:
        pay = {"url": providers.wxpay_h5_url(order.id, order.yuan, subject, request.client.host if request.client else "127.0.0.1")}
    return {"orderId": order.id, "yuan": order.yuan, "pay": pay}


@router.get("/orders/{order_id}")
def order_status(order_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order or order.user_id != user.id:
        raise HTTPException(404, "订单不存在")
    return {"orderId": order.id, "status": order.status, "kind": order.kind, "yuan": order.yuan, "coins": order.coins}


def fulfill_order(db: Session, order: Order):
    """支付成功回调后调用：加金币/开会员、首充邀请奖励、系统通知。幂等。"""
    if order.status == "paid":
        return
    order.status, order.paid_at = "paid", datetime.now()
    user = db.get(User, order.user_id)
    if order.kind == "coins":
        change(db, user, order.coins, "充值")
        notice(db, user.id, f"金额：{order.yuan:.2f}；充值：{order.coins}；账户余额：{user.coins}")
        if not user.first_paid:
            user.first_paid = True
            if user.invited_by:
                inviter = db.get(User, user.invited_by)
                change(db, inviter, 50, f"邀请奖励·{user.name}")
                change(db, inviter, int(order.coins * 0.1), f"邀请返现·{user.name}", "earnings")
                notice(db, inviter.id, f"你邀请的 {user.name} 完成首充，奖励 50 金币和 {int(order.coins * 0.1)} 收益已到账")
    else:
        start = max(user.vip_until or date.today(), date.today())
        user.vip_until = start + timedelta(days=order.vip_days)
        notice(db, user.id, f"VIP 开通成功，有效期至 {user.vip_until.isoformat()}")


@router.post("/orders/{order_id}/mock-pay")
async def mock_pay(order_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not config.DEV_MODE:
        raise HTTPException(404)
    order = db.get(Order, order_id)
    if not order or order.user_id != user.id:
        raise HTTPException(404, "订单不存在")
    fulfill_order(db, order)
    db.commit()
    await hub.send(user.id, {"type": "balance", "coins": user.coins})
    return me_user(user)


def _settle(db: Session, info: dict | None) -> bool:
    if not info:
        return False
    order = db.get(Order, info["order_id"])
    if not order or abs(order.yuan - info["yuan"]) > 0.001:   # 金额对不上一律不认
        return False
    fulfill_order(db, order)
    db.commit()
    return True


@router.post("/pay/notify/alipay", response_class=PlainTextResponse)
async def alipay_notify(request: Request, db: Session = Depends(get_db)):
    form = dict(await request.form())
    return "success" if _settle(db, providers.alipay_parse_notify(form)) else "failure"


@router.post("/pay/notify/wechat")
async def wechat_notify(request: Request, db: Session = Depends(get_db)):
    ok = _settle(db, providers.wxpay_parse_notify(dict(request.headers), await request.body()))
    if not ok:
        raise HTTPException(400, {"code": "FAIL", "message": "验签或订单校验失败"})
    return {"code": "SUCCESS", "message": "成功"}


class WithdrawIn(BaseModel):
    coins: int = Field(ge=100)
    account: str = Field(min_length=4, max_length=60)


@router.post("/withdraw")
def withdraw(body: WithdrawIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.earnings < body.coins and not user.is_host:
        raise HTTPException(403, "充值的金币不能提现，主播 / 陪玩收益才可以提现")
    if not user.realname_at:
        raise HTTPException(403, {"code": "realname", "msg": "提现前需要先完成实名认证"})
    change(db, user, -body.coins, "提现申请", "earnings")
    db.add(Withdrawal(user_id=user.id, coins=body.coins, yuan=body.coins / config.COINS_PER_YUAN,
                      account=f"{body.account}（{user.real_name}）"))
    notice(db, user.id, f"提现申请已提交：{body.coins / config.COINS_PER_YUAN:.2f} 元，1-3 个工作日到账")
    db.commit()
    return me_user(user)


# ================= 系统消息 / 客服 =================
FAQ = {
    "充值": "「我的」→「充值」，选择金额并支付即可，到账后会收到系统消息。",
    "收费": "视频通话按对方设置的价格每分钟扣金币，接通时先扣第 1 分钟，不足 1 分钟按 1 分钟计。",
    "提现": "主播收益可在「我的钱包」申请提现，1-3 个工作日到账。充值的金币不能提现。",
    "举报": "在对方主页或聊天页右上角「…」选择举报，我们会 24 小时内处理。",
}


def _notice_out(n: Notice):
    return {"id": n.id, "text": n.text, "me": n.from_user, "read": n.read, "time": n.created_at.strftime("%Y-%m-%d %H:%M")}


@router.get("/notices/{channel}")
def notices(channel: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if channel not in ("system", "service"):
        raise HTTPException(404)
    rows = db.scalars(select(Notice).where(Notice.user_id == user.id, Notice.channel == channel)
                      .order_by(Notice.id.desc()).limit(100)).all()
    out = [_notice_out(n) for n in reversed(rows)]
    for n in rows:
        n.read = True
    db.commit()
    return out


class ServiceIn(BaseModel):
    text: str = Field(min_length=1, max_length=300)


@router.post("/notices/service")
def ask_service(body: ServiceIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    notice(db, user.id, body.text, "service", from_user=True)
    answer = next((a for k, a in FAQ.items() if k in body.text), "已收到你的问题，人工客服会尽快回复你。")
    notice(db, user.id, "【自动回复】" + answer, "service")
    db.commit()
    return notices("service", user, db)


@router.get("/notices-unread")
def notices_unread(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {c: db.scalar(select(func.count()).where(Notice.user_id == user.id, Notice.channel == c, Notice.read.is_(False)))
            for c in ("system", "service")}


