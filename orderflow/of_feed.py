"""欧易实时行情（一条连接管所有币）：逐笔成交、挂单深度、爆仓、持仓量、资金费率、多空比。
优先 WebSocket（快）；连不上（比如代理不支持）自动改用 REST 轮询（慢一点，但能用）。
只读公开行情，不需要 API 密钥。

为什么一条连接管所有币：币一多，每个币各开一条连接、各自轮询，会被欧易限频。
  - WebSocket：一条连接订阅所有币的成交、盘口（400档），再加一个"全市场爆仓"频道
  - 全量盘口（上下 5000 档，热力图用）：只拉网页上正在看的那个币
  - 持仓量、资金费率：一次请求拿全市场
  - 多空比：每个币轮流拉，很慢的频率
"""
from __future__ import annotations

import asyncio
import json
import math
import time

import httpx

REST = "https://www.okx.com"
WS = "wss://ws.okx.com:8443/ws/v5/public"


class Book:
    """本地挂单簿：price -> size（合约张数）"""

    def __init__(self):
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.fbids: dict[float, float] = {}
        self.fasks: dict[float, float] = {}
        self.ts = 0.0
        self.fts = 0.0

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
        fb, fa = (self.fbids, self.fasks) if time.time() - self.fts < 30 else ({}, {})
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


# ---------------------------------------------------------------- 一次性的 REST 查询
async def get_json(c: httpx.AsyncClient, path: str, **params):
    for k in range(4):
        r = await c.get(f"{REST}{path}", params=params)
        j = r.json()
        if str(j.get("code")) == "0":
            return j["data"]
        if str(j.get("code")) in ("50011", "50061"):      # 限频，等一下
            await asyncio.sleep(1 + k)
            continue
        raise RuntimeError(j.get("msg") or j.get("code"))
    raise RuntimeError("欧易限频，稍后再试")


async def instruments(proxy) -> dict:
    """全部 USDT 永续合约的面值、最小价格单位"""
    async with httpx.AsyncClient(proxy=proxy, timeout=20) as c:
        data = await get_json(c, "/api/v5/public/instruments", instType="SWAP")
    return {d["instId"]: d for d in data if d["instId"].endswith("-USDT-SWAP") and d.get("state") == "live"}


async def top_by_volume(proxy, n: int) -> list[str]:
    """按 24 小时成交额（美元）挑最活跃的 n 个 USDT 永续"""
    async with httpx.AsyncClient(proxy=proxy, timeout=20) as c:
        data = await get_json(c, "/api/v5/market/tickers", instType="SWAP")
    rows = [d for d in data if d["instId"].endswith("-USDT-SWAP")]
    rows.sort(key=lambda d: -float(d.get("volCcy24h") or 0) * float(d.get("last") or 0))
    out = [d["instId"] for d in rows[:n]]
    if "BTC-USDT-SWAP" not in out:
        out = ["BTC-USDT-SWAP"] + out[:-1]
    return out


# ---------------------------------------------------------------- 成交 + 盘口
class OkxHub:
    def __init__(self, proxy: str | None = None, mode: str = "auto"):
        self.proxy = proxy or None
        self.mode = mode                 # auto / ws / rest
        self.books: dict[str, Book] = {}
        self.on_trade: dict = {}         # inst -> fn(price, size_contracts, is_buy, ts)
        self.on_liq = None               # fn(inst, price, size_contracts, side, ts)
        self.focus: str | None = None    # 网页正在看的币：拉全量盘口
        self.status = "未连接"
        self.using = ""
        self.last_id: dict[str, int] = {}
        self._ws = None
        self._stop = False
        self._hist_lock = asyncio.Lock()

    def add(self, inst, on_trade):
        self.books.setdefault(inst, Book())
        self.on_trade[inst] = on_trade
        if self._ws is not None:
            asyncio.create_task(self._subscribe([inst]))

    def remove(self, inst):
        self.on_trade.pop(inst, None)
        if self._ws is not None:
            asyncio.create_task(self._send("unsubscribe", [inst]))

    def book(self, inst) -> Book:
        return self.books.setdefault(inst, Book())

    async def run(self):
        asyncio.create_task(self._full_book_loop())
        while not self._stop:
            if self.mode in ("auto", "ws"):
                try:
                    await self._run_ws()
                except Exception as e:  # noqa: BLE001
                    self._ws = None
                    self.status = f"WebSocket 断开：{type(e).__name__}，重连中"
                    if self.mode == "auto" and self.using != "ws":
                        self.mode = "rest"      # 一次都没连上过：改用轮询
                    await asyncio.sleep(2)
            else:
                try:
                    await self._run_rest()
                except Exception as e:  # noqa: BLE001
                    self.status = f"REST 出错：{type(e).__name__}，2 秒后重试"
                    await asyncio.sleep(2)

    # ---------------------------------------------------------- WebSocket
    async def _send(self, op, insts):
        args = []
        for i in insts:
            args += [{"channel": "trades", "instId": i}, {"channel": "books", "instId": i}]
        for k in range(0, len(args), 40):           # 一次别发太多
            await self._ws.send(json.dumps({"op": op, "args": args[k:k + 40]}))
            await asyncio.sleep(0.2)

    async def _subscribe(self, insts):
        try:
            await self._send("subscribe", insts)
        except Exception:  # noqa: BLE001
            pass

    async def _run_ws(self):
        import websockets
        kw = {"ping_interval": 20, "max_size": 2 ** 24, "open_timeout": 10}
        if self.proxy:
            kw["proxy"] = self.proxy          # websockets 15+ 支持 HTTP 代理
        async with websockets.connect(WS, **kw) as ws:
            self._ws = ws
            await ws.send(json.dumps({"op": "subscribe", "args": [{"channel": "liquidation-orders", "instType": "SWAP"}]}))
            await self._send("subscribe", list(self.on_trade))
            self.status, self.using = f"已连接（WebSocket 实时，{len(self.on_trade)} 个币）", "ws"
            while not self._stop:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                arg = msg.get("arg", {})
                ch = arg.get("channel")
                if ch == "trades":
                    fn = self.on_trade.get(arg.get("instId"))
                    if fn:
                        for t in msg.get("data", []):
                            fn(float(t["px"]), float(t["sz"]), t["side"] == "buy", int(t["ts"]))
                elif ch == "books":
                    bk = self.books.get(arg.get("instId"))
                    if bk is not None:
                        for d in msg.get("data", []):
                            if msg.get("action") == "snapshot":
                                bk.snapshot(d["bids"], d["asks"])
                            else:
                                bk.update(d["bids"], d["asks"])
                elif ch == "liquidation-orders" and self.on_liq:
                    for blk in msg.get("data", []):
                        inst = blk.get("instId")
                        if inst in self.on_trade:
                            for d in blk.get("details", []):
                                # posSide=long：多单被强平
                                self.on_liq(inst, float(d["bkPx"]), float(d["sz"]),
                                            -1 if d.get("posSide") == "long" else 1, int(d["ts"]))
                self.status = f"已连接（WebSocket 实时，{len(self.on_trade)} 个币）"

    async def _full_book_loop(self):
        """全量盘口（上下 5000 档）：正在看的币每 3 秒一次；其他币轮流，每 3 秒顺带一个"""
        async with httpx.AsyncClient(proxy=self.proxy, timeout=10) as c:
            k = 0
            while not self._stop:
                others = [i for i in self.on_trade if i != self.focus]
                todo = ([self.focus] if self.focus else []) + ([others[k % len(others)]] if others else [])
                k += 1
                for inst in todo:
                    try:
                        d = await get_json(c, "/api/v5/market/books-full", instId=inst, sz=5000)
                        if d:
                            self.book(inst).full(d[0]["bids"], d[0]["asks"])
                    except Exception:  # noqa: BLE001
                        pass
                    await asyncio.sleep(0.5)
                await asyncio.sleep(2)

    async def history_trades(self, inst, since_ms, max_pages=10):
        """往回拉最近的逐笔成交（启动时补出当前这根K线的足迹）。返回按时间排好的列表"""
        out, after = [], None
        async with self._hist_lock, httpx.AsyncClient(proxy=self.proxy, timeout=15) as c:
            for _ in range(max_pages):
                params = {"instId": inst, "type": 1, "limit": 100}
                if after:
                    params["after"] = after
                try:
                    data = await get_json(c, "/api/v5/market/history-trades", **params)
                except Exception:  # noqa: BLE001
                    break
                if not data:
                    break
                out += data
                after = min(int(t["tradeId"]) for t in data)
                if min(int(t["ts"]) for t in data) < since_ms:
                    break
                await asyncio.sleep(0.12)
        out = [t for t in out if int(t["ts"]) >= since_ms]
        out.sort(key=lambda t: int(t["tradeId"]))
        return out

    # ---------------------------------------------------------- REST 轮询（WebSocket 连不上时）
    async def _run_rest(self):
        self.using = "rest"
        sem = asyncio.Semaphore(6)
        async with httpx.AsyncClient(proxy=self.proxy, timeout=10) as c:
            n = 0
            while not self._stop:
                insts = list(self.on_trade)
                self.status = f"已连接（REST 轮询，{len(insts)} 个币，约 1~3 秒延迟）"
                await asyncio.gather(*(self._poll_one(c, sem, i, n) for i in insts))
                n += 1
                await asyncio.sleep(max(0.5, len(insts) * 0.06))

    async def _poll_one(self, c, sem, inst, n):
        async with sem:
            try:
                data = await get_json(c, "/api/v5/market/trades", instId=inst, limit=500)
                last = self.last_id.get(inst, 0)
                fn = self.on_trade.get(inst)
                for t in sorted(data, key=lambda x: int(x["tradeId"])):
                    tid = int(t["tradeId"])
                    if tid <= last:
                        continue
                    last = tid
                    if fn and self.last_id.get(inst):        # 第一轮只记位置，不当新成交
                        fn(float(t["px"]), float(t["sz"]), t["side"] == "buy", int(t["ts"]))
                self.last_id[inst] = last
                if n % 4 == 0 and inst != self.focus:
                    d = await get_json(c, "/api/v5/market/books", instId=inst, sz=400)
                    if d:
                        self.book(inst).snapshot(d[0]["bids"], d[0]["asks"])
                elif inst == self.focus and n % 2 == 0:
                    d = await get_json(c, "/api/v5/market/books", instId=inst, sz=400)
                    if d:
                        self.book(inst).snapshot(d[0]["bids"], d[0]["asks"])
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------- 衍生品数据
class OkxExtrasHub:
    """持仓量（全市场一次拿）、资金费率（全市场一次拿）、多空比（轮流）、爆仓（WebSocket 没有时轮流拉）"""

    def __init__(self, proxy, hub: OkxHub):
        self.proxy, self.hub = proxy, hub
        self.handlers: dict = {}      # inst -> engine（on_oi / on_funding / on_ratio / on_liq）
        self.seen: dict = {}
        self.status = ""
        self._stop = False

    def add(self, inst, eng):
        self.handlers[inst] = eng

    def remove(self, inst):
        self.handlers.pop(inst, None)

    async def run(self):
        n = 0
        async with httpx.AsyncClient(proxy=self.proxy, timeout=15) as c:
            while not self._stop:
                insts = list(self.handlers)
                try:
                    if n % 3 == 0:          # 持仓量：每 6 秒
                        for d in await get_json(c, "/api/v5/public/open-interest", instType="SWAP"):
                            e = self.handlers.get(d["instId"])
                            if e:
                                e.on_oi(float(d["oiCcy"]), float(d["oiUsd"]), int(d["ts"]))
                    if n % 15 == 0:         # 资金费率：每 30 秒
                        for d in await get_json(c, "/api/v5/public/funding-rate", instId="ANY"):
                            e = self.handlers.get(d["instId"])
                            if e:
                                e.on_funding(float(d["fundingRate"]), int(d["fundingTime"]))
                    if insts:               # 多空比：每 2 秒轮到一个币
                        inst = insts[n % len(insts)]
                        acc = await get_json(c, "/api/v5/rubik/stat/contracts/long-short-account-ratio-contract",
                                             instId=inst, period="5m", limit=1)
                        top = []
                        try:
                            top = await get_json(c, "/api/v5/rubik/stat/contracts/long-short-position-ratio-contract-top-trader",
                                                 instId=inst, period="5m", limit=1)
                        except Exception:  # noqa: BLE001
                            pass
                        if inst in self.handlers:
                            self.handlers[inst].on_ratio(float(acc[0][1]) if acc else math.nan,
                                                         float(top[0][1]) if top else math.nan)
                    if insts and self.hub.using != "ws":     # WebSocket 有全市场爆仓频道；没有就轮流拉
                        inst = insts[(n * 7) % len(insts)]
                        await self._liq_rest(c, inst)
                    self.status = "衍生品数据正常"
                except Exception as e:  # noqa: BLE001
                    self.status = f"衍生品数据出错：{e}"
                n += 1
                await asyncio.sleep(2)

    async def _liq_rest(self, c, inst):
        uly = "-".join(inst.split("-")[:2])
        data = await get_json(c, "/api/v5/public/liquidation-orders", instType="SWAP", uly=uly, state="filled", limit=100)
        seen = self.seen.setdefault(inst, set())
        first = not seen
        fresh = []
        for blk in data:
            if blk.get("instId") != inst:
                continue
            for d in blk.get("details", []):
                key = (d["ts"], d["bkPx"], d["sz"], d["posSide"])
                if key not in seen:
                    seen.add(key)
                    fresh.append(d)
        if len(seen) > 3000:
            self.seen[inst] = set(list(seen)[-1000:])
        e = self.handlers.get(inst)
        for d in sorted(fresh, key=lambda x: int(x["ts"])):
            if e:
                e.on_liq(float(d["bkPx"]), float(d["sz"]), -1 if d["posSide"] == "long" else 1, int(d["ts"]), history=first)
