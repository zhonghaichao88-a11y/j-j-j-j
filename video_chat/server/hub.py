"""实时通道：WebSocket 连接管理、在线状态、来电/挂断推送、WebRTC 信令转发、通话按分钟计费。

单进程内存实现，适合起步阶段（几千人同时在线）。用户量上来后把 Hub 换成 Redis 发布订阅，
接口不变。
"""
import asyncio
import logging
from datetime import datetime

from fastapi import WebSocket

from . import config
from .core import NotEnoughCoins, change, fmt_time
from .db import Call, Message, SessionLocal, User

log = logging.getLogger("seeu.hub")


class Hub:
    def __init__(self):
        self.conns: dict[int, set[WebSocket]] = {}
        self.in_call: dict[int, int] = {}          # user_id -> call_id（响铃或通话中）
        self.ring_tasks: dict[int, asyncio.Task] = {}
        self.bill_tasks: dict[int, asyncio.Task] = {}
        self.drop_tasks: dict[int, asyncio.Task] = {}

    # ---------- 连接 ----------
    async def connect(self, uid: int, ws: WebSocket):
        self.conns.setdefault(uid, set()).add(ws)
        t = self.drop_tasks.pop(uid, None)
        if t:
            t.cancel()
        # 点开推送通知进来的：补发还在响铃的来电
        call_id = self.in_call.get(uid)
        if call_id:
            from .core import pub_user
            with SessionLocal() as db:
                call = db.get(Call, call_id)
                if call and call.status == "ringing" and call.callee_id == uid:
                    caller = db.get(User, call.caller_id)
                    await ws.send_json({"type": "call_invite", "call": call_out(call), "from": pub_user(caller, "busy")})

    async def disconnect(self, uid: int, ws: WebSocket):
        socks = self.conns.get(uid)
        if socks:
            socks.discard(ws)
            if not socks:
                self.conns.pop(uid, None)
                with SessionLocal() as db:
                    u = db.get(User, uid)
                    if u:
                        u.last_seen = datetime.now()
                        db.commit()
                # 掉线 30 秒没回来，结束正在进行的通话，避免一直扣费
                if uid in self.in_call:
                    self.drop_tasks[uid] = asyncio.create_task(self._drop_later(uid))

    async def _drop_later(self, uid):
        await asyncio.sleep(30)
        call_id = self.in_call.get(uid)
        if call_id and uid not in self.conns:
            await self.end_call(call_id, "disconnect")

    def online(self, uid: int) -> bool:
        return uid in self.conns

    def status(self, uid: int) -> str:
        if uid in self.in_call:
            return "busy"
        return "online" if uid in self.conns else "offline"

    async def send(self, uid: int, payload: dict):
        for ws in list(self.conns.get(uid, ())):
            try:
                await ws.send_json(payload)
            except Exception:
                self.conns.get(uid, set()).discard(ws)

    # ---------- 消息 ----------
    async def push_message(self, m: Message, sender: dict):
        payload = {"type": "message", "message": message_out(m), "from": sender}
        await self.send(m.to_id, payload)
        await self.send(m.from_id, payload)   # 同步到自己的其它设备
        if not self.online(m.to_id) and m.kind != "call":
            from .push import push_later
            preview = {"text": m.content[:60], "image": "[图片]", "voice": "[语音]", "gift": f"[礼物] {m.content}"}.get(m.kind, "")
            push_later(m.to_id, sender.get("name", "新消息"), preview, f"/#/chat/{m.from_id}", "message", f"chat-{m.from_id}")

    # ---------- 通话 ----------
    async def ring(self, call: Call, caller: dict):
        self.in_call[call.caller_id] = call.id
        self.in_call[call.callee_id] = call.id
        await self.send(call.callee_id, {"type": "call_invite", "call": call_out(call), "from": caller})
        self.ring_tasks[call.id] = asyncio.create_task(self._ring_timeout(call.id))
        if not self.online(call.callee_id):
            from .push import push_later
            what = "视频" if call.media == "video" else "语音"
            push_later(call.callee_id, f"{caller.get('name', '')} 邀请你{what}通话", "点击接听", "/#/call", "call", f"call-{call.id}")

    async def _ring_timeout(self, call_id):
        await asyncio.sleep(config.RING_TIMEOUT)
        self.ring_tasks.pop(call_id, None)
        await self.end_call(call_id, "missed")

    async def accept(self, call_id: int):
        t = self.ring_tasks.pop(call_id, None)
        if t:
            t.cancel()
        with SessionLocal() as db:
            call = db.get(Call, call_id)
            call.status = "active"
            call.answered_at = datetime.now()
            db.commit()
            out = call_out(call)
        if not await self._charge(call_id):
            return False
        self.bill_tasks[call_id] = asyncio.create_task(self._bill_loop(call_id))
        for uid in (out["callerId"], out["calleeId"]):
            await self.send(uid, {"type": "call_accepted", "call": out})
        return True

    async def _bill_loop(self, call_id):
        try:
            while True:
                await asyncio.sleep(config.BILL_INTERVAL)
                if not await self._charge(call_id):
                    return
        except asyncio.CancelledError:
            pass

    async def _charge(self, call_id) -> bool:
        """预扣下一分钟。余额不够就结束通话。"""
        with SessionLocal() as db:
            call = db.get(Call, call_id)
            if call.status != "active":
                return False
            if not call.payer_id or call.price <= 0:
                return True
            payer = db.get(User, call.payer_id)
            payee = db.get(User, call.callee_id if call.payer_id == call.caller_id else call.caller_id)
            try:
                change(db, payer, -call.price, f"{'视频' if call.media == 'video' else '语音'}通话·{payee.name}")
            except NotEnoughCoins:
                db.rollback()
                ok = False
            else:
                change(db, payee, int(call.price * config.HOST_SHARE), f"通话收益·{payer.name}", "earnings")
                call.cost += call.price
                db.commit()
                ok = True
                coins, payer_id = payer.coins, payer.id
        if not ok:
            await self.end_call(call_id, "no_coins")
            return False
        await self.send(payer_id, {"type": "balance", "coins": coins})
        # 余额只够最后一分钟时提醒
        if coins < call.price:
            await self.send(payer_id, {"type": "call_low_balance", "callId": call_id})
        return True

    async def end_call(self, call_id: int, reason: str, by: int | None = None):
        for tasks in (self.ring_tasks, self.bill_tasks):
            t = tasks.pop(call_id, None)
            if t and t is not asyncio.current_task():
                t.cancel()
        with SessionLocal() as db:
            call = db.get(Call, call_id)
            if not call or call.status in ("ended", "rejected", "canceled", "missed"):
                return None
            now = datetime.now()
            if call.status == "active":
                call.status = "ended"
                call.seconds = int((now - call.answered_at).total_seconds())
            else:
                call.status = {"reject": "rejected", "cancel": "canceled"}.get(reason, "missed")
            call.end_reason = reason
            call.ended_at = now
            text = f"{'视频' if call.media == 'video' else '语音'}通话 {mmss(call.seconds)}" if call.status == "ended" else \
                {"rejected": "对方已拒绝", "canceled": "已取消", "missed": "未接通"}[call.status]
            m = Message(from_id=call.caller_id, to_id=call.callee_id, kind="call", content=text,
                        extra={"callId": call.id, "media": call.media, "status": call.status}, read=False)
            db.add(m)
            db.commit()
            out = call_out(call)
            names = {u.id: u.name for u in db.query(User).filter(User.id.in_([call.caller_id, call.callee_id]))}
        for uid in (out["callerId"], out["calleeId"]):
            if self.in_call.get(uid) == call_id:
                self.in_call.pop(uid, None)
            await self.send(uid, {"type": "call_ended", "call": out, "reason": reason, "by": by})
        await self.push_message(m, {"id": out["callerId"], "name": names.get(out["callerId"], "")})
        return out

    async def relay_signal(self, uid: int, call_id: int, data: dict):
        with SessionLocal() as db:
            call = db.get(Call, call_id)
            if not call or uid not in (call.caller_id, call.callee_id) or call.status not in ("ringing", "active"):
                return
            peer = call.callee_id if uid == call.caller_id else call.caller_id
        await self.send(peer, {"type": "signal", "callId": call_id, "data": data})


def mmss(s):
    return f"{s // 60:02d}:{s % 60:02d}"


def call_out(c: Call) -> dict:
    return {"id": c.id, "callerId": c.caller_id, "calleeId": c.callee_id, "media": c.media, "price": c.price,
            "payerId": c.payer_id, "status": c.status, "seconds": c.seconds, "cost": c.cost,
            "endReason": c.end_reason, "time": fmt_time(c.created_at)}


def message_out(m: Message) -> dict:
    return {"id": m.id, "from": m.from_id, "to": m.to_id, "kind": m.kind, "content": m.content,
            "extra": m.extra or {}, "read": m.read, "time": fmt_time(m.created_at),
            "ts": int(m.created_at.timestamp() * 1000)}


hub = Hub()
