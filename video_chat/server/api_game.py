"""游戏陪玩：技能管理、下单（金币托管）、接单、完成、确认、取消退款、申诉、评价。"""
import asyncio
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import config
from .core import change, current_user, fmt_time, get_db, is_blocked, notice, pub_user
from .db import GameOrder, GameSkill, SessionLocal, User
from .hub import hub

router = APIRouter(prefix="/api")

GAMES = ["王者荣耀", "和平精英", "英雄联盟", "金铲铲之战", "原神", "蛋仔派对", "第五人格", "永劫无间", "穿越火线", "其他"]
STATUS_TEXT = {"pending": "待接单", "accepted": "进行中", "delivered": "待确认", "completed": "已完成",
               "canceled": "已取消", "disputed": "退款申诉中", "refunded": "已退款"}


def skill_out(s: GameSkill, u: User | None = None) -> dict:
    d = {"id": s.id, "userId": s.user_id, "game": s.game, "rank": s.rank, "price": s.price, "unit": s.unit,
         "intro": s.intro, "images": s.images or [], "enabled": s.enabled, "ordersDone": s.orders_done,
         "rating": round(s.rating_sum / s.rating_count, 1) if s.rating_count else 5.0}
    if u:
        d["user"] = pub_user(u, hub.status(u.id))
    return d


def order_out(o: GameOrder, me_id: int, users: dict) -> dict:
    peer = users.get(o.seller_id if o.buyer_id == me_id else o.buyer_id)
    return {"id": o.id, "skillId": o.skill_id, "game": o.game, "unit": o.unit, "price": o.price, "qty": o.qty,
            "total": o.total, "note": o.note, "status": o.status, "statusText": STATUS_TEXT[o.status], "reason": o.reason,
            "stars": o.stars, "review": o.review, "role": "buyer" if o.buyer_id == me_id else "seller",
            "peer": pub_user(peer, hub.status(peer.id)) if peer else None, "time": fmt_time(o.created_at),
            "autoConfirmAt": (o.delivered_at + timedelta(hours=config.GAME_AUTO_CONFIRM_HOURS)).strftime("%m-%d %H:%M")
            if o.delivered_at and o.status == "delivered" else None}


async def _tell(uid: int, text: str, order_id: int):
    await hub.send(uid, {"type": "notice", "text": text, "gameOrder": order_id})
    if not hub.online(uid):
        from .push import push_later
        push_later(uid, "陪玩订单", text, "/#/gameorders", "message", "game")


# ================= 技能 =================
@router.get("/games")
def games():
    return GAMES


class SkillIn(BaseModel):
    game: str = Field(min_length=1, max_length=20)
    rank: str = Field("", max_length=20)
    price: int = Field(ge=5, le=2000)
    unit: str = Field("局", pattern="^(局|小时)$")
    intro: str = Field("", max_length=200)
    images: list[str] = Field(default_factory=list, max_length=6)
    enabled: bool = True


def _check_seller(me: User):
    if not me.realname_at:
        raise HTTPException(403, {"code": "realname", "msg": "接陪玩订单需要先完成实名认证"})


@router.get("/me/skills")
def my_skills(me: User = Depends(current_user), db: Session = Depends(get_db)):
    return [skill_out(s) for s in db.scalars(select(GameSkill).where(GameSkill.user_id == me.id, GameSkill.deleted.is_(False)))]


@router.post("/skills")
def add_skill(body: SkillIn, me: User = Depends(current_user), db: Session = Depends(get_db)):
    _check_seller(me)
    if body.game not in GAMES:
        raise HTTPException(400, "不支持的游戏")
    if any(not i.startswith("/uploads/image/") for i in body.images):
        raise HTTPException(400, "图片地址无效")
    if db.scalar(select(GameSkill.id).where(GameSkill.user_id == me.id, GameSkill.game == body.game, GameSkill.deleted.is_(False))):
        raise HTTPException(400, "这个游戏的技能已经添加过了，可以直接编辑")
    s = GameSkill(user_id=me.id, **body.model_dump())
    db.add(s)
    db.commit()
    return skill_out(s)


@router.put("/skills/{sid}")
def edit_skill(sid: int, body: SkillIn, me: User = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(GameSkill, sid)
    if not s or s.user_id != me.id or s.deleted:
        raise HTTPException(404, "技能不存在")
    if any(not i.startswith("/uploads/image/") for i in body.images):
        raise HTTPException(400, "图片地址无效")
    for k, v in body.model_dump().items():
        if k != "game":
            setattr(s, k, v)
    db.commit()
    return skill_out(s)


@router.delete("/skills/{sid}")
def delete_skill(sid: int, me: User = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(GameSkill, sid)
    if not s or s.user_id != me.id:
        raise HTTPException(404, "技能不存在")
    s.deleted = True
    db.commit()
    return {"ok": True}


@router.get("/users/{uid}/skills")
def user_skills(uid: int, me: User = Depends(current_user), db: Session = Depends(get_db)):
    return [skill_out(s) for s in db.scalars(select(GameSkill).where(
        GameSkill.user_id == uid, GameSkill.enabled.is_(True), GameSkill.deleted.is_(False)))]


@router.get("/skills")
def browse_skills(game: str = "", me: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(GameSkill, User).join(User, User.id == GameSkill.user_id).where(
        GameSkill.enabled.is_(True), GameSkill.deleted.is_(False), User.banned.is_(False), User.id != me.id)
    if game:
        q = q.where(GameSkill.game == game)
    rows = db.execute(q.order_by(GameSkill.orders_done.desc()).limit(100)).all()
    return [skill_out(s, u) for s, u in rows if not is_blocked(db, me.id, u.id)]


# ================= 订单 =================
class OrderIn(BaseModel):
    skillId: int
    qty: int = Field(ge=1, le=20)
    note: str = Field("", max_length=200)


@router.post("/game-orders")
async def create_game_order(body: OrderIn, me: User = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(GameSkill, body.skillId)
    if not s or s.deleted or not s.enabled:
        raise HTTPException(404, "这个技能已下架")
    if s.user_id == me.id:
        raise HTTPException(400, "不能给自己下单")
    if is_blocked(db, me.id, s.user_id):
        raise HTTPException(403, "无法下单")
    total = s.price * body.qty
    change(db, me, -total, f"陪玩下单·{s.game}")   # 金币先托管在平台
    o = GameOrder(skill_id=s.id, buyer_id=me.id, seller_id=s.user_id, game=s.game, unit=s.unit, price=s.price,
                  qty=body.qty, total=total, note=body.note.strip())
    db.add(o)
    db.commit()
    await hub.send(me.id, {"type": "balance", "coins": me.coins})
    await _tell(s.user_id, f"{me.name} 下了 {s.game} 陪玩订单 ×{body.qty}{s.unit}，请尽快接单", o.id)
    return order_out(o, me.id, {s.user_id: db.get(User, s.user_id)})


@router.get("/game-orders")
def list_game_orders(role: str = "buyer", me: User = Depends(current_user), db: Session = Depends(get_db)):
    settle_due(db)
    col = GameOrder.buyer_id if role == "buyer" else GameOrder.seller_id
    rows = db.scalars(select(GameOrder).where(col == me.id).order_by(GameOrder.id.desc()).limit(100)).all()
    ids = {o.buyer_id for o in rows} | {o.seller_id for o in rows}
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(list(ids) or [-1])))}
    return [order_out(o, me.id, users) for o in rows]


def _refund(db, o: GameOrder, title: str):
    buyer = db.get(User, o.buyer_id)
    change(db, buyer, o.total, title)
    return buyer


def _pay_seller(db, o: GameOrder):
    seller = db.get(User, o.seller_id)
    change(db, seller, int(o.total * config.GAME_SHARE), f"陪玩收益·{o.game}", "earnings")
    s = db.get(GameSkill, o.skill_id)
    if s:
        s.orders_done += 1


class ActionIn(BaseModel):
    reason: str = Field("", max_length=200)
    stars: int = Field(0, ge=0, le=5)
    review: str = Field("", max_length=200)


@router.post("/game-orders/{oid}/{action}")
async def game_order_action(oid: int, action: str, body: ActionIn | None = None, me: User = Depends(current_user),
                            db: Session = Depends(get_db)):
    body = body or ActionIn()
    o = db.get(GameOrder, oid)
    if not o or me.id not in (o.buyer_id, o.seller_id):
        raise HTTPException(404, "订单不存在")
    buyer = me.id == o.buyer_id
    now = datetime.now()
    msg_to, msg = None, ""
    if action == "accept" and not buyer and o.status == "pending":
        o.status, o.accepted_at = "accepted", now
        msg_to, msg = o.buyer_id, f"{me.name} 已接单，快去私信约时间开黑吧"
    elif action == "reject" and not buyer and o.status == "pending":
        o.status, o.reason, o.finished_at = "canceled", body.reason or "陪玩师拒绝接单", now
        _refund(db, o, f"陪玩退款·{o.game}")
        msg_to, msg = o.buyer_id, f"{me.name} 没有接单，{o.total} 金币已退回"
    elif action == "cancel" and buyer and o.status == "pending":
        o.status, o.reason, o.finished_at = "canceled", body.reason or "买家取消", now
        _refund(db, o, f"陪玩退款·{o.game}")
        msg_to, msg = o.seller_id, f"{me.name} 取消了 {o.game} 订单"
    elif action == "deliver" and not buyer and o.status == "accepted":
        o.status, o.delivered_at = "delivered", now
        msg_to, msg = o.buyer_id, f"{me.name} 已完成陪玩，请确认（{config.GAME_AUTO_CONFIRM_HOURS} 小时后自动确认）"
    elif action == "confirm" and buyer and o.status in ("accepted", "delivered"):
        o.status, o.finished_at = "completed", now
        _pay_seller(db, o)
        msg_to, msg = o.seller_id, f"{me.name} 确认完成，收益已到账"
    elif action == "refund" and buyer and o.status in ("accepted", "delivered"):
        if not body.reason.strip():
            raise HTTPException(400, "请填写退款原因")
        o.status, o.reason = "disputed", body.reason.strip()
        msg_to, msg = o.seller_id, f"{me.name} 申请退款，客服会介入处理"
        notice(db, o.buyer_id, f"陪玩订单 #{o.id} 退款申请已提交，客服会在 24 小时内处理")
    elif action == "review" and buyer and o.status == "completed" and not o.stars:
        if not 1 <= body.stars <= 5:
            raise HTTPException(400, "请选择评分")
        o.stars, o.review = body.stars, body.review.strip()
        s = db.get(GameSkill, o.skill_id)
        if s:
            s.rating_sum += body.stars
            s.rating_count += 1
    else:
        raise HTTPException(409, f"当前订单状态（{STATUS_TEXT[o.status]}）不能这样操作")
    db.commit()
    if msg_to:
        await _tell(msg_to, msg, o.id)
    for uid in (o.buyer_id, o.seller_id):
        u = db.get(User, uid)
        await hub.send(uid, {"type": "balance", "coins": u.coins})
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_([o.buyer_id, o.seller_id])))}
    return order_out(o, me.id, users)


def settle_due(db: Session) -> int:
    """超时处理：待接单超时自动取消退款；陪玩师完成后买家一直不确认，到时间自动确认。"""
    now = datetime.now()
    n = 0
    for o in db.scalars(select(GameOrder).where(or_(
            (GameOrder.status == "pending") & (GameOrder.created_at < now - timedelta(minutes=config.GAME_ACCEPT_TIMEOUT_MIN)),
            (GameOrder.status == "delivered") & (GameOrder.delivered_at < now - timedelta(hours=config.GAME_AUTO_CONFIRM_HOURS))))):
        if o.status == "pending":
            o.status, o.reason, o.finished_at = "canceled", "陪玩师超时未接单", now
            _refund(db, o, f"陪玩退款·{o.game}")
            notice(db, o.buyer_id, f"{o.game} 陪玩订单超时未接单，{o.total} 金币已退回")
        else:
            o.status, o.finished_at = "completed", now
            _pay_seller(db, o)
        n += 1
    if n:
        db.commit()
    return n


async def settle_loop():
    while True:
        await asyncio.sleep(60)
        try:
            with SessionLocal() as db:
                settle_due(db)
        except Exception:
            pass
