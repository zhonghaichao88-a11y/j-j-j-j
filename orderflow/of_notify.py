"""手机推送（可选）：开仓、平仓、出错时发到微信。
在 .env 里填其中一个就行：
  OF_PUSHPLUS_TOKEN=xxxx      （pushplus.plus 微信公众号推送，免费）
  OF_SERVERCHAN_KEY=SCTxxxx   （Server酱 sct.ftqq.com，免费）
都不填就不推送。推送在后台线程发，不会卡住行情。"""
from __future__ import annotations

import threading

import httpx

CONF = {"pushplus": "", "serverchan": "", "proxy": None}


def setup(env: dict, proxy=None):
    CONF["pushplus"] = env.get("OF_PUSHPLUS_TOKEN", "").strip()
    CONF["serverchan"] = env.get("OF_SERVERCHAN_KEY", "").strip()
    CONF["proxy"] = proxy


def enabled() -> bool:
    return bool(CONF["pushplus"] or CONF["serverchan"])


def _send(title: str, text: str):
    try:
        # 这两个都是国内服务，不走代理
        if CONF["pushplus"]:
            httpx.post("https://www.pushplus.plus/send", timeout=10,
                       json={"token": CONF["pushplus"], "title": title, "content": text, "template": "txt"})
        if CONF["serverchan"]:
            httpx.post(f"https://sctapi.ftqq.com/{CONF['serverchan']}.send", timeout=10,
                       data={"title": title, "desp": text})
    except Exception:  # noqa: BLE001
        pass


def push(title: str, text: str = ""):
    if enabled():
        threading.Thread(target=_send, args=(title[:60], text[:1000]), daemon=True).start()
