"""欧易公开行情（不用 API Key）：合约列表、5 分钟K线、最新价。走 .env 里的 PROXY_URL（没有就直连）。"""
from __future__ import annotations

import asyncio
import time

import httpx

BASE = "https://www.okx.com"
BAR = {"1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H", "2h": "2H", "4h": "4H",
       "6h": "6Hutc", "12h": "12Hutc", "1d": "1Dutc"}


class Okx:
    def __init__(self, proxy: str | None = None):
        self.cli = httpx.AsyncClient(base_url=BASE, timeout=15, proxy=proxy or None,
                                     headers={"User-Agent": "Mozilla/5.0 stratbot"})
        self.sem = asyncio.Semaphore(6)
        self.calls = 0

    async def close(self):
        await self.cli.aclose()

    async def get(self, path, **params):
        """带重试：限频（50011）或网络抖动等一会再试，最多 5 次"""
        last = None
        for k in range(5):
            async with self.sem:
                try:
                    r = await self.cli.get(path, params=params)
                    self.calls += 1
                    j = r.json()
                    if j.get("code") == "0":
                        await asyncio.sleep(0.12)          # 每个通道每秒不超过 ~8 次，远低于欧易限频
                        return j["data"]
                    last = f"{j.get('code')} {j.get('msg')}"
                except (httpx.HTTPError, ValueError) as e:
                    last = repr(e)
            await asyncio.sleep(1 + k * 2)
        raise RuntimeError(f"欧易接口 {path} 失败：{last}")

    async def swaps(self):
        """所有 USDT 本位永续：{instId: {tick, ctVal, lotSz, minSz, listTime, state}}"""
        out = {}
        for x in await self.get("/api/v5/public/instruments", instType="SWAP"):
            if x.get("settleCcy") != "USDT" or x.get("ctType") != "linear":
                continue
            f = lambda v: float(v) if v else 0.0
            out[x["instId"]] = {"tick": f(x["tickSz"]), "ctVal": f(x["ctVal"]), "lotSz": f(x["lotSz"]),
                                "minSz": f(x["minSz"]), "listTime": int(x.get("listTime") or 0), "state": x["state"]}
        return out

    async def tickers(self):
        """所有永续最新价 {instId: last}"""
        return {x["instId"]: float(x["last"]) for x in await self.get("/api/v5/market/tickers", instType="SWAP")
                if x.get("last")}

    async def turnover(self):
        """所有永续 24 小时成交额（USDT）：{instId: 成交额}"""
        out = {}
        for x in await self.get("/api/v5/market/tickers", instType="SWAP"):
            try:
                out[x["instId"]] = float(x.get("volCcy24h") or 0) * float(x.get("last") or 0)
            except ValueError:
                pass
        return out

    async def candles(self, inst, tf="5m", n=1000):
        """最近 n 根【已收盘】K线，按时间从早到晚：[(ts毫秒, o, h, l, c, vol), ...]"""
        rows = {}
        data = await self.get("/api/v5/market/candles", instId=inst, bar=BAR[tf], limit=min(300, n + 1))
        for d in data:
            if d[8] == "1":
                rows[int(d[0])] = d
        after = min(rows) if rows else None
        while len(rows) < n and after:
            data = await self.get("/api/v5/market/history-candles", instId=inst, bar=BAR[tf], limit=100, after=after)
            if not data:
                break
            for d in data:
                if d[8] == "1":
                    rows[int(d[0])] = d
            nxt = min(int(d[0]) for d in data)
            if nxt >= after:
                break
            after = nxt
        ks = sorted(rows)[-n:]
        return [(k, float(rows[k][1]), float(rows[k][2]), float(rows[k][3]), float(rows[k][4]), float(rows[k][5]))
                for k in ks]

    async def recent(self, inst, tf="5m", n=5):
        """最近几根已收盘K线（每根K线收盘后补上新的）"""
        data = await self.get("/api/v5/market/candles", instId=inst, bar=BAR[tf], limit=n + 1)
        return sorted((int(d[0]), float(d[1]), float(d[2]), float(d[3]), float(d[4]), float(d[5]))
                      for d in data if d[8] == "1")


def now_ms():
    return int(time.time() * 1000)
