"""策略机主程序：拉欧易K线 → 每根K线收盘算信号 → 开仓；每 2 秒看最新价 → 按策略规则平仓。

和 freqtrade 实盘一样的做法：
  - 只在K线收盘后看那根收盘K线的信号（process_only_new_candles），信号出现就按市价开
  - 平仓条件每 2 秒按最新价检查：止损 / 移动止盈 / ROI 时间表 / 卖出信号（规则代码在 sb_engine，和回测共用一份）
  - 每个币同一时间只拿一单；每个策略最多同时 N 单；每单资金 =（策略本金 + 已实现盈亏 - 在用资金）÷ 剩下的空位（和 freqtrade 一样）
模拟盘：按最新价成交，扣吃单手续费 0.05% + 滑点 0.02%（每边）。
实盘：市价开仓，交易所同时挂一张保护止损（比策略止损再远 1%，程序没关时由程序先按策略止损平掉）。
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import threading
import time
import traceback

import sb_engine
import sb_loader
import sb_notify
from sb_data import Okx, now_ms

log = logging.getLogger("stratbot")
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "sb_state.json")
TRADES = os.path.join(HERE, "sb_trades.jsonl")
LIVE_CONFIRM = "我确认开实盘"


def inst_of(coin):
    return f"{coin}-USDT-SWAP"


def coin_of(inst):
    return inst.split("-")[0]


class Bot:
    def __init__(self, cfg: dict, env: dict, cfg_path: str):
        self.cfg, self.env, self.cfg_path = cfg, env, cfg_path
        self.proxy = env.get("PROXY_URL") or None
        self.keys = {"key": env.get("OKX_API_KEY", ""), "secret": env.get("OKX_API_SECRET", ""),
                     "passphrase": env.get("OKX_API_PASSPHRASE", "")}
        self.allow_live = env.get("SB_ALLOW_LIVE", env.get("OF_ALLOW_LIVE", "0")) == "1"
        self.okx: Okx | None = None
        self.inst: dict = {}                    # 合约信息
        self.coins: list[str] = []              # 在跑的币（instId）
        self.frames: dict = {}                  # instId -> DataFrame(date, open, high, low, close, volume)
        self.sig: dict = {}                     # (策略, instId) -> {"ts":收盘K线开盘时间, "enter":0/1, "exit":0/1}
        self.prices: dict = {}
        self.price_ts = 0
        self.trades: list[sb_engine.Trade] = []
        self.ledger: dict = {}                  # 策略 -> 已实现盈亏（U）
        self.mode = "paper"
        self.live = None
        self.paused = False
        self.events = collections.deque(maxlen=300)   # 信号/开平仓记录（网页上看）
        self.logs = collections.deque(maxlen=300)
        self.progress = {"stage": "启动中", "done": 0, "total": 0}
        self.ready = False
        self.last_candle = 0
        self.lock = asyncio.Lock()
        self.foreign: set = set()               # 欧易账户上不是本程序开的仓（不碰、也不再开这些币）
        self.ws_ok = False                      # 持仓币的实时价格推送（WebSocket）连着没有
        self.ws_ts = 0
        self.strats: dict[str, sb_loader.Strategy] = {}
        self.load_errors: dict = {}
        self._load_strategies()
        self._load_state()

    # ------------------------------------------------------------ 设置 / 状态
    def say(self, msg, level="info"):
        line = time.strftime("%m-%d %H:%M:%S ") + msg
        self.logs.appendleft(line)
        getattr(log, level)(msg)

    def event(self, kind, strat, inst, text):
        self.events.appendleft({"t": now_ms(), "kind": kind, "strat": strat, "coin": coin_of(inst) if inst else "", "text": text})

    def _load_strategies(self):
        for name, sc in self.cfg.get("strategies", {}).items():
            try:
                st = sb_loader.Strategy(name)
                if st.unsupported:
                    raise RuntimeError("用到了本程序不支持的功能：" + "、".join(st.unsupported))
                self.strats[name] = st
            except Exception as e:  # noqa: BLE001
                self.load_errors[name] = str(e)
                self.say(f"策略 {name} 加载失败：{e}", "error")

    def scfg(self, name):
        d = {"enabled": False, "max_open": 10, "capital": 1000.0}
        d.update(self.cfg.get("strategies", {}).get(name, {}))
        return d

    def save_cfg(self):
        tmp = self.cfg_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.cfg_path)

    def _load_state(self):
        if not os.path.exists(STATE):
            return
        try:
            s = json.load(open(STATE, encoding="utf-8"))
            self.trades = [sb_engine.Trade.from_dict(d) for d in s.get("trades", [])]
            self.ledger = s.get("ledger", {})
            self.mode = s.get("mode", "paper")
            self.paused = s.get("paused", False)
        except Exception as e:  # noqa: BLE001
            self.say(f"读取 sb_state.json 失败：{e}（当作没有持仓）", "error")

    def save_state(self):
        s = {"trades": [t.to_dict() for t in self.trades], "ledger": self.ledger, "mode": self.mode,
             "paused": self.paused, "saved": now_ms()}
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False)
        os.replace(tmp, STATE)

    def ledger_key(self, name):
        return f"{self.mode}:{name}"

    # ------------------------------------------------------------ 启动
    async def start(self):
        self.okx = Okx(self.proxy)
        if self.mode == "live":
            try:
                await self._connect_live()
                await self._reconcile()
            except Exception as e:  # noqa: BLE001
                self.say(f"实盘连接失败：{e}。先暂停开新单，网页上看清楚再处理", "error")
                self.paused = True
        while True:
            try:
                await self._load_market()
                break
            except Exception as e:  # noqa: BLE001
                self.progress = {"stage": f"连不上欧易（{e}），10 秒后重试；检查 .env 里的 PROXY_URL", "done": 0, "total": 0}
                self.say(self.progress["stage"], "error")
                await asyncio.sleep(10)
        self.ready = True
        self.progress = {"stage": "运行中", "done": len(self.coins), "total": len(self.coins)}
        await asyncio.gather(self._price_loop(), self._candle_loop(), self._ws_loop())

    def _coin_list(self, swaps):
        mode = self.cfg.get("coins", "backtest")
        if mode == "all":
            day = 86_400_000
            min_age = float(self.cfg.get("min_age_days", 30))
            out = [i for i, x in swaps.items() if x["state"] == "live" and now_ms() - x["listTime"] > min_age * day]
        else:
            out = [inst_of(c) for c in self.cfg.get("coin_list", []) if inst_of(c) in swaps and swaps[inst_of(c)]["state"] == "live"]
        return sorted(out)

    def _need_bars(self):
        st = [self.strats[n] for n in self.strats if self.scfg(n)["enabled"]] or list(self.strats.values())
        return max([int(self.cfg.get("history_bars", 1000))] + [s.startup + 300 for s in st])

    async def _load_market(self):
        import pandas as pd
        self.progress = {"stage": "读取欧易合约列表", "done": 0, "total": 0}
        self.inst = await self.okx.swaps()
        self.coins = self._coin_list(self.inst)
        for t in self.trades:                       # 有持仓的币一定要看着
            if t.pair not in self.coins and t.pair in self.inst:
                self.coins.append(t.pair)
        n = self._need_bars()
        self.progress = {"stage": f"下载 {len(self.coins)} 个币的历史K线（每个 {n} 根，第一次大约 2~3 分钟）", "done": 0, "total": len(self.coins)}

        gate = asyncio.Semaphore(4)                 # 一次下 4 个币（进度条能走起来，也不会撞欧易限频）

        async def one(i):
            async with gate:
                rows = await self.okx.candles(i, "5m", n)
            df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
            df["date"] = pd.to_datetime(df["date"], unit="ms", utc=True)
            self.frames[i] = sb_loader.fill_missing(df, 5) if len(df) else df
            self.progress["done"] += 1

        await asyncio.gather(*(one(i) for i in self.coins), return_exceptions=True)
        bad = [i for i in self.coins if i not in self.frames]
        if bad:
            self.say(f"{len(bad)} 个币K线下载失败，稍后自动补：{', '.join(coin_of(i) for i in bad[:10])}", "warning")
        self.progress["stage"] = "计算信号"
        await asyncio.to_thread(self._analyze_all)
        self.last_candle = (now_ms() // 300_000) * 300_000 - 300_000     # 启动时那根K线的信号不追（等下一根收盘）
        self.say(f"准备好了：{len(self.frames)} 个币，策略 {', '.join(n for n in self.strats if self.scfg(n)['enabled']) or '（都没勾选）'}")

    # ------------------------------------------------------------ K线
    async def _candle_loop(self):
        while True:
            now = time.time()
            nxt = (int(now) // 300 + 1) * 300 + 3            # 每根 5 分钟K线收盘后 3 秒
            await asyncio.sleep(max(1.0, nxt - now))
            try:
                await self._update_candles()
                await asyncio.to_thread(self._analyze_all)
                async with self.lock:
                    await self._entries()
            except Exception as e:  # noqa: BLE001
                self.say(f"K线更新出错：{e}", "error")
                log.debug(traceback.format_exc())

    async def _update_candles(self):
        import pandas as pd
        want = (now_ms() // 300_000) * 300_000 - 300_000      # 最新一根已收盘K线的开盘时间

        async def one(i):
            df = self.frames.get(i)
            if df is None or df.empty:
                rows = await self.okx.candles(i, "5m", self._need_bars())
                new = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
            else:
                last = int(df["date"].iloc[-1].timestamp() * 1000)
                gap = int((want - last) // 300_000)
                if gap <= 0:
                    return
                rows = await (self.okx.recent(i, "5m", min(gap + 2, 299)) if gap < 290 else self.okx.candles(i, "5m", self._need_bars()))
                new = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
            if new.empty:
                return
            new["date"] = pd.to_datetime(new["date"], unit="ms", utc=True)
            d = new if df is None or df.empty else pd.concat([df, new[new["date"] > df["date"].iloc[-1]]], ignore_index=True)
            d = sb_loader.fill_missing(d, 5).iloc[-self._need_bars():].reset_index(drop=True)
            self.frames[i] = d

        for k in range(3):                     # 欧易偶尔晚一两秒才给收盘K线：没拿到的再补两次
            await asyncio.gather(*(one(i) for i in self.coins), return_exceptions=True)
            late = [i for i in self.coins if i in self.frames and len(self.frames[i]) and
                    int(self.frames[i]["date"].iloc[-1].timestamp() * 1000) < want]
            if not late:
                break
            await asyncio.sleep(3)
        self.last_candle = want

    def _analyze_all(self):
        """每个币每个勾选的策略：算到最新一根收盘K线的信号"""
        for name, st in self.strats.items():
            if not self.scfg(name)["enabled"] and not any(t.strat == name for t in self.trades):
                continue
            for i, df in list(self.frames.items()):
                if df is None or len(df) <= st.startup:          # 新上的币K线不够预热：不做（和回测一样）
                    self.sig.pop((name, i), None)
                    continue
                try:
                    a = st.analyze(df, f"{coin_of(i)}/USDT:USDT")
                    last = a.iloc[-1]
                    self.sig[(name, i)] = {"ts": int(last["date"].timestamp() * 1000), "enter": int(last["enter_long"]),
                                           "exit": int(last["exit_long"])}
                except Exception as e:  # noqa: BLE001
                    self.sig.pop((name, i), None)
                    self.say(f"{name} 算 {coin_of(i)} 出错：{e}", "error")

    # ------------------------------------------------------------ 开仓
    def strat_money(self, name):
        """(本金+已实现盈亏, 在用资金, 空位)"""
        c = self.scfg(name)
        realized = self.ledger.get(self.ledger_key(name), 0.0)
        used = sum(t.stake for t in self.trades if t.strat == name)
        n_open = sum(1 for t in self.trades if t.strat == name)
        return float(c["capital"]) + realized, used, int(c["max_open"]) - n_open

    def brake_hit(self):
        lim = float(self.cfg.get("daily_loss_limit_pct", 0) or 0)
        if lim <= 0:
            return False
        day0 = (now_ms() // 86_400_000) * 86_400_000
        pnl = sum(r.get("pnl", 0) for r in self.closed_since(day0))
        cap = sum(float(self.scfg(n)["capital"]) for n in self.strats if self.scfg(n)["enabled"]) or 1
        return pnl < -cap * lim / 100

    async def _entries(self):
        if self.paused:
            return
        if self.brake_hit():
            self.event("brake", "", "", f"今天已亏超过 {self.cfg.get('daily_loss_limit_pct')}%，今天不再开新单")
            return
        want = self.last_candle
        held = {t.pair for t in self.trades} | (self.foreign if self.mode == "live" else set())
        for name, st in self.strats.items():
            if not self.scfg(name)["enabled"]:
                continue
            for i in self.coins:
                s = self.sig.get((name, i))
                if not s or s["ts"] != want or not s["enter"] or s["exit"]:
                    continue
                if i in held:
                    self.event("skip", name, i, "已经有这个币的仓位（本程序或你手动开的），不重复开")
                    continue
                total, used, free_slots = self.strat_money(name)
                if free_slots <= 0:
                    self.event("skip", name, i, f"{name} 已经开满 {self.scfg(name)['max_open']} 单")
                    continue
                stake = (total - used) / free_slots
                price = self.prices.get(i)
                if not price:
                    self.event("skip", name, i, "还没拿到最新价")
                    continue
                ok = await self._open(name, st, i, price, stake)
                if ok:
                    held.add(i)
        self.save_state()

    def contracts_for(self, i, stake, price):
        x = self.inst.get(i) or {}
        ct, lot, mn = x.get("ctVal") or 1, x.get("lotSz") or 1, x.get("minSz") or 0
        n = int(stake / (price * ct) / lot + 1e-9) * lot
        return round(n, 8), mn, ct

    async def _open(self, name, st, i, price, stake):
        n, mn, ct = self.contracts_for(i, stake, price)
        if n < mn or n <= 0:
            self.event("skip", name, i, f"每单资金 {stake:.2f}U 不够欧易最小下单（{mn} 张 ≈ {mn * ct * price:.2f}U），本金加大一点才做得了")
            return False
        tick = (self.inst.get(i) or {}).get("tick")
        if self.mode == "paper":
            fee, slip = float(self.cfg.get("paper_fee", 0.0005)), float(self.cfg.get("paper_slippage", 0.0002))
            fill = price * (1 + slip)
            t = sb_engine.Trade(i, name, now_ms(), fill, n * ct, fee, fee, tick=tick, stake=n * ct * fill)
            t.contracts = n
        else:
            if not self.live:
                self.event("error", name, i, "实盘没连上，不开")
                return False
            fee = float(self.cfg.get("live_fee", 0.0005))
            stop = price * (1 - abs(st.stoploss) - 0.01)            # 交易所保护止损：比策略止损再远 1%
            try:
                total, avail = await asyncio.to_thread(self.live.account)
                if avail < n * ct * price * 1.02:
                    self.event("skip", name, i, f"账户可用保证金 {avail:.2f}U 不够这单 {n * ct * price:.2f}U")
                    return False
                await asyncio.to_thread(self.live.prepare, i, 1, self.cfg.get("margin_mode", "isolated"))
                r = await asyncio.to_thread(self.live.open, i, 1, n, stop, None, self.cfg.get("margin_mode", "isolated"))
            except Exception as e:  # noqa: BLE001
                self.event("error", name, i, f"下单失败：{e}")
                self.say(f"{coin_of(i)} 开仓失败：{e}", "error")
                sb_notify.push(f"策略机 开仓失败 {coin_of(i)}", str(e))
                return False
            fill = r["fill"] or price
            t = sb_engine.Trade(i, name, now_ms(), fill, r["contracts"] * ct, fee, fee, tick=tick, stake=r["contracts"] * ct * fill)
            t.contracts, t.order_id, t.algo_id = r["contracts"], r.get("order_id", ""), r.get("algo_id", "")
        t.adjust_stop_loss(t.open_rate, st.stoploss, initial=True)
        self.trades.append(t)
        self.save_state()
        msg = f"开多 {coin_of(i)} {t.contracts:g} 张 @ {t.open_rate:.6g}（{t.stake:.2f}U），止损 {t.stop_loss:.6g}"
        self.event("open", name, i, msg)
        self.say(f"[{'模拟' if self.mode == 'paper' else '实盘'}] {name} {msg}")
        sb_notify.push(f"策略机 开多 {coin_of(i)}", f"{name}\n{msg}")
        return True

    # ------------------------------------------------------------ 平仓
    async def _price_loop(self):
        while True:
            try:
                p = await self.okx.tickers()
                self.prices.update(p)
                self.price_ts = now_ms()
                async with self.lock:
                    await self._exits()
            except Exception as e:  # noqa: BLE001
                if now_ms() - self.price_ts > 60_000:
                    self.say(f"最新价拿不到超过 1 分钟：{e}（实盘时交易所有保护止损）", "warning")
            await asyncio.sleep(float(self.cfg.get("price_interval_s", 2)))

    async def _ws_loop(self):
        """有持仓的币订阅欧易实时价格推送：价格一变就检查要不要平（移动止盈只回落 0.1%，每 2 秒看一次会晚）。
        断了 5 秒后重连；推送断的时候还有每 2 秒一次的价格兜底。"""
        import websockets
        urls = ["wss://ws.okx.com/ws/v5/public", "wss://ws.okx.com:8443/ws/v5/public"]   # 有的代理不放行 8443 端口：两个轮流试
        k = 0
        while True:
            url = urls[k % len(urls)]
            try:
                kw = {"ping_interval": 20, "open_timeout": 10, "max_size": 2 ** 22}
                if self.proxy:
                    kw["proxy"] = self.proxy
                async with websockets.connect(url, **kw) as ws:
                    subbed: set = set()
                    self.ws_ok = True
                    while True:
                        want = {t.pair for t in self.trades}
                        add, rem = want - subbed, subbed - want
                        if add:
                            await ws.send(json.dumps({"op": "subscribe", "args": [{"channel": "tickers", "instId": i} for i in sorted(add)]}))
                            subbed |= add
                        if rem:
                            await ws.send(json.dumps({"op": "unsubscribe", "args": [{"channel": "tickers", "instId": i} for i in sorted(rem)]}))
                            subbed -= rem
                        try:
                            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
                        except asyncio.TimeoutError:
                            continue
                        if msg.get("arg", {}).get("channel") != "tickers" or "data" not in msg:
                            continue
                        for d in msg["data"]:
                            i, px = d.get("instId"), float(d.get("last") or 0)
                            if not px:
                                continue
                            self.prices[i] = px
                            self.ws_ts = now_ms()
                            if any(t.pair == i for t in self.trades):
                                async with self.lock:
                                    await self._exits(only=i)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                if self.ws_ok:
                    self.say(f"实时价格推送断了（{e}），5 秒后重连；这期间每 2 秒查一次价格", "warning")
                self.ws_ok = False
                k += 1
                await asyncio.sleep(5)

    async def _exits(self, only=None):
        for t in list(self.trades):
            if only and t.pair != only:
                continue
            if t not in self.trades:              # 等锁的时候已经被平掉了
                continue
            st = self.strats.get(t.strat)
            px = self.prices.get(t.pair)
            if st is None or not px:
                continue
            s = self.sig.get((t.strat, t.pair)) or {}
            reason = sb_engine.check_exit_live(st, t, px, now_ms(), s.get("enter", 0), s.get("exit", 0))
            if reason:
                await self.close(t, px, reason)

    REASON = {"stop_loss": "止损", "trailing_stop_loss": "移动止盈", "roi": "到时间/到目标平仓", "exit_signal": "卖出信号",
              "manual": "手动平仓", "exchange": "交易所已平（保护止损触发或在 App 里平的）"}

    async def close(self, t, price, reason, real=None):
        if self.mode == "paper" or reason == "exchange":
            slip = float(self.cfg.get("paper_slippage", 0.0002)) if self.mode == "paper" else 0
            exit_px = price * (1 - slip)
            pnl = t.profit_abs(exit_px)
            fee_info = ""
            if real:                                   # 交易所已平：用欧易记的真实盈亏
                pnl, exit_px = real["pnl"], real.get("exit") or exit_px
                fee_info = f"（欧易记账：手续费 {real['fee']:.4f}，资金费 {real['funding']:.4f}）"
        else:
            try:
                exit_px = await asyncio.to_thread(self.live.close, t.pair, 1, t.contracts, t.algo_id,
                                                  self.cfg.get("margin_mode", "isolated")) or price
            except Exception as e:  # noqa: BLE001
                self.event("error", t.strat, t.pair, f"平仓失败：{e}（下个 2 秒再试）")
                self.say(f"{coin_of(t.pair)} 平仓失败：{e}", "error")
                sb_notify.push(f"策略机 平仓失败 {coin_of(t.pair)}", str(e))
                return
            pnl = t.profit_abs(exit_px)
            fee_info = ""
            try:
                real = await asyncio.to_thread(self.live.closed_pnl, t.pair, t.open_time - 5000)
                if real:
                    pnl = real["pnl"]
                    exit_px = real["exit"] or exit_px
                    fee_info = f"（欧易记账：手续费 {real['fee']:.4f}，资金费 {real['funding']:.4f}）"
            except Exception:  # noqa: BLE001
                pass
        self.trades.remove(t)
        k = self.ledger_key(t.strat)
        self.ledger[k] = self.ledger.get(k, 0.0) + pnl
        rec = {"mode": self.mode, "strat": t.strat, "coin": coin_of(t.pair), "open_ts": t.open_time, "close_ts": now_ms(),
               "open": t.open_rate, "close": exit_px, "stake": t.stake, "pnl": round(pnl, 6),
               "ret": round(pnl / t.stake, 6) if t.stake else 0, "reason": reason, "contracts": t.contracts}
        with open(TRADES, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.save_state()
        msg = (f"平 {coin_of(t.pair)} @ {exit_px:.6g}，{self.REASON.get(reason, reason)}，"
               f"盈亏 {pnl:+.3f}U（{rec['ret'] * 100:+.2f}%），拿了 {(rec['close_ts'] - t.open_time) / 60000:.0f} 分钟{fee_info}")
        self.event("close", t.strat, t.pair, msg)
        self.say(f"[{'模拟' if self.mode == 'paper' else '实盘'}] {t.strat} {msg}")
        sb_notify.push(f"策略机 {'赚' if pnl >= 0 else '亏'} {pnl:+.2f}U {coin_of(t.pair)}", f"{t.strat}\n{msg}")

    # ------------------------------------------------------------ 实盘
    async def _connect_live(self):
        if not (self.keys["key"] and self.keys["secret"] and self.keys["passphrase"]):
            raise RuntimeError(".env 里没有欧易 API")
        import sb_live
        self.live = await asyncio.to_thread(sb_live.OkxLive, self.keys, self.proxy)

    async def _reconcile(self):
        """重启后对账：程序记着的仓位，交易所上已经没了 → 记成"交易所已平"（读欧易的真实盈亏）"""
        pos = await asyncio.to_thread(self.live.positions)
        for t in list(self.trades):
            if t.pair not in pos:
                real = None
                try:
                    real = await asyncio.to_thread(self.live.closed_pnl, t.pair, t.open_time - 5000)
                except Exception:  # noqa: BLE001
                    pass
                px = (real or {}).get("exit") or self.prices.get(t.pair) or t.open_rate
                await self.close(t, px, "exchange", real)
        mine = {t.pair for t in self.trades}
        others = [coin_of(i) for i in pos if i not in mine]
        self.foreign = set(pos) - mine
        if others:
            self.say(f"欧易账户上还有不是本程序开的仓：{', '.join(others)}（本程序不会动它们，也不会再开这些币）")
        self.save_state()

    async def set_mode(self, mode, confirm=""):
        if mode == self.mode:
            return "已经是这个模式"
        if self.trades:
            raise ValueError(f"{'模拟盘' if self.mode == 'paper' else '实盘'}还有 {len(self.trades)} 单没平，先平掉再切换")
        if mode == "live":
            if not self.allow_live:
                raise ValueError("实盘没打开：.env 里 SB_ALLOW_LIVE=1 并重启才行")
            if confirm.strip() != LIVE_CONFIRM:
                raise ValueError(f"要切到实盘，请在框里输入：{LIVE_CONFIRM}")
            await self._connect_live()
            await self._reconcile()
        self.mode = mode
        self.save_state()
        self.say(f"切换到{'实盘' if mode == 'live' else '模拟盘'}")
        return "ok"

    # ------------------------------------------------------------ 网页用
    def closed_since(self, since_ms=0, mode=None):
        out = []
        if os.path.exists(TRADES):
            for line in open(TRADES, encoding="utf-8"):
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("close_ts", 0) >= since_ms and r.get("mode") == (mode or self.mode):
                    out.append(r)
        return out

    @staticmethod
    def stats(rows):
        n = len(rows)
        gp = sum(r["pnl"] for r in rows if r["pnl"] > 0)
        gl = -sum(r["pnl"] for r in rows if r["pnl"] < 0)
        return {"n": n, "win": round(sum(r["pnl"] > 0 for r in rows) / n, 3) if n else 0,
                "pnl": round(gp - gl, 4), "pf": round(gp / gl, 2) if gl > 0 else (None if not gp else 99)}

    def snapshot(self):
        now = now_ms()
        opens = []
        for t in self.trades:
            px = self.prices.get(t.pair) or t.open_rate
            st = self.strats.get(t.strat)
            roi_left = None
            if st:
                keys = sorted(k for k, v in st.minimal_roi.items() if v <= -1)
                if keys:
                    roi_left = max(0, keys[0] - (now - t.open_time) / 60000)
            opens.append({"coin": coin_of(t.pair), "strat": t.strat, "open_ts": t.open_time, "open": t.open_rate,
                          "price": px, "ret": round(t.ratio(px), 5), "pnl": round(t.profit_abs(px), 4), "stake": round(t.stake, 2),
                          "stop": t.stop_loss, "trailing": t.is_stop_loss_trailing, "contracts": t.contracts,
                          "mins": round((now - t.open_time) / 60000), "time_exit_in": None if roi_left is None else round(roi_left)})
        day0 = (now // 86_400_000) * 86_400_000
        strategies = []
        for name in self.cfg.get("strategies", {}):
            c = self.scfg(name)
            st = self.strats.get(name)
            total, used, free = self.strat_money(name) if st else (0, 0, 0)
            strategies.append({"name": name, "enabled": c["enabled"], "max_open": c["max_open"], "capital": c["capital"],
                               "equity": round(total, 2), "used": round(used, 2), "open": c["max_open"] - free if st else 0,
                               "rules": st.settings_text() if st else None, "error": self.load_errors.get(name),
                               "title": c.get("title", name), "note": c.get("note", "")})
        signals_now = sum(1 for k, s in self.sig.items() if s.get("ts") == self.last_candle and s.get("enter") and not s.get("exit"))
        return {"ready": self.ready, "progress": self.progress, "mode": self.mode, "allow_live": self.allow_live,
                "has_keys": bool(self.keys["key"]), "paused": self.paused, "coins": len(self.coins),
                "last_candle": self.last_candle, "price_age_s": round((now - self.price_ts) / 1000) if self.price_ts else None,
                "open": opens, "strategies": strategies, "events": list(self.events)[:80], "logs": list(self.logs)[:60],
                "today": self.stats(self.closed_since(day0)), "week": self.stats(self.closed_since(now - 7 * 86_400_000)),
                "all": self.stats(self.closed_since(0)), "signals_now": signals_now,
                "brake": self.cfg.get("daily_loss_limit_pct", 0), "coins_mode": self.cfg.get("coins", "backtest"),
                "ws_ok": self.ws_ok, "ws_age_s": round((now - self.ws_ts) / 1000) if self.ws_ts else None,
                "live_confirm": LIVE_CONFIRM}
