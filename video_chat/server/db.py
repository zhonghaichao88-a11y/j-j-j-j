"""数据库表。SQLite 开发，上线把 SEEU_DATABASE_URL 换成 PostgreSQL 即可。"""
from datetime import date, datetime

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint, create_engine, event)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from . import config

engine = create_engine(config.DATABASE_URL, connect_args={"check_same_thread": False}
                       if config.DATABASE_URL.startswith("sqlite") else {})
if config.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(conn, _):
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def now():
    return datetime.now()


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(20))
    sex: Mapped[str] = mapped_column(String(1), default="m")       # m / f
    age: Mapped[int] = mapped_column(Integer, default=25)
    city: Mapped[str] = mapped_column(String(20), default="")
    sign: Mapped[str] = mapped_column(String(60), default="")
    avatar: Mapped[str] = mapped_column(String(255), default="")
    photos: Mapped[list] = mapped_column(JSON, default=list)
    labels: Mapped[list] = mapped_column(JSON, default=list)
    coins: Mapped[int] = mapped_column(Integer, default=0)          # 可消费金币
    earnings: Mapped[int] = mapped_column(Integer, default=0)       # 主播收益（金币），可提现
    is_host: Mapped[bool] = mapped_column(Boolean, default=False)   # 认证主播才出现在发现页
    price: Mapped[int] = mapped_column(Integer, default=0)          # 视频通话 金币/分钟
    voice_price: Mapped[int] = mapped_column(Integer, default=0)    # 语音通话 金币/分钟
    rating_sum: Mapped[int] = mapped_column(Integer, default=0)
    rating_count: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[int] = mapped_column(Integer, default=1)
    vip_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    verify_status: Mapped[str] = mapped_column(String(10), default="none")  # none/pending/approved/rejected
    verify_video: Mapped[str] = mapped_column(String(255), default="")
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    invite_code: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    invited_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    last_sign: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_share: Mapped[date | None] = mapped_column(Date, nullable=True)
    profile_rewarded: Mapped[bool] = mapped_column(Boolean, default=False)
    first_paid: Mapped[bool] = mapped_column(Boolean, default=False)
    banned: Mapped[bool] = mapped_column(Boolean, default=False)
    # 实名（提现、开通接听、陪玩接单前必须完成）
    real_name: Mapped[str] = mapped_column(String(20), default="")
    id_masked: Mapped[str] = mapped_column(String(20), default="")
    id_hash: Mapped[str] = mapped_column(String(64), default="", index=True)   # 防止一证多号
    realname_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)   # 测试账号（seed 生成），上线前删除
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=now)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    @property
    def rating(self):
        return round(self.rating_sum / self.rating_count, 1) if self.rating_count else 5.0

    @property
    def is_vip(self):
        return bool(self.vip_until and self.vip_until >= date.today())


class SmsCode(Base):
    __tablename__ = "sms_codes"
    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    code: Mapped[str] = mapped_column(String(6))
    expires: Mapped[datetime] = mapped_column(DateTime)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class Follow(Base):
    __tablename__ = "follows"
    __table_args__ = (UniqueConstraint("follower_id", "followee_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    follower_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    followee_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Block(Base):
    __tablename__ = "blocks"
    __table_args__ = (UniqueConstraint("user_id", "blocked_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    blocked_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    target_type: Mapped[str] = mapped_column(String(10))   # user / post / message
    target_id: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(10), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Visit(Base):
    __tablename__ = "visits"
    id: Mapped[int] = mapped_column(primary_key=True)
    visitor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Post(Base):
    __tablename__ = "posts"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10), default="post")  # post / reel
    text: Mapped[str] = mapped_column(Text, default="")
    images: Mapped[list] = mapped_column(JSON, default=list)
    video: Mapped[str] = mapped_column(String(255), default="")
    location: Mapped[str] = mapped_column(String(20), default="")
    visibility: Mapped[str] = mapped_column(String(10), default="all")  # all / fans / self
    like_count: Mapped[int] = mapped_column(Integer, default=0)
    comment_count: Mapped[int] = mapped_column(Integer, default=0)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class PostLike(Base):
    __tablename__ = "post_likes"
    __table_args__ = (UniqueConstraint("post_id", "user_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))


class Comment(Base):
    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    text: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    from_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    to_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))   # text / image / voice / gift / call
    content: Mapped[str] = mapped_column(Text, default="")
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    hidden_for: Mapped[list] = mapped_column(JSON, default=list)  # 谁清空了这条（各自清空互不影响）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class Call(Base):
    __tablename__ = "calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    caller_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    callee_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    media: Mapped[str] = mapped_column(String(5))   # video / voice
    payer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)  # 付费方（为空=免费通话）
    price: Mapped[int] = mapped_column(Integer)     # 每分钟金币，发起时锁定
    status: Mapped[str] = mapped_column(String(10), default="ringing")  # ringing/active/ended/rejected/canceled/missed
    end_reason: Mapped[str] = mapped_column(String(20), default="")
    seconds: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Rating(Base):
    __tablename__ = "ratings"
    __table_args__ = (UniqueConstraint("call_id", "rater_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id"))
    rater_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    stars: Mapped[int] = mapped_column(Integer)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class GiftRecord(Base):
    __tablename__ = "gift_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    from_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    to_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    gift_id: Mapped[int] = mapped_column(Integer)
    price: Mapped[int] = mapped_column(Integer)
    call_id: Mapped[int | None] = mapped_column(ForeignKey("calls.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class Ledger(Base):
    """金币流水。coins 和 earnings 每次变动都记一条，方便对账。"""
    __tablename__ = "ledger"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    account: Mapped[str] = mapped_column(String(10), default="coins")  # coins / earnings
    amount: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(40))
    balance_after: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(5))        # coins / vip
    yuan: Mapped[float] = mapped_column(Float)
    coins: Mapped[int] = mapped_column(Integer, default=0)
    vip_days: Mapped[int] = mapped_column(Integer, default=0)
    channel: Mapped[str] = mapped_column(String(10))    # wechat / alipay / apple / mock
    status: Mapped[str] = mapped_column(String(10), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Withdrawal(Base):
    __tablename__ = "withdrawals"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    coins: Mapped[int] = mapped_column(Integer)
    yuan: Mapped[float] = mapped_column(Float)
    account: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(10), default="pending")   # pending / paid / rejected
    note: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Device(Base):
    """推送设备：网页推送订阅、安卓 FCM token、苹果 APNs token。"""
    __tablename__ = "devices"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(10))       # webpush / fcm / apns
    token: Mapped[str] = mapped_column(Text)                 # webpush 时是订阅 JSON
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class GameSkill(Base):
    """游戏陪玩技能：一个用户可以有多个游戏。"""
    __tablename__ = "game_skills"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    game: Mapped[str] = mapped_column(String(20))
    rank: Mapped[str] = mapped_column(String(20), default="")
    price: Mapped[int] = mapped_column(Integer)              # 金币 / 单位
    unit: Mapped[str] = mapped_column(String(4), default="局")  # 局 / 小时
    intro: Mapped[str] = mapped_column(String(200), default="")
    images: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    orders_done: Mapped[int] = mapped_column(Integer, default=0)
    rating_sum: Mapped[int] = mapped_column(Integer, default=0)
    rating_count: Mapped[int] = mapped_column(Integer, default=0)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class GameOrder(Base):
    """陪玩订单。下单时金币托管在平台，完成后才结算给陪玩师。

    状态：pending 待接单 → accepted 进行中 → delivered 陪玩师已完成待确认 → completed 已完成
          pending 时买家可取消、陪玩师可拒绝 → canceled（全额退款）
          accepted/delivered 时买家可申请退款 → disputed 由客服裁决 → refunded / completed
    """
    __tablename__ = "game_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("game_skills.id"))
    buyer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    game: Mapped[str] = mapped_column(String(20))
    unit: Mapped[str] = mapped_column(String(4))
    price: Mapped[int] = mapped_column(Integer)
    qty: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(10), default="pending")
    reason: Mapped[str] = mapped_column(String(200), default="")   # 取消 / 退款原因
    stars: Mapped[int] = mapped_column(Integer, default=0)
    review: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Notice(Base):
    """系统消息（充值到账、审核结果等）和客服对话。"""
    __tablename__ = "notices"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    channel: Mapped[str] = mapped_column(String(10))   # system / service
    from_user: Mapped[bool] = mapped_column(Boolean, default=False)  # 客服对话里用户自己发的
    text: Mapped[str] = mapped_column(Text)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


def init_db():
    Base.metadata.create_all(engine)
