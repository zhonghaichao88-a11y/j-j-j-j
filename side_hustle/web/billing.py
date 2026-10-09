"""额度与兑换码：免费用户每天按 IP 限次，付费用户用兑换码扣次数。

数据存在一个 SQLite 文件里（默认 web/data.db，可用 OFFICE_WEB_DB 改路径）。
兑换码由 admin.py 生成，卖给客户（闲鱼/微信/小红书收款后发码）。
"""
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = Path(os.environ.get("OFFICE_WEB_DB", Path(__file__).with_name("data.db")))
FREE_PER_DAY = int(os.environ.get("OFFICE_WEB_FREE_PER_DAY", "3"))
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 去掉易混淆的 0/O/1/I
_lock = threading.Lock()


def _conn(path=None):
    c = sqlite3.connect(str(path or DB_PATH))
    c.execute("CREATE TABLE IF NOT EXISTS codes (code TEXT PRIMARY KEY, credits INTEGER NOT NULL,"
              " created REAL NOT NULL, note TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS free_usage (ip TEXT, day TEXT, used INTEGER NOT NULL,"
              " PRIMARY KEY (ip, day))")
    c.execute("CREATE TABLE IF NOT EXISTS runs (ts REAL, tool TEXT, who TEXT, ok INTEGER)")
    return c


def _today():
    return time.strftime("%Y-%m-%d")


def new_codes(n, credits, note="", db=None):
    out = []
    with _lock, _conn(db) as c:
        for _ in range(n):
            code = "-".join("".join(secrets.choice(_ALPHABET) for _ in range(4)) for _ in range(3))
            c.execute("INSERT INTO codes VALUES (?,?,?,?)", (code, credits, time.time(), note))
            out.append(code)
    return out


def normalize(code):
    return (code or "").strip().upper().replace(" ", "")


def code_credits(code, db=None):
    with _conn(db) as c:
        row = c.execute("SELECT credits FROM codes WHERE code=?", (normalize(code),)).fetchone()
    return None if row is None else row[0]


def free_left(ip, db=None):
    with _conn(db) as c:
        row = c.execute("SELECT used FROM free_usage WHERE ip=? AND day=?", (ip, _today())).fetchone()
    return max(0, FREE_PER_DAY - (row[0] if row else 0))


def charge(ip, code=None, db=None):
    """扣一次额度。成功返回 (True, 描述)，失败返回 (False, 原因)。"""
    with _lock, _conn(db) as c:
        if code:
            code = normalize(code)
            row = c.execute("SELECT credits FROM codes WHERE code=?", (code,)).fetchone()
            if row is None:
                return False, "兑换码不存在，请检查是否输错"
            if row[0] <= 0:
                return False, "兑换码次数已用完"
            c.execute("UPDATE codes SET credits=credits-1 WHERE code=?", (code,))
            return True, f"code:{code}"
        day = _today()
        row = c.execute("SELECT used FROM free_usage WHERE ip=? AND day=?", (ip, day)).fetchone()
        used = row[0] if row else 0
        if used >= FREE_PER_DAY:
            return False, f"今天的 {FREE_PER_DAY} 次免费额度已用完，输入兑换码可继续使用"
        c.execute("INSERT INTO free_usage VALUES (?,?,1) ON CONFLICT(ip, day) DO UPDATE SET used=used+1",
                  (ip, day))
        return True, f"ip:{ip}"


def refund(who, db=None):
    """处理失败时把刚扣的额度退回去，不让客户为报错买单。"""
    kind, _, key = who.partition(":")
    with _lock, _conn(db) as c:
        if kind == "code":
            c.execute("UPDATE codes SET credits=credits+1 WHERE code=?", (key,))
        else:
            c.execute("UPDATE free_usage SET used=MAX(used-1,0) WHERE ip=? AND day=?", (key, _today()))


def log_run(tool, who, ok, db=None):
    with _lock, _conn(db) as c:
        c.execute("INSERT INTO runs VALUES (?,?,?,?)", (time.time(), tool, who, int(ok)))


def stats(db=None):
    with _conn(db) as c:
        return {
            "runs_total": c.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
            "runs_today": c.execute("SELECT COUNT(*) FROM runs WHERE ts>=?",
                                    (time.mktime(time.strptime(_today(), "%Y-%m-%d")),)).fetchone()[0],
            "by_tool": dict(c.execute("SELECT tool, COUNT(*) FROM runs GROUP BY tool").fetchall()),
            "codes": c.execute("SELECT COUNT(*), COALESCE(SUM(credits),0) FROM codes").fetchone(),
        }
