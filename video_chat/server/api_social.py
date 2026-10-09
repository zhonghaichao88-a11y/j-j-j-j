"""发现页、排行榜、用户主页、关注、拉黑、举报、访客、动态、小视频、评论、点赞。"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from .core import BANNERS, GIFT_BY_ID, current_user, fmt_time, get_db, is_blocked, pub_user, settings_of
from .db import Block, Call, Comment, Follow, GiftRecord, Post, PostLike, Report, User, Visit
from .hub import hub

router = APIRouter(prefix="/api")


def _blocked_ids(db, uid):
    a = db.scalars(select(Block.blocked_id).where(Block.user_id == uid)).all()
    b = db.scalars(select(Block.user_id).where(Block.blocked_id == uid)).all()
    return set(a) | set(b)


def _following(db, uid):
    return set(db.scalars(select(Follow.followee_id).where(Follow.follower_id == uid)).all())


# ================= 发现 =================
@router.get("/banners")
def banners():
    return BANNERS


@router.get("/hosts")
def hosts(tab: str = "recommend", chip: str = "all", age: str = "", price: str = "", online: bool = False,
          sameCity: bool = False, offset: int = 0, limit: int = Query(40, le=100),
          me: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(User).where(User.is_host.is_(True), User.banned.is_(False), User.id != me.id)
    blocked = _blocked_ids(db, me.id)
    if blocked:
        q = q.where(User.id.not_in(list(blocked)))
    if chip == "new":
        q = q.where(User.created_at >= datetime.now() - timedelta(days=7))
    if chip == "verified":
        q = q.where(User.verify_status == "approved")
    if chip == "city" or sameCity or tab == "nearby":
        q = q.where(User.city == me.city)
    if chip == "close":
        ids = _following(db, me.id)
        q = q.where(User.id.in_(list(ids) or [-1]))
    rng = {"18-22": (18, 22), "23-27": (23, 27), "28以上": (28, 200)}.get(age)
    if rng:
        q = q.where(User.age.between(*rng))
    rng = {"30以下": (0, 30), "30-60": (30, 60), "60以上": (60, 100000)}.get(price)
    if rng:
        q = q.where(User.price.between(*rng))
    users = db.scalars(q.order_by(User.rating_sum.desc(), User.id.desc()).limit(500)).all()
    if tab == "nearby":
        users = [u for u in users if not settings_of(u)["hideNearby"]]
    rows = [pub_user(u, hub.status(u.id)) for u in users]
    if online or chip == "online":
        rows = [r for r in rows if r["status"] == "online"]
    order = {"online": 0, "busy": 1, "offline": 2}
    if tab in ("recommend", "active"):
        rows.sort(key=lambda r: (order[r["status"]], -r["rating"]))
    elif tab == "square":
        rows.sort(key=lambda r: -r["id"])  # 广场：最新入驻
    return rows[offset: offset + limit]


@router.get("/rank")
def rank(kind: str = "charm", period: str = "day", me: User = Depends(current_user), db: Session = Depends(get_db)):
    since = datetime.now() - {"day": timedelta(days=1), "week": timedelta(days=7), "month": timedelta(days=30)}.get(period, timedelta(days=1))
    totals: dict[int, int] = {}
    who_g = GiftRecord.to_id if kind == "charm" else GiftRecord.from_id
    for uid, s in db.execute(select(who_g, func.sum(GiftRecord.price)).where(GiftRecord.created_at >= since).group_by(who_g)):
        totals[uid] = totals.get(uid, 0) + int(s or 0)
    # 通话：魅力榜算收款方，富豪榜算付款方
    for c in db.scalars(select(Call).where(Call.created_at >= since, Call.cost > 0, Call.payer_id.is_not(None))):
        uid = (c.callee_id if c.payer_id == c.caller_id else c.caller_id) if kind == "charm" else c.payer_id
        totals[uid] = totals.get(uid, 0) + c.cost
    top = sorted(totals.items(), key=lambda kv: -kv[1])[:50]
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_([k for k, _ in top] or [-1])))}
    return [{**pub_user(users[uid], hub.status(uid)), "score": score} for uid, score in top if uid in users and not users[uid].banned]


# ================= 用户主页 =================
@router.get("/users/{uid}")
def user_page(uid: int, me: User = Depends(current_user), db: Session = Depends(get_db)):
    u = db.get(User, uid)
    if not u or u.banned:
        raise HTTPException(404, "用户不存在")
    if uid != me.id and not (settings_of(me)["stealth"] and me.is_vip):
        db.add(Visit(visitor_id=me.id, owner_id=uid))
        db.commit()
    gifts = db.execute(select(GiftRecord.gift_id, func.count()).where(GiftRecord.to_id == uid).group_by(GiftRecord.gift_id)).all()
    posts = db.scalars(select(Post).where(Post.user_id == uid, Post.deleted.is_(False), Post.visibility == "all")
                       .order_by(Post.id.desc()).limit(8)).all()
    thumbs = [img for p in posts for img in p.images][:8]
    calls = db.scalar(select(func.count()).where(Call.callee_id == uid))
    answered = db.scalar(select(func.count()).where(Call.callee_id == uid, Call.answered_at.is_not(None)))
    return {
        **pub_user(u, hub.status(uid)),
        "fans": db.scalar(select(func.count()).where(Follow.followee_id == uid)),
        "followed": db.scalar(select(Follow.id).where(Follow.follower_id == me.id, Follow.followee_id == uid)) is not None,
        "blocked": db.scalar(select(Block.id).where(Block.user_id == me.id, Block.blocked_id == uid)) is not None,
        "answerRate": round(answered * 100 / calls) if calls else 100,
        "gifts": [{**GIFT_BY_ID[g], "count": n} for g, n in gifts if g in GIFT_BY_ID],
        "thumbs": thumbs,
        "sameCity": u.city == me.city,
    }


@router.post("/users/{uid}/follow")
async def follow(uid: int, me: User = Depends(current_user), db: Session = Depends(get_db)):
    if uid == me.id or not db.get(User, uid):
        raise HTTPException(400, "不能关注")
    f = db.scalar(select(Follow).where(Follow.follower_id == me.id, Follow.followee_id == uid))
    if f:
        db.delete(f)
    else:
        db.add(Follow(follower_id=me.id, followee_id=uid))
    db.commit()
    if not f:
        await hub.send(uid, {"type": "notice", "text": f"{me.name} 关注了你"})
    return {"followed": not f}


@router.post("/users/{uid}/block")
def block(uid: int, me: User = Depends(current_user), db: Session = Depends(get_db)):
    if uid == me.id:
        raise HTTPException(400)
    b = db.scalar(select(Block).where(Block.user_id == me.id, Block.blocked_id == uid))
    if b:
        db.delete(b)
    else:
        db.add(Block(user_id=me.id, blocked_id=uid))
        db.execute(delete(Follow).where(((Follow.follower_id == me.id) & (Follow.followee_id == uid)) |
                                        ((Follow.follower_id == uid) & (Follow.followee_id == me.id))))
    db.commit()
    return {"blocked": not b}


@router.get("/me/blocks")
def my_blocks(me: User = Depends(current_user), db: Session = Depends(get_db)):
    ids = db.scalars(select(Block.blocked_id).where(Block.user_id == me.id)).all()
    return [pub_user(u) for u in db.scalars(select(User).where(User.id.in_(ids or [-1])))]


class ReportIn(BaseModel):
    type: str = Field(pattern="^(user|post|message)$")
    id: int
    reason: str = Field(min_length=1, max_length=50)


@router.post("/reports")
def report(body: ReportIn, me: User = Depends(current_user), db: Session = Depends(get_db)):
    db.add(Report(reporter_id=me.id, target_type=body.type, target_id=body.id, reason=body.reason))
    db.commit()
    return {"ok": True}


@router.get("/me/follows")
def my_follows(kind: str = "following", me: User = Depends(current_user), db: Session = Depends(get_db)):
    if kind == "following":
        ids = db.scalars(select(Follow.followee_id).where(Follow.follower_id == me.id).order_by(Follow.id.desc())).all()
    else:
        ids = db.scalars(select(Follow.follower_id).where(Follow.followee_id == me.id).order_by(Follow.id.desc())).all()
    mine = _following(db, me.id)
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(ids or [-1])))}
    return [{**pub_user(users[i], hub.status(i)), "followed": i in mine} for i in ids if i in users]


@router.get("/me/counts")
def my_counts(me: User = Depends(current_user), db: Session = Depends(get_db)):
    return {"following": db.scalar(select(func.count()).where(Follow.follower_id == me.id)),
            "fans": db.scalar(select(func.count()).where(Follow.followee_id == me.id))}


@router.get("/me/visitors")
def visitors(me: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Visit.visitor_id, func.max(Visit.created_at)).where(Visit.owner_id == me.id)
                      .group_by(Visit.visitor_id).order_by(func.max(Visit.created_at).desc()).limit(50)).all()
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_([r[0] for r in rows] or [-1])))}
    out = []
    for i, (vid, t) in enumerate(rows):
        if vid not in users:
            continue
        locked = not me.is_vip and i >= 3
        out.append({"locked": locked, "time": fmt_time(t), **({} if locked else pub_user(users[vid], hub.status(vid)))})
    return out


# ================= 动态 / 小视频 =================
def _post_out(p: Post, u: User, liked: bool):
    return {"id": p.id, "kind": p.kind, "text": p.text, "images": p.images, "video": p.video, "location": p.location,
            "likes": p.like_count, "comments": p.comment_count, "liked": liked, "time": fmt_time(p.created_at),
            "user": pub_user(u, hub.status(u.id)), "mine": False}


@router.get("/posts")
def posts(kind: str = "post", tab: str = "feed", verified: bool = False, userId: int | None = None,
          offset: int = 0, limit: int = Query(20, le=50), me: User = Depends(current_user), db: Session = Depends(get_db)):
    following = _following(db, me.id)
    q = select(Post, User).join(User, User.id == Post.user_id).where(Post.deleted.is_(False), Post.kind == kind, User.banned.is_(False))
    q = q.where(or_(Post.visibility == "all", Post.user_id == me.id,
                    (Post.visibility == "fans") & Post.user_id.in_(list(following) or [-1])))
    blocked = _blocked_ids(db, me.id)
    if blocked:
        q = q.where(Post.user_id.not_in(list(blocked)))
    if userId:
        q = q.where(Post.user_id == userId)
    if tab == "city":
        q = q.where(User.city == me.city)
    if tab == "follow":
        q = q.where(Post.user_id.in_(list(following) or [-1]))
    if verified:
        q = q.where(User.verify_status == "approved")
    rows = db.execute(q.order_by(Post.id.desc()).offset(offset).limit(limit)).all()
    liked = set(db.scalars(select(PostLike.post_id).where(PostLike.user_id == me.id,
                                                          PostLike.post_id.in_([p.id for p, _ in rows] or [-1]))).all())
    out = []
    for p, u in rows:
        d = _post_out(p, u, p.id in liked)
        d["mine"] = u.id == me.id
        out.append(d)
    return out


class PostIn(BaseModel):
    kind: str = Field("post", pattern="^(post|reel)$")
    text: str = Field("", max_length=500)
    images: list[str] = Field(default_factory=list, max_length=9)
    video: str = ""
    location: str = Field("", max_length=20)
    visibility: str = Field("all", pattern="^(all|fans|self)$")


@router.post("/posts")
def create_post(body: PostIn, me: User = Depends(current_user), db: Session = Depends(get_db)):
    if any(not i.startswith("/uploads/image/") for i in body.images):
        raise HTTPException(400, "图片地址无效")
    if body.kind == "reel" and not body.video.startswith("/uploads/video/"):
        raise HTTPException(400, "请先上传视频")
    if body.kind == "post" and not body.text.strip() and not body.images:
        raise HTTPException(400, "写点什么或者选张图片吧")
    p = Post(user_id=me.id, **body.model_dump())
    db.add(p)
    db.commit()
    return _post_out(p, me, False) | {"mine": True}


@router.delete("/posts/{pid}")
def delete_post(pid: int, me: User = Depends(current_user), db: Session = Depends(get_db)):
    p = db.get(Post, pid)
    if not p or p.user_id != me.id:
        raise HTTPException(404)
    p.deleted = True
    db.commit()
    return {"ok": True}


@router.post("/posts/{pid}/like")
def like(pid: int, me: User = Depends(current_user), db: Session = Depends(get_db)):
    p = db.get(Post, pid)
    if not p or p.deleted:
        raise HTTPException(404)
    lk = db.scalar(select(PostLike).where(PostLike.post_id == pid, PostLike.user_id == me.id))
    if lk:
        db.delete(lk)
        p.like_count = max(0, p.like_count - 1)
    else:
        db.add(PostLike(post_id=pid, user_id=me.id))
        p.like_count += 1
    db.commit()
    return {"liked": not lk, "likes": p.like_count}


@router.get("/posts/{pid}/comments")
def comments(pid: int, me: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Comment, User).join(User, User.id == Comment.user_id)
                      .where(Comment.post_id == pid).order_by(Comment.id)).all()
    return [{"id": c.id, "text": c.text, "time": fmt_time(c.created_at), "user": {"id": u.id, "name": u.name}} for c, u in rows]


class CommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=200)


@router.post("/posts/{pid}/comments")
async def add_comment(pid: int, body: CommentIn, me: User = Depends(current_user), db: Session = Depends(get_db)):
    p = db.get(Post, pid)
    if not p or p.deleted:
        raise HTTPException(404)
    if is_blocked(db, me.id, p.user_id):
        raise HTTPException(403, "无法评论")
    db.add(Comment(post_id=pid, user_id=me.id, text=body.text.strip()))
    p.comment_count += 1
    db.commit()
    if p.user_id != me.id:
        await hub.send(p.user_id, {"type": "notice", "text": f"{me.name} 评论了你的动态"})
    return comments(pid, me, db)
