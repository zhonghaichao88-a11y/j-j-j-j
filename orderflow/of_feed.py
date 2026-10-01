"""欧易实时行情：逐笔成交 + 挂单深度。
优先用 WebSocket（快）；连不上（比如代理不支持）自动改用 REST 轮询（慢一点，但能用）。
只读公开行情，不需要 API 密钥。"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Callable

import httpx

REST = "https://www.okx.com"
_HIST_LOCK = asyncio.Lock()   # 历史成交接口限频是按 IP 算的，几个币一起补会超限，排队一个个来
WS = "wss://ws.okx.com:8443/ws/v5/public"


class Book:
    """本地挂单簿：price -> size（合约张数）"""

    def __init__(self):
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.ts = 0

    def snapshot(self, bids, asks):
        self.bids = {float(p): float(s) for p, s, *_ in bids if float(s) > 0}
        self.asks = {float(p): float(s) for p, s, *_ in asks if float(s) > 0}
        self.ts = time.time()

    def update(self, bids, asks):
        for side, levels in ((self.bids, bids), (self.asks, asks)):
            for p, s, *_ in levels:
                p, s = float(p), float(s)
                if s == 0:
                    side.pop(p, None)
                else:
                    side[p] = s
        self.ts = time.time()

    def top(self, n=60):
        b = sorted(self.bids.items(), key=lambda x: -x[0])[:n]
        a = sorted(self.asks.items(), key=lambda x: x[0])[:n]
        return b, a


class OkxFeed:
    def __init__(self, inst_id: str, on_trade: Callable, proxy: str | None = None, mode: str = "auto"):
        self.inst = inst_id
        self.on_trade = on_trade          # on_trade(price, size_contracts, is_buy, ts_ms)
        self.proxy = proxy or None
        self.mode = mode                  # auto / ws / rest
        self.book = Book()
        self.status = "未连接"
        self.last_trade_id = 0
        self._stop = False
        self.using = ""

    async def instrument(self) -> dict:
        async with httpx.AsyncClient(proxy=self.proxy, timeout=15) as c:
            r = await c.get(f"{REST}/api/v5/public/instruments", params={"instType": "SWAP", "instId": self.inst})
            return r.json()["data"][0]

    async def candles(self, bar="5m", limit=100) -> list:
        async with httpx.AsyncClient(proxy=self.proxy, timeout=15) as c:
            r = await c.get(f"{REST}/api/v5/market/candles", params={"instId": self.inst, "bar": bar, "limit": limit})
            return r.json()["data"]

    async def backfill(self, since_ms: int, max_pages: int = 400) -> list:
        """往回拉最近的逐笔成交（启动时补出最近几根足迹K线）。返回按时间排好的列表。"""
        out, after = [], None
        async with _HIST_LOCK, httpx.AsyncClient(proxy=self.proxy, timeout=15) as c:
            for _ in range(max_pages):
                params = {"instId": self.inst, "type": 1, "limit": 100}
                if after:
                    params["after"] = after
                r = await c.get(f"{REST}/api/v5/market/history-trades", params=params)
                j = r.json()
                if j.get("code") not in ("0", 0):
                    await asyncio.sleep(1)            # 被限频，等一下再拉
                    continue
                data = j.get("data", [])
                if not data:
                    break
                out += data
                after = min(int(t["tradeId"]) for t in data)
                if min(int(t["ts"]) for t in data) < since_ms:
                    break
                await asyncio.sleep(0.11)          # 别超过欧易限频（20次/2秒）
        out = [t for t in out if int(t["ts"]) >= since_ms]
        out.sort(key=lambda t: int(t["tradeId"]))
        if out:
            self.last_trade_id = int(out[-1]["tradeId"])
        return out

    def stop(self):
        self._stop = True

    async def run(self):
        while not self._stop:
            if self.mode in ("auto", "ws"):
                try:
                    await self._run_ws()
                except Exception as e:  # noqa: BLE001
                    self.status = f"WebSocket 断开：{type(e).__name__}"
                    if self.mode == "auto":
                        self.mode = "rest"       # 连不上就改用轮询
                    await asyncio.sleep(2)
                    continue
            else:
                try:
                    await self._run_rest()
                except Exception as e:  # noqa: BLE001
                    self.status = f"REST 出错：{type(e).__name__}，2秒后重试"
                    await asyncio.sleep(2)

    # ---------------------------------------------------------- WebSocket
    async def _run_ws(self):
        import websockets
        kw = {"ping_interval": 20, "max_size": 2 ** 23, "open_timeout": 10}
        if self.proxy:
            kw["proxy"] = self.proxy          # websockets 15+ 支持 HTTP 代理
        async with websockets.connect(WS, **kw) as ws:
            await ws.send(json.dumps({"op": "subscribe", "args": [
                {"channel": "trades", "instId": self.inst},
                {"channel": "books", "instId": self.inst}]}))
            self.status, self.using = "已连接（WebSocket 实时）", "ws"
            while not self._stop:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                arg = msg.get("arg", {})
                if arg.get("channel") == "trades":
                    for t in msg.get("data", []):
                        self.last_trade_id = max(self.last_trade_id, int(t["tradeId"]))
                        self.on_trade(float(t["px"]), float(t["sz"]), t["side"] == "buy", int(t["ts"]))
                elif arg.get("channel") == "books":
                    for d in msg.get("data", []):
                        if msg.get("action") == "snapshot":
                            self.book.snapshot(d["bids"], d["asks"])
                        else:
                            self.book.update(d["bids"], d["asks"])

    # ---------------------------------------------------------- REST 轮询
    async def _run_rest(self):
        self.status, self.using = "已连接（REST 轮询，约1秒延迟）", "rest"
        async with httpx.AsyncClient(proxy=self.proxy, timeout=10) as c:
            n = 0
            while not self._stop:
                r = await c.get(f"{REST}/api/v5/market/trades", params={"instId": self.inst, "limit": 500})
                data = r.json().get("data", [])
                new = [t for t in data if int(t["tradeId"]) > self.last_trade_id]
                if self.last_trade_id and new and min(int(t["tradeId"]) for t in new) > self.last_trade_id + 1:
                    new += await self._fill_gap(c, self.last_trade_id, min(int(t["tradeId"]) for t in new))
                for t in sorted(new, key=lambda x: int(x["tradeId"])):
                    if int(t["tradeId"]) <= self.last_trade_id:
                        continue
                    self.last_trade_id = int(t["tradeId"])
                    self.on_trade(float(t["px"]), float(t["sz"]), t["side"] == "buy", int(t["ts"]))
                if n % 2 == 0:
                    rb = await c.get(f"{REST}/api/v5/market/books", params={"instId": self.inst, "sz": 400})
                    d = rb.json().get("data", [])
                    if d:
                        self.book.snapshot(d[0]["bids"], d[0]["asks"])
                n += 1
                await asyncio.sleep(0.5)

    async def _fill_gap(self, c, last_id, first_new):
        """轮询间隔里成交太多，用历史成交接口补齐中间漏掉的"""
        out, after = [], first_new
        for _ in range(20):
            r = await c.get(f"{REST}/api/v5/market/history-trades",
                            params={"instId": self.inst, "type": 1, "after": after, "limit": 100})
            data = r.json().get("data", [])
            if not data:
                break
            out += [t for t in data if int(t["tradeId"]) > last_id]
            after = min(int(t["tradeId"]) for t in data)
            if after <= last_id + 1:
                break
        return out
