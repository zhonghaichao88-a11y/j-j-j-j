"""欧易实时行情：逐笔成交 + 挂单深度。
优先用 WebSocket（快）；连不上（比如代理不支持）自动改用 REST 轮询（慢一点，但能用）。
只读公开行情，不需要 API 密钥。"""
from __future__ import annotations

import asyncio
import json
import math
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

    def full(self, bids, asks):
        """books-full（上下各 5000 档）：远处的挂单用它，近处以实时推送为准"""
        self.fbids = {float(p): float(s) for p, s, *_ in bids if float(s) > 0}
        self.fasks = {float(p): float(s) for p, s, *_ in asks if float(s) > 0}
        self.fts = time.time()

    def levels(self):
        fb, fa = getattr(self, "fbids", {}), getattr(self, "fasks", {})
        if not fb and not fa:
            return self.bids, self.asks
        nb_lo = min(self.bids) if self.bids else math.inf
        na_hi = max(self.asks) if self.asks else -math.inf
        bids = {p: s for p, s in fb.items() if p < nb_lo}
        bids.update(self.bids)
        asks = {p: s for p, s in fa.items() if p > na_hi}
        asks.update(self.asks)
        if self.bids and self.asks:          # 去掉已经被吃掉、穿过最优价的旧档位
            bb, ba = max(self.bids), min(self.asks)
            bids = {p: s for p, s in bids.items() if p <= bb}
            asks = {p: s for p, s in asks.items() if p >= ba}
        return bids, asks

    def top(self, n=60):
        bids, asks = self.levels()
        b = sorted(bids.items(), key=lambda x: -x[0])[:n]
        a = sorted(asks.items(), key=lambda x: x[0])[:n]
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
            full_task = asyncio.create_task(self._full_book_loop())
            try:
                await self._ws_loop(ws)
            finally:
                full_task.cancel()

    async def _full_book_loop(self):
        async with httpx.AsyncClient(proxy=self.proxy, timeout=10) as c:
            while not self._stop:
                try:
                    r = await c.get(f"{REST}/api/v5/market/books-full", params={"instId": self.inst, "sz": 5000})
                    d = r.json().get("data", [])
                    if d:
                        self.book.full(d[0]["bids"], d[0]["asks"])
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(3)

    async def _ws_loop(self, ws):
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
                if n % 4 == 0:
                    rb = await c.get(f"{REST}/api/v5/market/books-full", params={"instId": self.inst, "sz": 5000})
                    d = rb.json().get("data", [])
                    if d:
                        self.book.snapshot(d[0]["bids"][:400], d[0]["asks"][:400])
                        self.book.full(d[0]["bids"], d[0]["asks"])
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


class OkxExtras:
    """爆仓、持仓量、资金费率、多空比（REST 轮询，频率很低，不会超限）。"""

    def __init__(self, inst_id: str, proxy: str | None, on_liq, on_oi, on_funding, on_ratio):
        self.inst = inst_id
        self.uly = "-".join(inst_id.split("-")[:2])
        self.proxy = proxy
        self.on_liq, self.on_oi, self.on_funding, self.on_ratio = on_liq, on_oi, on_funding, on_ratio
        self.seen: set = set()
        self.status = ""
        self._stop = False

    async def _get(self, c, path, **params):
        r = await c.get(f"{REST}{path}", params=params)
        j = r.json()
        if str(j.get("code")) != "0":
            raise RuntimeError(j.get("msg") or j.get("code"))
        return j["data"]

    async def run(self):
        n = 0
        async with httpx.AsyncClient(proxy=self.proxy, timeout=10) as c:
            while not self._stop:
                try:
                    # 爆仓单：每 2 秒
                    data = await self._get(c, "/api/v5/public/liquidation-orders", instType="SWAP",
                                           uly=self.uly, state="filled", limit=100)
                    fresh = []
                    for blk in data:
                        if blk.get("instId") != self.inst:
                            continue
                        for d in blk.get("details", []):
                            key = (d["ts"], d["bkPx"], d["sz"], d["posSide"])
                            if key in self.seen:
                                continue
                            self.seen.add(key)
                            fresh.append(d)
                    if len(self.seen) > 5000:
                        self.seen = set(list(self.seen)[-2000:])
                    first = n == 0
                    for d in sorted(fresh, key=lambda x: int(x["ts"])):
                        # posSide=long 表示多单被强平（强平单是卖出）
                        self.on_liq(float(d["bkPx"]), float(d["sz"]), -1 if d["posSide"] == "long" else 1,
                                    int(d["ts"]), history=first)
                    if n % 3 == 0:
                        d = (await self._get(c, "/api/v5/public/open-interest", instType="SWAP", instId=self.inst))[0]
                        self.on_oi(float(d["oiCcy"]), float(d["oiUsd"]), int(d["ts"]))
                    if n % 15 == 0:
                        d = (await self._get(c, "/api/v5/public/funding-rate", instId=self.inst))[0]
                        self.on_funding(float(d["fundingRate"]), int(d["fundingTime"]))
                    if n % 30 == 0:
                        acc = await self._get(c, "/api/v5/rubik/stat/contracts/long-short-account-ratio-contract",
                                              instId=self.inst, period="5m", limit=1)
                        top = []
                        try:
                            top = await self._get(c, "/api/v5/rubik/stat/contracts/long-short-position-ratio-contract-top-trader",
                                                  instId=self.inst, period="5m", limit=1)
                        except Exception:  # noqa: BLE001
                            pass
                        self.on_ratio(float(acc[0][1]) if acc else math.nan, float(top[0][1]) if top else math.nan)
                    self.status = "衍生品数据正常"
                except Exception as e:  # noqa: BLE001
                    self.status = f"衍生品数据出错：{e}"
                n += 1
                await asyncio.sleep(2)
