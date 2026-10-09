"""测试环境：独立的临时数据库和上传目录；计费周期 1 秒、响铃 2 秒，方便测通话。

默认用 SQLite；设置 SEEU_TEST_PG=postgresql+psycopg://... 则在 PostgreSQL 上跑同一套测试。
"""
import os
import tempfile

TMP = tempfile.mkdtemp()
os.environ.update(
    SEEU_DATABASE_URL=os.environ.get("SEEU_TEST_PG") or f"sqlite:///{TMP}/t.db",
    SEEU_UPLOAD_DIR=f"{TMP}/up", SEEU_DATA_DIR=TMP, SEEU_DEV="1", SEEU_ADMIN_TOKEN="adm",
    SEEU_BILL_INTERVAL="1", SEEU_RING_TIMEOUT="2",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server.main import app  # noqa: E402

ADMIN = {"X-Admin-Token": "adm"}
# 能通过校验位的测试身份证号（虚构）
ID_A = "110101199003078515"
ID_B = "110101199503075513"


def valid_id(prefix17: str) -> str:
    w = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    return prefix17 + "10X98765432"[sum(int(c) * k for c, k in zip(prefix17, w)) % 11]


@pytest.fixture(scope="session")
def c():
    with TestClient(app) as client:
        yield client


def login(c, phone, invite=""):
    code = c.post("/api/auth/sms", json={"phone": phone}).json()["devCode"]
    r = c.post("/api/auth/login", json={"phone": phone, "code": code, "invite": invite}).json()
    return {"Authorization": f"Bearer {r['token']}"}, r["me"], r["token"]


def recv_until(ws, typ, limit=30):
    for _ in range(limit):
        m = ws.receive_json()
        if m["type"] == typ:
            return m
    raise AssertionError(f"没收到 {typ}")
