"""开发用：生成几个「测试主播」账号，让发现页有内容可看。上线前不要运行，并删除 is_test=True 的账号。

  python -m server.seed            # 生成
  python -m server.seed --clear    # 删除所有测试账号
"""
import sys

from sqlalchemy import delete, select

from .api_account import _new_invite_code
from .db import SessionLocal, User, init_db

HOSTS = [("测试主播·小雨", 23, "长沙市", 30), ("测试主播·九月", 25, "杭州市", 25), ("测试主播·苏苏", 22, "成都市", 40),
         ("测试主播·Lily", 27, "长沙市", 50), ("测试主播·安然", 24, "广州市", 20), ("测试主播·糖糖", 21, "武汉市", 35)]


def main():
    init_db()
    with SessionLocal() as db:
        if "--clear" in sys.argv:
            n = db.execute(delete(User).where(User.is_test.is_(True))).rowcount
            db.commit()
            print(f"已删除 {n} 个测试账号")
            return
        for i, (name, age, city, price) in enumerate(HOSTS):
            phone = f"1990000{i:04d}"
            if db.scalar(select(User.id).where(User.phone == phone)):
                continue
            db.add(User(phone=phone, name=name, sex="f", age=age, city=city, sign="这是测试账号，用于开发调试",
                        is_host=True, price=price, voice_price=price // 2, verify_status="approved",
                        invite_code=_new_invite_code(db), is_test=True, settings={}))
        db.commit()
        print("测试主播已生成（手机号 19900000000 起，开发模式下验证码会直接显示，可以登录这些账号来接听）")


if __name__ == "__main__":
    main()
