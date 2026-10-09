"""管理后台接口（需要请求头 X-Admin-Token = SEEU_ADMIN_TOKEN）。页面在 /admin.html。"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .core import admin_only, change, fmt_time, get_db, notice
from .db import Call, GameOrder, Message, Notice, Order, Post, Report, User, Withdrawal
from .hub import hub

router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin_only)])


def _today():
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)


def _user_row(u: User) -> dict:
    return {"id": u.id, "name": u.name, "phone": u.phone, "sex": u.sex, "age": u.age, "city": u.city, "avatar": u.avatar,
            "coins": u.coins, "earnings": u.earnings, "isHost": u.is_host, "price": u.price, "verify": u.verify_status,
            "realName": u.real_name, "idMasked": u.id_masked, "banned": u.banned, "isTest": u.is_test,
            "online": hub.status(u.id), "created": u.created_at.strftime("%Y-%m-%d %H:%M")}


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    t = _today()
    return {
        "users": db.scalar(select(func.count(User.id))),
        "newToday": db.scalar(select(func.count(User.id)).where(User.created_at >= t)),
        "online": len(hub.conns),
        "inCall": len(hub.in_call) // 2,
        "hosts": db.scalar(select(func.count(User.id)).where(User.is_host.is_(True))),
        "callsToday": db.scalar(select(func.count(Call.id)).where(Call.created_at >= t, Call.status == "ended")),
        "callMinutesToday": int((db.scalar(select(func.sum(Call.seconds)).where(Call.created_at >= t)) or 0) / 60),
        "revenueToday": float(db.scalar(select(func.sum(Order.yuan)).where(Order.paid_at >= t, Order.status == "paid")) or 0),
        "revenueTotal": float(db.scalar(select(func.sum(Order.yuan)).where(Order.status == "paid")) or 0),
        "pending": {
            "verify": db.scalar(select(func.count(User.id)).where(User.verify_status == "pending")),
            "reports": db.scalar(select(func.count(Report.id)).where(Report.status == "open")),
            "withdrawals": db.scalar(select(func.count(Withdrawal.id)).where(Withdrawal.status == "pending")),
            "disputes": db.scalar(select(func.count(GameOrder.id)).where(GameOrder.status == "disputed")),
            "service": len(_service_waiting(db)),
        },
    }


# ---------- 认证审核 ----------
@router.get("/verify")
def verify_list(db: Session = Depends(get_db)):
    return [{**_user_row(u), "video": u.verify_video} for u in db.scalars(select(User).where(User.verify_status == "pending"))]


@router.post("/verify/{uid}")
def review_verify(uid: int, approve: bool, db: Session = Depends(get_db)):
    u = db.get(User, uid)
    if not u or u.verify_status != "pending":
        raise HTTPException(404, "没有待审核的认证")
    u.verify_status = "approved" if approve else "rejected"
    if approve:
        change(db, u, 10, "视频认证奖励")
    notice(db, u.id, "视频认证已通过，获得 10 金币。完成实名认证后可以在「接听设置」开通接听收费" if approve
           else "视频认证未通过，请正对镜头、光线充足后重新提交")
    db.commit()
    return {"ok": True}


# ---------- 举报 ----------
@router.get("/reports")
def reports(status: str = "open", db: Session = Depends(get_db)):
    rows = db.scalars(select(Report).where(Report.status == status).order_by(Report.id.desc()).limit(200)).all()
    out = []
    for r in rows:
        target_user, content = None, ""
        if r.target_type == "user":
            target_user = db.get(User, r.target_id)
        elif r.target_type == "post":
            p = db.get(Post, r.target_id)
            if p:
                target_user, content = db.get(User, p.user_id), p.text or "[图片/视频]"
        elif r.target_type == "message":
            m = db.get(Message, r.target_id)
            if m:
                target_user, content = db.get(User, m.from_id), m.content
        reporter = db.get(User, r.reporter_id)
        out.append({"id": r.id, "type": r.target_type, "targetId": r.target_id, "reason": r.reason, "content": content,
                    "time": fmt_time(r.created_at), "reporter": reporter.name if reporter else "",
                    "target": _user_row(target_user) if target_user else None})
    return out


@router.post("/reports/{rid}/close")
def close_report(rid: int, delete_post: bool = False, db: Session = Depends(get_db)):
    r = db.get(Report, rid)
    if not r:
        raise HTTPException(404)
    r.status = "closed"
    if delete_post and r.target_type == "post":
        p = db.get(Post, r.target_id)
        if p:
            p.deleted = True
    db.commit()
    return {"ok": True}


# ---------- 提现 ----------
@router.get("/withdrawals")
def withdrawals(status: str = "pending", db: Session = Depends(get_db)):
    rows = db.scalars(select(Withdrawal).where(Withdrawal.status == status).order_by(Withdrawal.id.desc()).limit(200)).all()
    return [{"id": w.id, "user": _user_row(db.get(User, w.user_id)), "coins": w.coins, "yuan": w.yuan,
             "account": w.account, "note": w.note, "time": w.created_at.strftime("%Y-%m-%d %H:%M")} for w in rows]


class HandleIn(BaseModel):
    note: str = Field("", max_length=100)


@router.post("/withdrawals/{wid}/{action}")
def handle_withdrawal(wid: int, action: str, body: HandleIn | None = None, db: Session = Depends(get_db)):
    w = db.get(Withdrawal, wid)
    if not w or w.status != "pending" or action not in ("paid", "reject"):
        raise HTTPException(404, "没有这笔待处理的提现")
    note = (body.note if body else "").strip()
    w.status, w.note, w.handled_at = ("paid" if action == "paid" else "rejected"), note, datetime.now()
    u = db.get(User, w.user_id)
    if action == "reject":
        change(db, u, w.coins, "提现驳回退回", "earnings")
        notice(db, u.id, f"提现 {w.yuan:.2f} 元被驳回，收益已退回。{note}")
    else:
        notice(db, u.id, f"提现 {w.yuan:.2f} 元已打款，请查收。{note}")
    db.commit()
    return {"ok": True}


# ---------- 客服 ----------
AUTO = "【自动回复】"


def _service_waiting(db) -> list[int]:
    """等人工回复的用户：最后一条是用户发的，或者只有自动回复。"""
    last = db.execute(select(Notice.user_id, func.max(Notice.id)).where(Notice.channel == "service").group_by(Notice.user_id)).all()
    rows = db.scalars(select(Notice).where(Notice.id.in_([mid for _, mid in last] or [-1]))).all()
    return [n.user_id for n in rows if n.from_user or n.text.startswith(AUTO)]


@router.get("/service")
def service_inbox(db: Session = Depends(get_db)):
    waiting = set(_service_waiting(db))
    last = db.execute(select(Notice.user_id, func.max(Notice.id)).where(Notice.channel == "service", Notice.from_user.is_(True))
                      .group_by(Notice.user_id).order_by(func.max(Notice.id).desc()).limit(100)).all()
    out = []
    for uid, mid in last:
        n, u = db.get(Notice, mid), db.get(User, uid)
        if u:
            out.append({"user": _user_row(u), "last": n.text, "time": fmt_time(n.created_at), "waiting": uid in waiting})
    return out


@router.get("/service/{uid}")
def service_thread(uid: int, db: Session = Depends(get_db)):
    rows = db.scalars(select(Notice).where(Notice.user_id == uid, Notice.channel == "service").order_by(Notice.id)).all()
    return [{"text": n.text, "me": n.from_user, "time": n.created_at.strftime("%m-%d %H:%M")} for n in rows]


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


@router.post("/service/{uid}")
async def service_reply(uid: int, body: ReplyIn, db: Session = Depends(get_db)):
    notice(db, uid, body.text, "service")
    db.commit()
    await hub.send(uid, {"type": "notice", "text": "客服回复了你的消息"})
    return {"ok": True}


# ---------- 用户 ----------
@router.get("/users")
def users(q: str = "", db: Session = Depends(get_db)):
    query = select(User).order_by(User.id.desc()).limit(50)
    if q:
        conds = [User.phone.contains(q), User.name.contains(q)]
        if q.isdigit() and len(q) <= 9:   # 手机号太长，不能当 ID 查（PostgreSQL 整数会溢出）
            conds.append(User.id == int(q))
        query = query.where(or_(*conds))
    return [_user_row(u) for u in db.scalars(query)]


@router.post("/users/{uid}/ban")
async def ban(uid: int, banned: bool = True, db: Session = Depends(get_db)):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404)
    u.banned = banned
    if banned:
        u.is_host = False
    db.commit()
    if banned:
        call_id = hub.in_call.get(uid)
        if call_id:
            await hub.end_call(call_id, "hangup")
        for ws in list(hub.conns.get(uid, ())):
            await ws.close(code=4403)
    return {"ok": True}


class CoinsIn(BaseModel):
    amount: int
    reason: str = Field(min_length=1, max_length=30)


@router.post("/users/{uid}/coins")
def adjust_coins(uid: int, body: CoinsIn, db: Session = Depends(get_db)):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404)
    change(db, u, body.amount, f"客服调整·{body.reason}")
    notice(db, uid, f"客服为你{'增加' if body.amount > 0 else '扣除'}了 {abs(body.amount)} 金币：{body.reason}")
    db.commit()
    return _user_row(u)


# ---------- 陪玩纠纷 ----------
@router.get("/disputes")
def disputes(db: Session = Depends(get_db)):
    rows = db.scalars(select(GameOrder).where(GameOrder.status == "disputed").order_by(GameOrder.id)).all()
    return [{"id": o.id, "game": o.game, "qty": o.qty, "unit": o.unit, "total": o.total, "reason": o.reason, "note": o.note,
             "buyer": _user_row(db.get(User, o.buyer_id)), "seller": _user_row(db.get(User, o.seller_id)),
             "time": o.created_at.strftime("%m-%d %H:%M")} for o in rows]


@router.post("/disputes/{oid}")
def resolve_dispute(oid: int, refund: bool, db: Session = Depends(get_db)):
    from .api_game import _pay_seller, _refund
    o = db.get(GameOrder, oid)
    if not o or o.status != "disputed":
        raise HTTPException(404, "没有这个申诉")
    o.finished_at = datetime.now()
    if refund:
        o.status = "refunded"
        _refund(db, o, f"陪玩退款·{o.game}")
        notice(db, o.buyer_id, f"陪玩订单 #{o.id} 退款成功，{o.total} 金币已退回")
        notice(db, o.seller_id, f"陪玩订单 #{o.id} 经客服核实已退款给买家")
    else:
        o.status = "completed"
        _pay_seller(db, o)
        notice(db, o.buyer_id, f"陪玩订单 #{o.id} 经客服核实服务已完成，退款申请未通过")
        notice(db, o.seller_id, f"陪玩订单 #{o.id} 申诉已处理，收益已到账")
    db.commit()
    return {"ok": True}
