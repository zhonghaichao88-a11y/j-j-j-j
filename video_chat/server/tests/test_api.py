"""后端全流程测试：两个真实账号走完 登录→资料→认证→开通接听→充值→聊天→送礼→视频通话扣费→评价→提现。

运行（在 video_chat 目录下）: python -m pytest server/tests -q
"""
import os
import tempfile
import time

TMP = tempfile.mkdtemp()
os.environ.update(SEEU_DATABASE_URL=f"sqlite:///{TMP}/t.db", SEEU_UPLOAD_DIR=f"{TMP}/up", SEEU_DEV="1",
                  SEEU_ADMIN_TOKEN="adm", SEEU_BILL_INTERVAL="1", SEEU_RING_TIMEOUT="2")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server.main import app  # noqa: E402


@pytest.fixture(scope="module")
def c():
    with TestClient(app) as client:
        yield client


def login(c, phone, invite=""):
    code = c.post("/api/auth/sms", json={"phone": phone}).json()["devCode"]
    r = c.post("/api/auth/login", json={"phone": phone, "code": code, "invite": invite}).json()
    return {"Authorization": f"Bearer {r['token']}"}, r["me"], r["token"]


def recv_until(ws, typ, limit=20):
    for _ in range(limit):
        m = ws.receive_json()
        if m["type"] == typ:
            return m
    raise AssertionError(f"没收到 {typ}")


@pytest.fixture(scope="module")
def users(c):
    hb, b, tb = login(c, "13800000002")                     # B：主播
    ha, a, ta = login(c, "13800000001", invite=b["inviteCode"])  # A：B 邀请来的普通用户
    return {"a": (ha, a, ta), "b": (hb, b, tb)}


def test_auth_errors(c):
    assert c.post("/api/auth/sms", json={"phone": "123"}).status_code == 400
    assert c.post("/api/auth/login", json={"phone": "13800000009", "code": "000000"}).status_code == 400
    assert c.get("/api/me").status_code == 401
    assert c.get("/api/me", headers={"Authorization": "Bearer bad.token"}).status_code == 401


def test_profile_settings_upload(c, users):
    ha, a, _ = users["a"]
    up = c.post("/api/upload", headers=ha, data={"kind": "image"}, files={"file": ("a.png", b"\x89PNG fake", "image/png")}).json()
    assert up["url"].startswith("/uploads/image/")
    assert c.get(up["url"]).status_code == 200
    assert c.post("/api/upload", headers=ha, data={"kind": "image"}, files={"file": ("a.exe", b"x")}).status_code == 400
    me = c.put("/api/me", headers=ha, json={"name": "小明", "age": 28, "city": "长沙市", "sign": "你好", "avatar": up["url"]}).json()
    assert me["name"] == "小明" and me["coins"] == 5 and me["profileRewarded"]  # 完善资料奖励 5
    assert c.put("/api/me", headers=ha, json={"age": 12}).status_code == 422
    s = c.put("/api/me/settings", headers=ha, json={"hideDistance": True, "beauty": {"smooth": 80}}).json()
    assert s["hideDistance"] and s["beauty"]["smooth"] == 80 and s["beauty"]["white"] == 40
    assert c.put("/api/me/settings", headers=ha, json={"stealth": True}).status_code == 403  # VIP 特权
    assert c.put("/api/me/settings", headers=ha, json={"hack": 1}).status_code == 400


def test_become_host(c, users):
    hb, b, _ = users["b"]
    c.put("/api/me", headers=hb, json={"name": "九月", "sex": "f", "age": 23, "city": "长沙市"})
    assert c.put("/api/me/host", headers=hb, json={"price": 30}).status_code == 403  # 未认证不能开通
    v = c.post("/api/upload", headers=hb, data={"kind": "video"}, files={"file": ("v.mp4", b"fakevideo")}).json()["url"]
    assert c.post("/api/verify", headers=hb, json={"video": v}).json()["verifyStatus"] == "pending"
    assert c.get("/api/admin/pending").status_code == 403
    pend = c.get("/api/admin/pending", headers={"X-Admin-Token": "adm"}).json()
    assert pend["verify"][0]["id"] == b["id"]
    c.post(f"/api/admin/verify/{b['id']}?approve=true", headers={"X-Admin-Token": "adm"})
    me = c.put("/api/me/host", headers=hb, json={"price": 30, "voicePrice": 15}).json()
    assert me["isHost"] and me["verified"] and me["coins"] == 10  # 认证奖励 10

    ha = users["a"][0]
    hosts = c.get("/api/hosts", headers=ha).json()
    assert [h["id"] for h in hosts] == [b["id"]] and hosts[0]["price"] == 30
    assert c.get("/api/hosts?chip=city", headers=ha).json()
    assert c.get("/api/hosts?price=60以上", headers=ha).json() == []


def test_recharge_and_invite(c, users):
    ha, a, _ = users["a"]
    hb = users["b"][0]
    o = c.post("/api/orders", headers=ha, json={"kind": "coins", "pack": 2, "channel": "mock"}).json()
    assert c.post("/api/orders", headers=ha, json={"kind": "coins", "pack": 2, "channel": "wechat"}).status_code == 503
    me = c.post(f"/api/orders/{o['orderId']}/mock-pay", headers=ha).json()
    assert me["coins"] == 5 + 720
    c.post(f"/api/orders/{o['orderId']}/mock-pay", headers=ha)  # 重复回调不重复加钱
    assert c.get("/api/me", headers=ha).json()["coins"] == 725
    inv = c.get("/api/invite", headers=hb).json()
    assert inv["list"][0]["paid"] is True
    b = c.get("/api/me", headers=hb).json()
    assert b["coins"] == 10 + 50 and b["earnings"] == 72  # 邀请奖励 + 10% 返现
    sysmsg = c.get("/api/notices/system", headers=ha).json()
    assert any("充值：720" in n["text"] for n in sysmsg)
    vip = c.post("/api/orders", headers=ha, json={"kind": "vip", "plan": "m1", "channel": "mock"}).json()
    assert c.post(f"/api/orders/{vip['orderId']}/mock-pay", headers=ha).json()["vip"] is True


def test_tasks(c, users):
    ha = users["a"][0]
    before = c.get("/api/me", headers=ha).json()["coins"]
    assert c.post("/api/tasks/sign", headers=ha).json()["coins"] == before + 1 + 10  # VIP 多送 10
    assert c.post("/api/tasks/sign", headers=ha).status_code == 400
    assert c.post("/api/tasks/share", headers=ha).json()["coins"] == before + 13
    assert c.post("/api/tasks/share", headers=ha).json()["coins"] == before + 13


def test_social(c, users):
    ha, a, _ = users["a"]
    hb, b, _ = users["b"]
    assert c.post(f"/api/users/{b['id']}/follow", headers=ha).json()["followed"]
    page = c.get(f"/api/users/{b['id']}", headers=ha).json()
    assert page["followed"] and page["fans"] == 1 and page["verified"]
    assert c.get("/api/me/visitors", headers=hb).json()[0]["id"] == a["id"]
    assert c.get("/api/me/follows?kind=fans", headers=hb).json()[0]["id"] == a["id"]
    assert c.get("/api/me/counts", headers=ha).json() == {"following": 1, "fans": 0}

    img = c.post("/api/upload", headers=hb, data={"kind": "image"}, files={"file": ("p.jpg", b"jpg")}).json()["url"]
    p = c.post("/api/posts", headers=hb, json={"text": "在的呢", "images": [img]}).json()
    assert c.post("/api/posts", headers=hb, json={"text": ""}).status_code == 400
    assert c.post("/api/posts", headers=hb, json={"text": "x", "images": ["http://evil/x.jpg"]}).status_code == 400
    feed = c.get("/api/posts", headers=ha).json()
    assert feed[0]["id"] == p["id"] and not feed[0]["mine"]
    assert c.get("/api/posts?tab=follow", headers=ha).json()[0]["id"] == p["id"]
    assert c.post(f"/api/posts/{p['id']}/like", headers=ha).json() == {"liked": True, "likes": 1}
    cm = c.post(f"/api/posts/{p['id']}/comments", headers=ha, json={"text": "好看"}).json()
    assert cm[0]["text"] == "好看"
    assert c.get("/api/posts", headers=ha).json()[0]["comments"] == 1
    hidden = c.post("/api/posts", headers=hb, json={"text": "仅自己", "visibility": "self"}).json()
    assert hidden["id"] not in [x["id"] for x in c.get("/api/posts", headers=ha).json()]
    assert c.delete(f"/api/posts/{p['id']}", headers=ha).status_code == 404  # 不能删别人的
    vid = c.post("/api/upload", headers=hb, data={"kind": "video"}, files={"file": ("r.mp4", b"mp4")}).json()["url"]
    reel = c.post("/api/posts", headers=hb, json={"kind": "reel", "text": "海边", "video": vid}).json()
    assert c.get("/api/posts?kind=reel", headers=ha).json()[0]["id"] == reel["id"]
    assert c.post("/api/reports", headers=ha, json={"type": "post", "id": p["id"], "reason": "广告骚扰"}).json()["ok"]


def test_chat_gift_call_flow(c, users):
    ha, a, ta = users["a"]
    hb, b, tb = users["b"]
    with c.websocket_connect(f"/api/ws?token={ta}") as wa, c.websocket_connect(f"/api/ws?token={tb}") as wb:
        assert wa.receive_json()["type"] == "hello" and wb.receive_json()["type"] == "hello"

        # 文字 + 语音消息实时到达
        c.post("/api/messages", headers=ha, json={"to": b["id"], "kind": "text", "content": "你好"})
        assert recv_until(wb, "message")["message"]["content"] == "你好"
        voice = c.post("/api/upload", headers=ha, data={"kind": "voice"}, files={"file": ("v.webm", b"opus")}).json()["url"]
        c.post("/api/messages", headers=ha, json={"to": b["id"], "kind": "voice", "content": voice, "duration": 3})
        m = recv_until(wb, "message")["message"]
        assert m["kind"] == "voice" and m["extra"]["duration"] == 3
        conv = c.get("/api/conversations", headers=hb).json()
        assert conv[0]["user"]["id"] == a["id"] and conv[0]["unread"] == 2 and conv[0]["user"]["status"] == "online"
        assert len(c.get(f"/api/conversations/{a['id']}/messages", headers=hb).json()) == 2
        assert c.get("/api/conversations", headers=hb).json()[0]["unread"] == 0
        assert [u["id"] for u in c.get("/api/online-users", headers=ha).json()] == [b["id"]]

        # 送礼物：A 扣金币，B 得一半收益
        a_coins = c.get("/api/me", headers=ha).json()["coins"]
        b_earn = c.get("/api/me", headers=hb).json()["earnings"]
        r = c.post("/api/gifts/send", headers=ha, json={"to": b["id"], "giftId": 5}).json()
        assert r["coins"] == a_coins - 99
        assert recv_until(wb, "message")["message"]["kind"] == "gift"
        assert c.get("/api/me", headers=hb).json()["earnings"] == b_earn + 49

        # 视频通话：VIP 9 折 = 27 金币/分钟
        call = c.post("/api/calls", headers=ha, json={"to": b["id"], "media": "video"}).json()["call"]
        assert call["price"] == 27 and call["payerId"] == a["id"]
        assert recv_until(wb, "call_invite")["call"]["id"] == call["id"]
        assert c.post("/api/calls", headers=ha, json={"to": b["id"]}).status_code == 409  # 自己正在通话
        # 信令转发
        wa.send_json({"type": "signal", "callId": call["id"], "data": {"sdp": "offer"}})
        assert recv_until(wb, "signal")["data"] == {"sdp": "offer"}
        assert c.post(f"/api/calls/{call['id']}/accept", headers=ha).status_code == 409  # 只有被叫能接
        c.post(f"/api/calls/{call['id']}/accept", headers=hb)
        assert recv_until(wa, "call_accepted")["call"]["status"] == "active"
        coins0 = r["coins"]
        assert c.get("/api/me", headers=ha).json()["coins"] == coins0 - 27  # 接通先扣第 1 分钟
        time.sleep(1.6)  # 测试里计费周期设成 1 秒
        assert c.get("/api/me", headers=ha).json()["coins"] == coins0 - 54
        c.post(f"/api/calls/{call['id']}/end", headers=hb)
        ended = recv_until(wa, "call_ended")
        assert ended["call"]["status"] == "ended" and ended["call"]["cost"] == 54
        rec = c.get("/api/calls", headers=ha).json()[0]
        assert rec["status"] == "ended" and rec["outgoing"]
        assert c.post(f"/api/calls/{call['id']}/rating", headers=ha, json={"stars": 4, "tags": ["聊得来"]}).json()["ok"]
        assert c.post(f"/api/calls/{call['id']}/rating", headers=ha, json={"stars": 4}).status_code == 400
        assert c.get(f"/api/users/{b['id']}", headers=ha).json()["rating"] == 4.0
        assert c.get("/api/me/ratings", headers=ha).json()[0]["stars"] == 4

        # 无人接听 -> 超时变未接通
        call2 = c.post("/api/calls", headers=ha, json={"to": b["id"], "media": "voice"}).json()["call"]
        assert call2["price"] == 13  # 语音 15 * 0.9
        ended = recv_until(wa, "call_ended", 40)
        assert ended["call"]["status"] == "missed"

        # 拒接
        call3 = c.post("/api/calls", headers=ha, json={"to": b["id"]}).json()["call"]
        c.post(f"/api/calls/{call3['id']}/reject", headers=hb)
        assert recv_until(wa, "call_ended")["call"]["status"] == "rejected"

        # 排行榜：B 魅力榜第一，A 富豪榜第一
        assert c.get("/api/rank?kind=charm", headers=ha).json()[0]["id"] == b["id"]
        assert c.get("/api/rank?kind=rich", headers=ha).json()[0]["id"] == a["id"]

        # 免打扰
        c.put("/api/me/settings", headers=hb, json={"dnd": True})
        assert c.post("/api/calls", headers=ha, json={"to": b["id"]}).json()["detail"]["code"] == "dnd"
        c.put("/api/me/settings", headers=hb, json={"dnd": False})

    # 都下线后不能打
    assert c.post("/api/calls", headers=ha, json={"to": b["id"]}).json()["detail"]["code"] == "offline"


def test_not_enough_coins_and_block(c, users):
    hb, b, tb = users["b"]
    hc, cuser, tc = login(c, "13800000003")
    with c.websocket_connect(f"/api/ws?token={tb}") as wb:
        wb.receive_json()
        r = c.post("/api/calls", headers=hc, json={"to": b["id"]})
        assert r.status_code == 402 and r.json()["detail"]["code"] == "coins"
        assert c.post("/api/gifts/send", headers=hc, json={"to": b["id"], "giftId": 8}).status_code == 402
        c.post(f"/api/users/{cuser['id']}/block", headers=hb)
        assert c.post("/api/messages", headers=hc, json={"to": b["id"], "kind": "text", "content": "hi"}).status_code == 403
        assert c.post("/api/calls", headers=hc, json={"to": b["id"]}).status_code == 403
        assert b["id"] not in [h["id"] for h in c.get("/api/hosts", headers=hc).json()]
        assert c.get("/api/me/blocks", headers=hb).json()[0]["id"] == cuser["id"]


def test_withdraw_and_service(c, users):
    ha = users["a"][0]
    hb = users["b"][0]
    assert c.post("/api/withdraw", headers=ha, json={"coins": 100, "account": "支付宝 138****"}).status_code == 403
    earn = c.get("/api/me", headers=hb).json()["earnings"]
    assert earn >= 100
    assert c.post("/api/withdraw", headers=hb, json={"coins": 100, "account": "支付宝 138****"}).json()["earnings"] == earn - 100
    w = c.get("/api/wallet", headers=hb).json()
    assert w["ledger"][0]["title"] == "提现申请"
    ans = c.post("/api/notices/service", headers=ha, json={"text": "怎么充值"}).json()
    assert "充值" in ans[-1]["text"] and ans[-2]["me"]
    assert c.get("/api/conversations", headers=ha).json()  # 通话记录消息也在会话里
    assert c.post("/api/conversations/read-all", headers=ha).json()["ok"]
