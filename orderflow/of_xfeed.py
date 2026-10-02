"""全网数据：币安合约 + 币安现货 + Bybit 合约 + Coinbase，补上欧易一家看不到的东西。

  - 币安合约 / 现货：每 30 秒拉一次 1 分钟K线（自带主动买入量），算现货和合约的主动买卖、现货占比
    （和回测用的是同一种数据，实盘和回测口径一致）
  - Bybit：WebSocket 逐笔成交（主动买卖）+ 爆仓
  - 币安爆仓：WebSocket 全市场爆仓频道
  - Coinbase 溢价：Coinbase BTC-USD 和币安现货 BTCUSDT 的价差，美国资金在买还是在卖
  - 大背景：币安合约 15 分钟K线（最近 10 天）→ 每天的 POC、没被碰过的 POC；1 小时 / 日线趋势
连不上的数据源只是那一项显示"无"，不影响其他。
"""
from __future__ import annotations

import asyncio
import json
import math
import time

import httpx

FAPI = "https://fapi.binance.com"
SAPI = "https://api.binance.com"
SAPI_MIRROR = "https://data-api.binance.vision"      # 币安官方公开行情镜像（主站连不上时用，只有现货）
BYBIT_WS = "wss://stream.bybit.com/v5/public/linear"
BN_LIQ_WS = "wss://fstream.binance.com/ws/!forceOrder@arr"


def base_of(inst):                     # BTC-USDT-SWAP -> BTC
    return inst.split("-")[0]


class CrossHub:
    def __init__(self, proxy=None):
        self.proxy = proxy or None
        self.engines: dict = {}          # inst -> engine
        self.fsym: dict = {}             # BTC -> BTCUSDT / 1000PEPEUSDT（币安合约）
        self.ssym: dict = {}             # BTC -> BTCUSDT（币安现货，没有就不在里面）
        self.bsym: dict = {}             # BTC -> BTCUSDT（Bybit）
        self.mult: dict = {}             # 1000PEPE 这类：价格 ÷ 1000 才是 1 个币的价格
        self.min: dict = {}              # inst -> {分钟: [合约主动买$, 合约成交$, 现货主动买$, 现货成交$, 合约收盘, 现货收盘]}
        self.bybit: dict = {}            # inst -> {分钟: [主动买$, 主动卖$]}
        self.ctx: dict = {}              # inst -> 大背景
        self.cb = {"premium": math.nan, "hist": [], "ts": 0}
        self.status = {"binance": "未连接", "bybit": "未连接", "bn_liq": "未连接", "coinbase": "未连接"}
        self._ws_bybit = None
        self.spot_base = SAPI
        self._stop = False

    # ------------------------------------------------------------ 币种
    def add(self, inst, eng):
        self.engines[inst] = eng
        self.min.setdefault(inst, {})
        self.bybit.setdefault(inst, {})
        if self._ws_bybit is not None and base_of(inst) in self.bsym:
            asyncio.create_task(self._bybit_sub([inst]))

    def remove(self, inst):
        self.engines.pop(inst, None)

    async def _symbols(self, c):
        try:
            r = (await c.get(FAPI + "/fapi/v1/exchangeInfo")).json()
            for s in r["symbols"]:
                if s.get("contractType") == "PERPETUAL" and s["quoteAsset"] == "USDT" and s.get("status") == "TRADING":
                    b = s["baseAsset"]
                    for p in ("1000000", "1000"):
                        if b.startswith(p) and len(b) > len(p):
                            self.mult[b[len(p):]] = int(p)
                            b = b[len(p):]
                            break
                    self.fsym[b] = s["symbol"]
        except Exception:  # noqa: BLE001
            pass
        for base in (SAPI, SAPI_MIRROR):
            try:
                r = (await c.get(base + "/api/v3/exchangeInfo", params={"permissions": "SPOT"})).json()
                for s in r["symbols"]:
                    if s["quoteAsset"] == "USDT" and s.get("status") == "TRADING":
                        self.ssym[s["baseAsset"]] = s["symbol"]
                self.spot_base = base
                break
            except Exception:  # noqa: BLE001
                pass
        try:
            r = (await c.get("https://api.bybit.com/v5/market/instruments-info", params={"category": "linear", "limit": 1000})).json()
            for s in r["result"]["list"]:
                if s["quoteCoin"] == "USDT" and s.get("contractType") == "LinearPerpetual":
                    b = s["baseCoin"]
                    for p in ("1000000", "1000"):
                        if b.startswith(p) and len(b) > len(p):
                            b = b[len(p):]
                            break
                    self.bsym[b] = s["symbol"]
        except Exception:  # noqa: BLE001
            pass

    async def run(self):
        async with httpx.AsyncClient(proxy=self.proxy, timeout=15) as c:
            await self._symbols(c)
        await asyncio.gather(self._binance_loop(), self._bybit_loop(), self._bn_liq_loop(),
                             self._coinbase_loop(), self._ctx_loop())

    # ------------------------------------------------------------ 币安合约 + 现货 1 分钟K线
    async def _klines(self, c, url, sym, limit):
        r = await c.get(url, params={"symbol": sym, "interval": "1m", "limit": limit})
        if r.status_code in (403, 451):
            raise RuntimeError("这个地区被限制，请换代理节点（香港、日本、新加坡等）")
        r.raise_for_status()
        return r.json()

    async def _binance_loop(self):
        first: set = set()
        async with httpx.AsyncClient(proxy=self.proxy, timeout=15) as c:
            while not self._stop:
                ok = 0
                for inst in list(self.engines):
                    b = base_of(inst)
                    fs, ss = self.fsym.get(b), self.ssym.get(b)
                    lim = 3 if inst in first else 1500        # 第一次拉 25 小时，之后只拉最新几根
                    m = self.min.setdefault(inst, {})
                    try:
                        if fs:
                            for k in await self._klines(c, FAPI + "/fapi/v1/klines", fs, lim):
                                row = m.setdefault(int(k[0]) // 60000, [0.0, 0.0, math.nan, math.nan, math.nan, math.nan])
                                row[0], row[1], row[4] = float(k[10]), float(k[7]), float(k[4]) / self.mult.get(b, 1)
                        if ss:
                            for k in await self._klines(c, self.spot_base + "/api/v3/klines", ss, lim):
                                row = m.setdefault(int(k[0]) // 60000, [0.0, 0.0, math.nan, math.nan, math.nan, math.nan])
                                row[2], row[3], row[5] = float(k[10]), float(k[7]), float(k[4])
                        if fs or ss:
                            first.add(inst)
                            ok += 1
                        if len(m) > 2000:
                            for key in sorted(m)[:-1600]:
                                m.pop(key, None)
                    except Exception as e:  # noqa: BLE001
                        self.status["binance"] = f"出错：{e if isinstance(e, RuntimeError) else type(e).__name__}"
                    await asyncio.sleep(0.15)
                if ok:
                    part = "" if self.fsym else "（合约连不上，只有现货）"
                    self.status["binance"] = f"正常（{ok} 个币，每 30 秒）{part}"
                elif self.engines and not (self.fsym or self.ssym):
                    self.status["binance"] = "出错：连不上币安，请检查代理节点"
                await asyncio.sleep(30)

    # ------------------------------------------------------------ Bybit 逐笔成交 + 爆仓
    async def _bybit_sub(self, insts):
        args = []
        for i in insts:
            s = self.bsym.get(base_of(i))
            if s:
                args += [f"publicTrade.{s}", f"allLiquidation.{s}"]
        for k in range(0, len(args), 10):
            try:
                await self._ws_bybit.send(json.dumps({"op": "subscribe", "args": args[k:k + 10]}))
            except Exception:  # noqa: BLE001
                return
            await asyncio.sleep(0.1)

    async def _bybit_loop(self):
        import websockets
        while not self._stop:
            try:
                kw = {"ping_interval": 20, "open_timeout": 10}
                if self.proxy:
                    kw["proxy"] = self.proxy
                if not self.bsym:
                    async with httpx.AsyncClient(proxy=self.proxy, timeout=15) as c:
                        await self._symbols(c)
                    if not self.bsym:
                        self.status["bybit"] = "出错：连不上 Bybit（可能是地区限制，请换代理节点），1 分钟后再试"
                        await asyncio.sleep(60)
                        continue
                async with websockets.connect(BYBIT_WS, **kw) as ws:
                    self._ws_bybit = ws
                    await self._bybit_sub(list(self.engines))
                    self.status["bybit"] = "正常（WebSocket）"
                    by_sym = {}
                    while not self._stop:
                        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=40))
                        topic = msg.get("topic", "")
                        if not topic:
                            continue
                        sym = topic.split(".", 1)[1]
                        if sym not in by_sym:
                            by_sym = {self.bsym[base_of(i)]: i for i in self.engines if base_of(i) in self.bsym}
                        inst = by_sym.get(sym)
                        if inst is None:
                            continue
                        mult = 1000 if sym.startswith("1000") and not sym.startswith("1000000") else (1_000_000 if sym.startswith("1000000") else 1)
                        if topic.startswith("publicTrade"):
                            book = self.bybit.setdefault(inst, {})
                            for t in msg.get("data", []):
                                usd = float(t["p"]) * float(t["v"])
                                row = book.setdefault(int(t["T"]) // 60000, [0.0, 0.0])
                                row[0 if t["S"] == "Buy" else 1] += usd
                            if len(book) > 400:
                                for key in sorted(book)[:-300]:
                                    book.pop(key, None)
                        elif topic.startswith("allLiquidation"):
                            e = self.engines.get(inst)
                            for d in msg.get("data", []):
                                # Bybit 的 S 是被强平那一方的方向：Buy = 多单被平
                                px = float(d["p"]) / mult
                                if e:
                                    e.on_liq_usd(px, float(d["p"]) * float(d["v"]), -1 if d["S"] == "Buy" else 1, int(d["T"]), "Bybit")
            except Exception as e:  # noqa: BLE001
                self._ws_bybit = None
                self.status["bybit"] = f"断开：{type(e).__name__}，10 秒后重连"
                await asyncio.sleep(10)

    # ------------------------------------------------------------ 币安爆仓
    async def _bn_liq_loop(self):
        import websockets
        while not self._stop:
            try:
                kw = {"ping_interval": 20, "open_timeout": 10}
                if self.proxy:
                    kw["proxy"] = self.proxy
                async with websockets.connect(BN_LIQ_WS, **kw) as ws:
                    self.status["bn_liq"] = "正常（WebSocket）"
                    while not self._stop:
                        o = json.loads(await asyncio.wait_for(ws.recv(), timeout=120)).get("o", {})
                        sym = o.get("s", "")
                        for inst, e in list(self.engines.items()):
                            if self.fsym.get(base_of(inst)) == sym:
                                b = base_of(inst)
                                px = float(o["ap"] or o["p"]) / self.mult.get(b, 1)
                                usd = float(o["ap"] or o["p"]) * float(o["z"] or o["q"])
                                # 币安 S=SELL 是强平多单
                                e.on_liq_usd(px, usd, -1 if o["S"] == "SELL" else 1, int(o["T"]), "币安")
            except Exception as e:  # noqa: BLE001
                self.status["bn_liq"] = f"断开：{type(e).__name__}，10 秒后重连"
                await asyncio.sleep(10)

    # ------------------------------------------------------------ Coinbase 溢价
    async def _coinbase_loop(self):
        async with httpx.AsyncClient(proxy=self.proxy, timeout=10) as c:
            while not self._stop:
                try:
                    cb = float((await c.get("https://api.exchange.coinbase.com/products/BTC-USD/ticker")).json()["price"])
                    bn = float((await c.get(self.spot_base + "/api/v3/ticker/price", params={"symbol": "BTCUSDT"})).json()["price"])
                    p = cb / bn - 1
                    now = int(time.time() * 1000)
                    h = self.cb["hist"]
                    h.append((now, p))
                    self.cb["hist"] = [x for x in h if now - x[0] <= 6 * 3600_000]
                    self.cb.update(premium=p, ts=now)
                    self.status["coinbase"] = "正常（每 10 秒）"
                except Exception as e:  # noqa: BLE001
                    self.status["coinbase"] = f"出错：{type(e).__name__}"
                await asyncio.sleep(10)

    # ------------------------------------------------------------ 大背景（每 30 分钟更新一次）
    async def _ctx_loop(self):
        """新加的币马上算；所有币每 30 分钟更新一次"""
        async with httpx.AsyncClient(proxy=self.proxy, timeout=15) as c:
            while not self._stop:
                now = time.time() * 1000
                for inst in list(self.engines):
                    if now - self.ctx.get(inst, {}).get("ts", 0) < 1800_000:
                        continue
                    b = base_of(inst)
                    if self.fsym.get(b):
                        url, sym, mult = FAPI + "/fapi/v1/klines", self.fsym[b], self.mult.get(b, 1)
                    elif self.ssym.get(b):                      # 合约连不上就用现货K线
                        url, sym, mult = self.spot_base + "/api/v3/klines", self.ssym[b], 1
                    else:
                        continue
                    try:
                        r = await c.get(url, params={"symbol": sym, "interval": "15m", "limit": 1000})
                        d = await c.get(url, params={"symbol": sym, "interval": "1d", "limit": 40})
                        self.ctx[inst] = context_from_15m(r.json(), mult, d.json())
                    except Exception:  # noqa: BLE001
                        pass
                    await asyncio.sleep(0.3)
                await asyncio.sleep(20)

    # ------------------------------------------------------------ 给引擎 / 网页用的数字
    def stats(self, inst, okx_min=None):
        """okx_min: 欧易自己的 {分钟: [主动买$, 主动卖$]}，和币安、Bybit 合在一起算全网合约主动买卖"""
        m = self.min.get(inst, {})
        now = int(time.time() // 60)
        out = {"has_binance": bool(m), "has_spot": False, "has_bybit": bool(self.bybit.get(inst))}

        def flow(lo, idx_b, idx_q):
            b = q = 0.0
            for k in range(now - lo, now + 1):
                r = m.get(k)
                if r and not math.isnan(r[idx_q]):
                    b += r[idx_b]
                    q += r[idx_q]
            return (2 * b - q) / q if q > 0 else math.nan, q

        def perp_all(lo):
            buy = sell = 0.0
            for k in range(now - lo, now + 1):
                r = m.get(k)
                if r:
                    buy += r[0]
                    sell += r[1] - r[0]
                y = self.bybit.get(inst, {}).get(k)
                if y:
                    buy += y[0]
                    sell += y[1]
                if okx_min:
                    z = okx_min.get(k)
                    if z:
                        buy += z[0]
                        sell += z[1]
            return (buy - sell) / (buy + sell) if buy + sell > 0 else math.nan, buy - sell

        for lo, tag in ((60, "60"), (240, "240")):
            out["pf_" + tag], out["perp_usd_" + tag] = flow(lo, 0, 1)
            out["sf_" + tag], out["spot_usd_" + tag] = flow(lo, 2, 3)
            out["all_pf_" + tag], out["all_cvd_" + tag] = perp_all(lo)
            if not math.isnan(out["sf_" + tag]):
                out["has_spot"] = True
                perp = out["pf_" + tag] if not math.isnan(out["pf_" + tag]) else out["all_pf_" + tag]   # 币安合约连不上就用欧易+Bybit
                out["div_" + tag] = out["sf_" + tag] - perp
            else:
                out["div_" + tag] = math.nan
        # 现货占比：最近 4 小时 vs 最近 24 小时
        def share(lo):
            s = f = 0.0
            for k in range(now - lo, now + 1):
                r = m.get(k)
                if r and not math.isnan(r[3]):
                    s += r[3]
                    f += r[1]
            return s / (s + f) if s > 0 and f > 0 else math.nan
        out["spot_share"] = share(240)
        out["spot_share_chg"] = out["spot_share"] - share(1440)
        out["cb_premium"] = self.cb["premium"]
        h = self.cb["hist"]
        out["cb_premium_chg"] = (self.cb["premium"] - sum(p for _, p in h[-360:]) / len(h[-360:])) if h else math.nan
        out["ctx"] = self.ctx.get(inst, {})
        return out


def context_from_15m(rows, mult=1, daily=None):
    """15 分钟K线 → 每天 POC、没被碰过的 POC、1 小时 / 日线趋势"""
    if not rows:
        return {}
    days: dict = {}
    closes = []
    for k in rows:
        t, h, l, c, v = int(k[0]), float(k[2]) / mult, float(k[3]) / mult, float(k[4]) / mult, float(k[7])
        closes.append((t, c))
        d = days.setdefault(t // 86_400_000, {"bars": [], "hi": -1e18, "lo": 1e18})
        d["bars"].append((h, l, v))
        d["hi"], d["lo"] = max(d["hi"], h), min(d["lo"], l)
    last = closes[-1][1]
    step = 10 ** math.floor(math.log10(last * 0.001)) if last > 0 else 1
    pocs = []
    for day in sorted(days):
        prof: dict = {}
        for h, l, v in days[day]["bars"]:
            a, b = int(l // step), int(h // step)
            n = b - a + 1
            for r in range(a, b + 1):
                prof[r] = prof.get(r, 0.0) + v / n
        if prof:
            pocs.append((day, (max(prof, key=prof.get) + 0.5) * step))
    today = max(days)
    naked = []
    for i, (day, p) in enumerate(pocs):
        if day >= today:
            continue
        later = [days[d2] for d2 in sorted(days) if d2 > day]
        if all(not (x["lo"] <= p <= x["hi"]) for x in later):
            naked.append(p)
    cs = [c for _, c in closes]

    def ema(n):
        a, e = 2 / (n + 1), cs[0]
        for x in cs:
            e = a * x + (1 - a) * e
        return e
    e4h = ema(16 * 20)                                # 20 根 4 小时
    if daily:
        dc = [float(k[4]) / mult for k in daily]
        a, e1d = 2 / 21, dc[0]
        for x in dc:
            e1d = a * x + (1 - a) * e1d                 # 20 日均线
    else:
        e1d = ema(96 * 5)
    return {"prev_poc": pocs[-2][1] if len(pocs) >= 2 else math.nan, "naked_pocs": naked[-6:],
            "ema_4h": last / e4h - 1, "ema_1d": last / e1d - 1,
            "trend": "上涨" if last > e4h and last > e1d else ("下跌" if last < e4h and last < e1d else "震荡"),
            "ts": int(time.time() * 1000)}
