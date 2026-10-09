"""会话、消息（文字/图片/语音/礼物）、礼物、视频/语音通话、通话评价、实时 WebSocket。"""
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from . import config
from .core import (GIFT_BY_ID, GIFTS, NotEnoughCoins, call_price, change, current_user, get_db, is_blocked,
                   pub_user, read_token, settings_of)
from .db import Call, GiftRecord, Message, Rating, SessionLocal, User
from .hub import call_out, hub, message_out

router = APIRouter(prefix="/api")


# ================= 会话 / 消息 =================
@router.get("/conversations")
def conversations(me: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Message).where(or_(Message.from_id == me.id, Message.to_id == me.id))
                      .order_by(Message.id.desc()).limit(3000)).all()
    convs: dict[int, dict] = {}
    for m in rows:
        if me.id in (m.hidden_for or []):
            continue
        peer = m.to_id if m.from_id == me.id else m.from_id
        c = convs.setdefault(peer, {"peer": peer, "last": m, "unread": 0})
        if m.to_id == me.id and not m.read:
            c["unread"] += 1
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(list(convs) or [-1])))}
    out = []
    for c in convs.values():
        u = users.get(c["peer"])
        if not u:
            continue
        out.append({"user": pub_user(u, hub.status(u.id)), "unread": c["unread"], "last": message_out(c["last"])})
    return out


@router.get("/online-users")
def online_users(me: User = Depends(current_user), db: Session = Depends(get_db)):
    ids = [uid for uid in hub.conns if uid != me.id][:200]
    users = db.scalars(select(User).where(User.id.in_(ids or [-1]), User.is_host.is_(True), User.banned.is_(False)).limit(20)).all()
    return [pub_user(u, hub.status(u.id)) for u in users if not is_blocked(db, me.id, u.id)]


@router.get("/conversations/{uid}/messages")
def history(uid: int, before: int | None = None, limit: int = Query(50, le=100),
            me: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(Message).where(or_((Message.from_id == me.id) & (Message.to_id == uid),
                                  (Message.from_id == uid) & (Message.to_id == me.id)))
    if before:
        q = q.where(Message.id < before)
    rows = [m for m in db.scalars(q.order_by(Message.id.desc()).limit(limit)).all() if me.id not in (m.hidden_for or [])]
    db.execute(update(Message).where(Message.from_id == uid, Message.to_id == me.id, Message.read.is_(False)).values(read=True))
    db.commit()
    return [message_out(m) for m in reversed(rows)]


@router.post("/conversations/read-all")
def read_all(me: User = Depends(current_user), db: Session = Depends(get_db)):
    db.execute(update(Message).where(Message.to_id == me.id, Message.read.is_(False)).values(read=True))
    db.commit()
    return {"ok": True}


@router.delete("/conversations/{uid}")
def clear_conversation(uid: int, me: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Message).where(or_((Message.from_id == me.id) & (Message.to_id == uid),
                                                (Message.from_id == uid) & (Message.to_id == me.id)))).all()
    for m in rows:
        if me.id not in (m.hidden_for or []):
            m.hidden_for = [*(m.hidden_for or []), me.id]
    db.commit()
    return {"ok": True}


class MessageIn(BaseModel):
    to: int
    kind: str = Field(pattern="^(text|image|voice)$")
    content: str = Field(min_length=1, max_length=500)
    duration: int = Field(0, ge=0, le=60)


@router.post("/messages")
async def send_message(body: MessageIn, me: User = Depends(current_user), db: Session = Depends(get_db)):
    peer = db.get(User, body.to)
    if not peer or peer.id == me.id or peer.banned:
        raise HTTPException(404, "用户不存在")
    if is_blocked(db, me.id, peer.id):
        raise HTTPException(403, "对方已拒收你的消息")
    if body.kind in ("image", "voice") and not body.content.startswith(f"/uploads/{body.kind}/"):
        raise HTTPException(400, "文件地址无效")
    m = Message(from_id=me.id, to_id=peer.id, kind=body.kind, content=body.content.strip(),
                extra={"duration": body.duration} if body.kind == "voice" else {})
    db.add(m)
    db.commit()
    await hub.push_message(m, {"id": me.id, "name": me.name, "avatar": me.avatar})
    return message_out(m)


# ================= 礼物 =================
@router.get("/gifts")
def gifts():
    return GIFTS


class GiftIn(BaseModel):
    to: int
    giftId: int
    callId: int | None = None


@router.post("/gifts/send")
async def send_gift(body: GiftIn, me: User = Depends(current_user), db: Session = Depends(get_db)):
    g = GIFT_BY_ID.get(body.giftId)
    peer = db.get(User, body.to)
    if not g or not peer or peer.id == me.id:
        raise HTTPException(400, "礼物或用户不存在")
    if is_blocked(db, me.id, peer.id):
        raise HTTPException(403, "无法赠送")
    change(db, me, -g["price"], f"送礼物·{g['name']}")
    change(db, peer, int(g["price"] * config.HOST_SHARE), f"收到礼物·{g['name']}", "earnings")
    db.add(GiftRecord(from_id=me.id, to_id=peer.id, gift_id=g["id"], price=g["price"], call_id=body.callId))
    m = Message(from_id=me.id, to_id=peer.id, kind="gift", content=g["name"], extra={"gift": g, "callId": body.callId})
    db.add(m)
    db.commit()
    await hub.push_message(m, {"id": me.id, "name": me.name, "avatar": me.avatar})
    await hub.send(me.id, {"type": "balance", "coins": me.coins})
    return {"coins": me.coins, "message": message_out(m)}


@router.get("/me/gifts")
def my_gifts(me: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(GiftRecord).where(or_(GiftRecord.from_id == me.id, GiftRecord.to_id == me.id))
                      .order_by(GiftRecord.id.desc()).limit(100)).all()
    names = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_(list({r.from_id for r in rows} | {r.to_id for r in rows}) or [-1])))}
    return [{"gift": GIFT_BY_ID.get(r.gift_id), "sent": r.from_id == me.id,
             "peer": names.get(r.to_id if r.from_id == me.id else r.from_id, ""),
             "time": r.created_at.strftime("%Y-%m-%d %H:%M")} for r in rows]


@router.get("/me/guards")
def my_guards(me: User = Depends(current_user), db: Session = Depends(get_db)):
    """守护：累计送某人礼物 ≥1314 金币。"""
    from sqlalchemy import func
    rows = db.execute(select(GiftRecord.to_id, func.sum(GiftRecord.price)).where(GiftRecord.from_id == me.id)
                      .group_by(GiftRecord.to_id).having(func.sum(GiftRecord.price) >= 1314)).all()
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_([r[0] for r in rows] or [-1])))}
    return [{**pub_user(users[uid], hub.status(uid)), "total": int(total)} for uid, total in rows if uid in users]


# ================= 通话 =================
@router.get("/config")
def rtc_config():
    ice = [{"urls": config.STUN_URLS}]
    if config.TURN_URL:
        ice.append({"urls": config.TURN_URL.split(","), "username": config.TURN_USER, "credential": config.TURN_PASS})
    from .push import enabled_providers, vapid_public_key
    from .providers import alipay_ready, wxpay_ready
    return {"iceServers": ice, "ringTimeout": config.RING_TIMEOUT, "billInterval": config.BILL_INTERVAL,
            "devMode": config.DEV_MODE, "vapidPublicKey": vapid_public_key(), "push": sorted(enabled_providers()),
            "pay": {"alipay": alipay_ready(), "wechat": wxpay_ready(), "mock": config.DEV_MODE}}


class CallIn(BaseModel):
    to: int
    media: str = Field("video", pattern="^(video|voice)$")


@router.post("/calls")
async def start_call(body: CallIn, me: User = Depends(current_user), db: Session = Depends(get_db)):
    peer = db.get(User, body.to)
    if not peer or peer.id == me.id or peer.banned:
        raise HTTPException(404, "用户不存在")
    if is_blocked(db, me.id, peer.id):
        raise HTTPException(403, {"code": "blocked", "msg": "无法呼叫对方"})
    if me.id in hub.in_call:
        raise HTTPException(409, {"code": "self_busy", "msg": "你还有一个通话没有结束"})
    if not hub.online(peer.id):
        from .push import has_devices
        if not has_devices(peer.id):   # 对方离线又没有可推送的设备，打不通
            raise HTTPException(409, {"code": "offline", "msg": "对方不在线"})
    if peer.id in hub.in_call:
        raise HTTPException(409, {"code": "busy", "msg": "对方正在通话中，请稍后再试"})
    if settings_of(peer)["dnd"]:
        raise HTTPException(409, {"code": "dnd", "msg": "对方开启了免打扰"})
    # 谁付费：打给主播 -> 呼叫方付；主播打给普通用户 -> 接听方付（来电页会显示价格）；都不是主播 -> 免费
    if peer.is_host:
        payer, price = me, call_price(peer, me, body.media)
    elif me.is_host:
        payer, price = peer, call_price(me, peer, body.media)
    else:
        payer, price = None, 0
    if payer is me and me.coins < price:
        raise NotEnoughCoins(price, me.coins)
    call = Call(caller_id=me.id, callee_id=peer.id, media=body.media, price=price, payer_id=payer.id if payer else None)
    db.add(call)
    db.commit()
    await hub.ring(call, pub_user(me, "busy"))
    return {"call": call_out(call), "peer": pub_user(peer, "busy")}


def _my_call(db, call_id, me) -> Call:
    call = db.get(Call, call_id)
    if not call or me.id not in (call.caller_id, call.callee_id):
        raise HTTPException(404, "通话不存在")
    return call


@router.post("/calls/{call_id}/accept")
async def accept_call(call_id: int, me: User = Depends(current_user), db: Session = Depends(get_db)):
    call = _my_call(db, call_id, me)
    if call.callee_id != me.id or call.status != "ringing":
        raise HTTPException(409, "通话已结束")
    if call.payer_id == me.id and me.coins < call.price:
        raise NotEnoughCoins(call.price, me.coins)
    if not await hub.accept(call_id):
        raise HTTPException(402, {"code": "coins", "msg": "付费方金币不足，通话无法接通"})
    db.refresh(call)
    return {"call": call_out(call)}


@router.post("/calls/{call_id}/reject")
async def reject_call(call_id: int, me: User = Depends(current_user), db: Session = Depends(get_db)):
    return await _finish(call_id, "reject", me, db)


@router.post("/calls/{call_id}/cancel")
async def cancel_call(call_id: int, me: User = Depends(current_user), db: Session = Depends(get_db)):
    return await _finish(call_id, "cancel", me, db)


@router.post("/calls/{call_id}/end")
async def end_call(call_id: int, me: User = Depends(current_user), db: Session = Depends(get_db)):
    return await _finish(call_id, "end", me, db)


async def _finish(call_id, action, me, db):
    call = _my_call(db, call_id, me)
    reason = {"reject": "reject", "cancel": "cancel", "end": "hangup"}[action]
    if action == "reject" and call.callee_id != me.id:
        raise HTTPException(403)
    if call.status == "ringing" and action == "end":
        reason = "cancel" if me.id == call.caller_id else "reject"
    out = await hub.end_call(call_id, reason, by=me.id)
    db.refresh(call)
    return {"call": out or call_out(call)}


@router.get("/calls")
def call_records(me: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Call).where(or_(Call.caller_id == me.id, Call.callee_id == me.id))
                      .order_by(Call.id.desc()).limit(100)).all()
    ids = {c.caller_id for c in rows} | {c.callee_id for c in rows}
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(list(ids) or [-1])))}
    return [{**call_out(c), "outgoing": c.caller_id == me.id,
             "peer": pub_user(users[c.callee_id if c.caller_id == me.id else c.caller_id])} for c in rows]


class RatingIn(BaseModel):
    stars: int = Field(ge=1, le=5)
    tags: list[str] = Field(default_factory=list, max_length=6)


@router.post("/calls/{call_id}/rating")
def rate_call(call_id: int, body: RatingIn, me: User = Depends(current_user), db: Session = Depends(get_db)):
    call = _my_call(db, call_id, me)
    if call.status != "ended":
        raise HTTPException(400, "只能评价已接通的通话")
    if db.scalar(select(Rating.id).where(Rating.call_id == call_id, Rating.rater_id == me.id)):
        raise HTTPException(400, "已经评价过了")
    target = db.get(User, call.callee_id if call.caller_id == me.id else call.caller_id)
    db.add(Rating(call_id=call_id, rater_id=me.id, target_id=target.id, stars=body.stars, tags=[t[:8] for t in body.tags]))
    target.rating_sum += body.stars
    target.rating_count += 1
    db.commit()
    return {"ok": True}


@router.get("/me/ratings")
def my_ratings(me: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Rating).where(Rating.rater_id == me.id).order_by(Rating.id.desc()).limit(100)).all()
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_([r.target_id for r in rows] or [-1])))}
    return [{"user": pub_user(users[r.target_id]), "stars": r.stars, "tags": r.tags,
             "time": r.created_at.strftime("%Y-%m-%d")} for r in rows if r.target_id in users]


# ================= WebSocket =================
@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str = ""):
    uid = read_token(token)
    if not uid:
        await ws.close(code=4401)
        return
    with SessionLocal() as db:
        u = db.get(User, uid)
        if not u or u.banned:
            await ws.close(code=4403)
            return
    await ws.accept()
    await hub.connect(uid, ws)
    await ws.send_json({"type": "hello", "uid": uid})
    try:
        while True:
            msg = await ws.receive_json()
            t = msg.get("type")
            if t == "ping":
                await ws.send_json({"type": "pong"})
            elif t == "signal":
                await hub.relay_signal(uid, int(msg.get("callId", 0)), msg.get("data") or {})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await hub.disconnect(uid, ws)
