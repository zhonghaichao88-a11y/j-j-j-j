"""新功能测试：实名、陪玩全流程、管理后台、离线推送与来电补发。"""
import json
from datetime import datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from server import push
from server.db import GameOrder, SessionLocal

from .conftest import ADMIN, login, recv_until, valid_id


@pytest.fixture(scope="module")
def p(c):
    """P1 买家（有钱），P2 陪玩师 / 主播。"""
    h1, u1, t1 = login(c, "13700000001")
    h2, u2, t2 = login(c, "13700000002")
    o = c.post("/api/orders", headers=h1, json={"kind": "coins", "pack": 3, "channel": "mock"}).json()
    c.post(f"/api/orders/{o['orderId']}/mock-pay", headers=h1)
    return {"h1": h1, "u1": u1, "t1": t1, "h2": h2, "u2": u2, "t2": t2}


def test_realname_rules(c, p):
    h = login(c, "13700000009")[0]
    bad = [("张三", "110101199003078514", "校验位"), ("张三", "11010119900307851", None),
           ("张三", valid_id("11010120990101123"), "出生日期"), ("张三", valid_id("11010120150101123"), "18 周岁")]
    for name, idno, word in bad:
        r = c.post("/api/realname", headers=h, json={"name": name, "idNo": idno})
        assert r.status_code in (400, 403, 422), idno
        if word:
            assert word in json.dumps(r.json(), ensure_ascii=False)
    good = valid_id("44030619880808123")
    me = c.post("/api/realname", headers=h, json={"name": "张三", "idNo": good}).json()
    assert me["realname"] and me["realName"] == "张*" and me["idMasked"] == good[:3] + "*" * 11 + good[-4:]
    assert c.post("/api/realname", headers=h, json={"name": "张三", "idNo": good}).status_code == 400
    # 同一身份证不能认证第二个账号
    h2 = login(c, "13700000008")[0]
    assert "其它账号" in c.post("/api/realname", headers=h2, json={"name": "张三", "idNo": good}).json()["detail"]


def test_game_flow(c, p):
    h1, h2, u1, u2 = p["h1"], p["h2"], p["u1"], p["u2"]
    skill = {"game": "王者荣耀", "rank": "王者 50 星", "price": 20, "unit": "局", "intro": "国服打野"}
    r = c.post("/api/skills", headers=h2, json=skill)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "realname"
    c.post("/api/realname", headers=h2, json={"name": "李四", "idNo": valid_id("31010119960505123")})
    s = c.post("/api/skills", headers=h2, json=skill).json()
    assert c.post("/api/skills", headers=h2, json=skill).status_code == 400          # 同一游戏不能重复加
    assert c.post("/api/skills", headers=h2, json={**skill, "game": "不存在的游戏"}).status_code == 400
    s = c.put(f"/api/skills/{s['id']}", headers=h2, json={**skill, "price": 25}).json()
    assert s["price"] == 25
    assert c.get(f"/api/users/{u2['id']}/skills", headers=h1).json()[0]["id"] == s["id"]
    assert any(x["id"] == s["id"] for x in c.get("/api/skills?game=王者荣耀", headers=h1).json())

    coins0 = c.get("/api/me", headers=h1).json()["coins"]
    assert c.post("/api/game-orders", headers=h2, json={"skillId": s["id"], "qty": 1}).status_code == 400   # 不能自己下单
    o = c.post("/api/game-orders", headers=h1, json={"skillId": s["id"], "qty": 2, "note": "晚上 9 点"}).json()
    assert o["total"] == 50 and o["status"] == "pending"
    assert c.get("/api/me", headers=h1).json()["coins"] == coins0 - 50              # 托管扣款
    assert c.post(f"/api/game-orders/{o['id']}/confirm", headers=h1).status_code == 409   # 还没接单不能确认
    assert c.post(f"/api/game-orders/{o['id']}/accept", headers=h1).status_code == 409    # 买家不能接单
    c.post(f"/api/game-orders/{o['id']}/accept", headers=h2)
    assert c.post(f"/api/game-orders/{o['id']}/deliver", headers=h2).json()["status"] == "delivered"
    earn0 = c.get("/api/me", headers=h2).json()["earnings"]
    assert c.post(f"/api/game-orders/{o['id']}/confirm", headers=h1).json()["status"] == "completed"
    assert c.get("/api/me", headers=h2).json()["earnings"] == earn0 + 40            # 80% 分成
    assert c.post(f"/api/game-orders/{o['id']}/review", headers=h1, json={"stars": 4, "review": "带飞"}).json()["stars"] == 4
    assert c.get(f"/api/users/{u2['id']}/skills", headers=h1).json()[0]["rating"] == 4.0

    # 买家取消待接单的订单 -> 全额退款
    o2 = c.post("/api/game-orders", headers=h1, json={"skillId": s["id"], "qty": 1}).json()
    assert c.post(f"/api/game-orders/{o2['id']}/cancel", headers=h1).json()["status"] == "canceled"
    # 陪玩师拒单 -> 退款
    o3 = c.post("/api/game-orders", headers=h1, json={"skillId": s["id"], "qty": 1}).json()
    assert c.post(f"/api/game-orders/{o3['id']}/reject", headers=h2).json()["status"] == "canceled"
    assert c.get("/api/me", headers=h1).json()["coins"] == coins0 - 50

    # 申诉 -> 管理员裁决退款
    o4 = c.post("/api/game-orders", headers=h1, json={"skillId": s["id"], "qty": 1}).json()
    c.post(f"/api/game-orders/{o4['id']}/accept", headers=h2)
    assert c.post(f"/api/game-orders/{o4['id']}/refund", headers=h1, json={"reason": ""}).status_code == 400
    assert c.post(f"/api/game-orders/{o4['id']}/refund", headers=h1, json={"reason": "没上线"}).json()["status"] == "disputed"
    d = c.get("/api/admin/disputes", headers=ADMIN).json()
    assert d[0]["id"] == o4["id"]
    c.post(f"/api/admin/disputes/{o4['id']}?refund=true", headers=ADMIN)
    assert c.get("/api/me", headers=h1).json()["coins"] == coins0 - 50

    # 超时：待接单超时自动退款；已完成未确认到时间自动确认
    o5 = c.post("/api/game-orders", headers=h1, json={"skillId": s["id"], "qty": 1}).json()
    o6 = c.post("/api/game-orders", headers=h1, json={"skillId": s["id"], "qty": 1}).json()
    c.post(f"/api/game-orders/{o6['id']}/accept", headers=h2)
    c.post(f"/api/game-orders/{o6['id']}/deliver", headers=h2)
    with SessionLocal() as db:
        db.get(GameOrder, o5["id"]).created_at = datetime.now() - timedelta(hours=2)
        db.get(GameOrder, o6["id"]).delivered_at = datetime.now() - timedelta(days=2)
        db.commit()
    orders = {x["id"]: x for x in c.get("/api/game-orders?role=buyer", headers=h1).json()}
    assert orders[o5["id"]]["status"] == "canceled" and orders[o6["id"]]["status"] == "completed"
    assert [x["role"] for x in c.get("/api/game-orders?role=seller", headers=h2).json()][0] == "seller"
    # 删除技能后不能再下单
    c.delete(f"/api/skills/{s['id']}", headers=h2)
    assert c.post("/api/game-orders", headers=h1, json={"skillId": s["id"], "qty": 1}).status_code == 404


def test_admin(c, p):
    h1, u1, u2 = p["h1"], p["u1"], p["u2"]
    st = c.get("/api/admin/stats", headers=ADMIN).json()
    assert st["users"] >= 2 and st["revenueTotal"] > 0 and "pending" in st
    assert c.get("/api/admin/users?q=13700000001", headers=ADMIN).json()[0]["id"] == u1["id"]
    assert c.get(f"/api/admin/users?q={u1['id']}", headers=ADMIN).json()[0]["id"] == u1["id"]
    before = c.get("/api/me", headers=h1).json()["coins"]
    assert c.post(f"/api/admin/users/{u1['id']}/coins", headers=ADMIN, json={"amount": 15, "reason": "补偿"}).json()["coins"] == before + 15
    # 客服：用户提问 -> 待回复 -> 人工回复后不再待回复
    c.post("/api/notices/service", headers=h1, json={"text": "我的订单有问题"})
    inbox = c.get("/api/admin/service", headers=ADMIN).json()
    assert any(x["user"]["id"] == u1["id"] and x["waiting"] for x in inbox)
    c.post(f"/api/admin/service/{u1['id']}", headers=ADMIN, json={"text": "您好，已为您处理"})
    assert not any(x["user"]["id"] == u1["id"] and x["waiting"] for x in c.get("/api/admin/service", headers=ADMIN).json())
    assert c.get(f"/api/admin/service/{u1['id']}", headers=ADMIN).json()[-1]["text"] == "您好，已为您处理"
    # 举报 -> 处理并删除动态
    post = c.post("/api/posts", headers=p["h2"], json={"text": "广告内容"}).json()
    c.post("/api/reports", headers=h1, json={"type": "post", "id": post["id"], "reason": "广告骚扰"})
    rep = [r for r in c.get("/api/admin/reports", headers=ADMIN).json() if r["targetId"] == post["id"]][0]
    assert rep["content"] == "广告内容" and rep["target"]["id"] == u2["id"]
    c.post(f"/api/admin/reports/{rep['id']}/close?delete_post=true", headers=ADMIN)
    assert post["id"] not in [x["id"] for x in c.get("/api/posts", headers=h1).json()]
    # 提现：驳回退回收益；打款
    h2 = p["h2"]
    c.post("/api/gifts/send", headers=h1, json={"to": u2["id"], "giftId": 6})   # 送钻戒，陪玩师得 99 收益
    earn = c.get("/api/me", headers=h2).json()["earnings"]
    assert earn >= 100
    c.post("/api/withdraw", headers=h2, json={"coins": 100, "account": "支付宝 abc"})
    w = c.get("/api/admin/withdrawals", headers=ADMIN).json()[0]
    assert "李四" in w["account"]          # 自动带上实名
    c.post(f"/api/admin/withdrawals/{w['id']}/reject", headers=ADMIN, json={"note": "账号不对"})
    assert c.get("/api/me", headers=h2).json()["earnings"] == earn
    c.post("/api/withdraw", headers=h2, json={"coins": 100, "account": "支付宝 abc"})
    w = c.get("/api/admin/withdrawals", headers=ADMIN).json()[0]
    c.post(f"/api/admin/withdrawals/{w['id']}/paid", headers=ADMIN, json={"note": ""})
    assert c.get("/api/admin/withdrawals?status=paid", headers=ADMIN).json()[0]["id"] == w["id"]
    # 封号：之后接口都 403
    h3, u3, _ = login(c, "13700000007")
    c.post(f"/api/admin/users/{u3['id']}/ban", headers=ADMIN)
    assert c.get("/api/me", headers=h3).status_code == 403
    c.post(f"/api/admin/users/{u3['id']}/ban?banned=false", headers=ADMIN)
    assert c.get("/api/me", headers=h3).status_code == 200


def _ua_keys():
    k = ec.generate_private_key(ec.SECP256R1())
    pub = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return k, pub


def webpush_decrypt(body: bytes, ua_key, ua_pub: bytes, auth: bytes) -> bytes:
    """浏览器那一侧的解密（按 RFC 8291），用来验证我们的加密是对的。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, idlen = body[:16], body[20]
    as_pub = body[21:21 + idlen]
    secret = ua_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    ikm = push._hkdf(auth, secret, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    cek = push._hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = push._hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    plain = AESGCM(cek).decrypt(nonce, body[21 + idlen:], None)
    assert plain.endswith(b"\x02")
    return plain[:-1]


def test_webpush_crypto():
    ua_key, ua_pub = _ua_keys()
    auth = b"0123456789abcdef"
    body = push.webpush_encrypt('{"title":"你好"}'.encode(), push.b64u(ua_pub), push.b64u(auth))
    assert int.from_bytes(body[16:20], "big") == 4096 and body[20] == 65
    assert webpush_decrypt(body, ua_key, ua_pub, auth) == '{"title":"你好"}'.encode()
    # VAPID JWT 能被公钥验证
    hdr = push.vapid_header("https://fcm.googleapis.com/fcm/send/abc")
    token = hdr.split("t=")[1].split(",")[0]
    h, cl, sig = token.split(".")
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    raw = push.b64u_dec(sig)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    push.vapid_key().public_key().verify(der, f"{h}.{cl}".encode(), ec.ECDSA(hashes.SHA256()))
    assert json.loads(push.b64u_dec(cl))["aud"] == "https://fcm.googleapis.com"


def test_offline_call_push_and_redelivery(c, p, monkeypatch):
    sent = []

    async def fake_webpush(sub, data, client):
        sent.append((sub["endpoint"], data))
        return True
    monkeypatch.setattr(push, "send_webpush", fake_webpush)

    h1, h2, t2, u1, u2 = p["h1"], p["h2"], p["t2"], p["u1"], p["u2"]
    # 让 P2 成为主播
    v = c.post("/api/upload", headers=h2, data={"kind": "video"}, files={"file": ("v.mp4", b"x")}).json()["url"]
    c.post("/api/verify", headers=h2, json={"video": v})
    c.post(f"/api/admin/verify/{u2['id']}?approve=true", headers=ADMIN)
    c.put("/api/me/host", headers=h2, json={"price": 10})
    assert c.post("/api/calls", headers=h1, json={"to": u2["id"]}).json()["detail"]["code"] == "offline"
    # 注册网页推送设备
    _, ua_pub = _ua_keys()
    sub = {"endpoint": "https://push.example.com/abc", "keys": {"p256dh": push.b64u(ua_pub), "auth": push.b64u(b"0123456789abcdef")}}
    assert c.post("/api/devices", headers=h2, json={"provider": "webpush", "token": "this is not a json subscription"}).status_code == 400
    assert c.post("/api/devices", headers=h2, json={"provider": "webpush", "token": json.dumps(sub)}).json()["enabled"]
    # 对方离线但有推送设备：可以打，会推送来电
    call = c.post("/api/calls", headers=h1, json={"to": u2["id"]}).json()["call"]
    assert call["status"] == "ringing"
    # 离线消息也会推送
    c.post("/api/messages", headers=h1, json={"to": u2["id"], "kind": "text", "content": "在吗"})
    with c.websocket_connect(f"/api/ws?token={t2}") as ws:
        assert ws.receive_json()["type"] == "call_invite"     # 上线后补发还在响铃的来电
        assert ws.receive_json()["type"] == "hello"
        c.post(f"/api/calls/{call['id']}/reject", headers=h2)
        recv_until(ws, "call_ended")
    kinds = [d["type"] for _, d in sent]
    assert "call" in kinds and "message" in kinds
    assert any("邀请你视频通话" in d["title"] for _, d in sent)
    # 关闭「新消息通知」后只推来电，不推消息
    sent.clear()
    c.put("/api/me/settings", headers=h2, json={"notify": False})
    c.post("/api/messages", headers=h1, json={"to": u2["id"], "kind": "text", "content": "还在吗"})
    import time
    time.sleep(0.3)
    assert not sent
    c.post("/api/devices/remove", headers=h2, json={"token": json.dumps(sub)})
    assert c.post("/api/calls", headers=h1, json={"to": u2["id"]}).json()["detail"]["code"] == "offline"


def test_config_exposes_push_and_pay(c, p):
    cfg = c.get("/api/config").json()
    assert len(push.b64u_dec(cfg["vapidPublicKey"])) == 65 and "webpush" in cfg["push"]
    assert cfg["pay"] == {"alipay": False, "wechat": False, "mock": True}
    r = c.post("/api/orders", headers=p["h1"], json={"kind": "coins", "pack": 0, "channel": "alipay"})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "pay_not_configured"
