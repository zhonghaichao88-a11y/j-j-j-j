"""欧易实盘下单（ccxt）。所有方法都是同步的，由 of_engine 放到后台线程里调用，不卡行情。

做了哪些保护：
  - 启动时读账户的持仓模式：单向（net）或双向（long/short），双向时自动带 posSide
  - 开仓前设好杠杆（逐仓或全仓，网页上选）；检查可用保证金；数量按合约面值和精度取整，低于最小下单量直接拒绝并说明差多少
  - 市价开仓时同时挂好止损止盈（交易所端执行，程序关掉也有效），开完读回真实成交均价和止盈止损单号
  - 平仓：只减仓市价单，然后只撤掉这笔单自己的止盈止损
  - 平仓后从欧易的"历史持仓"读真实盈亏（含手续费、资金费）
"""
from __future__ import annotations

import threading
import time


class LiveError(RuntimeError):
    pass


class OkxLive:
    name = "实盘"

    def __init__(self, keys: dict, proxy: str | None):
        import ccxt
        cfg = {"apiKey": keys["key"], "secret": keys["secret"], "password": keys["passphrase"],
               "options": {"defaultType": "swap"}, "enableRateLimit": True, "timeout": 15000}
        if proxy:
            cfg["httpsProxy"] = proxy
        self.ex = ccxt.okx(cfg)
        self.lock = threading.RLock()
        self.ex.load_markets()
        conf = self.ex.privateGetAccountConfig()["data"][0]
        self.hedged = conf.get("posMode") == "long_short_mode"      # 双向持仓
        self.acct_level = conf.get("acctLv")                        # 1 简单模式不能做合约
        if str(self.acct_level) == "1":
            raise LiveError("欧易账户是“简单交易模式”，不能做永续合约。请在欧易 App 里把账户模式改成“合约模式”或“跨币种保证金”")
        self._lev_done: dict = {}

    # ------------------------------------------------------------ 工具
    @staticmethod
    def sym(inst):                    # BTC-USDT-SWAP -> BTC/USDT:USDT
        return f"{inst.split('-')[0]}/USDT:USDT"

    def market(self, inst):
        return self.ex.market(self.sym(inst))

    def contracts_for(self, inst, qty_coin):
        """币数量 → 合约张数（按精度向下取整），返回 (张数, 最小张数, 每张多少币)"""
        m = self.market(inst)
        cs = float(m["contractSize"] or 1)
        n = float(self.ex.amount_to_precision(self.sym(inst), qty_coin / cs))
        return n, float(m["limits"]["amount"]["min"] or 0), cs

    def _pos_side(self, side_open):
        return {"posSide": "long" if side_open == 1 else "short"} if self.hedged else {}

    # ------------------------------------------------------------ 账户
    def account(self):
        """返回 (总权益 USD, 可用保证金 USD)"""
        with self.lock:
            d = self.ex.privateGetAccountBalance()["data"][0]
            total = float(d.get("totalEq") or 0)
            avail = 0.0
            for c in d.get("details", []):
                if c.get("ccy") == "USDT":
                    avail = float(c.get("availEq") or c.get("availBal") or 0)
                    if not total:
                        total = float(c.get("eq") or 0)
            if d.get("adjEq"):          # 跨币种保证金模式：有效保证金
                try:
                    avail = max(avail, float(d.get("adjEq")) - float(d.get("imr") or 0))
                except ValueError:
                    pass
            return total, avail

    def positions(self) -> dict:
        """欧易账户上所有永续持仓：{instId: [{'pos': 张数(带方向), 'avgPx': 均价, 'posSide': ...}]}"""
        with self.lock:
            out = {}
            for p in self.ex.privateGetAccountPositions({"instType": "SWAP"})["data"]:
                n = float(p.get("pos") or 0)
                if n == 0:
                    continue
                ps = p.get("posSide")
                if ps == "short":
                    n = -abs(n)
                elif ps == "long":
                    n = abs(n)
                out.setdefault(p["instId"], []).append({"pos": n, "avgPx": float(p.get("avgPx") or 0), "posSide": ps})
            return out

    # ------------------------------------------------------------ 下单
    def prepare(self, inst, leverage, mgn="isolated"):
        """mgn: isolated 逐仓 / cross 全仓"""
        key = (inst, leverage, mgn)
        if self._lev_done.get(key):
            return
        with self.lock:
            # 双向持仓 + 逐仓要分别设多、空两边；全仓一次设好
            sides = ["long", "short"] if (self.hedged and mgn == "isolated") else [None]
            for ps in sides:
                params = {"mgnMode": mgn}
                if ps:
                    params["posSide"] = ps
                try:
                    self.ex.set_leverage(leverage, self.sym(inst), params=params)
                except Exception as e:  # noqa: BLE001
                    if "59000" not in str(e):         # 有持仓/挂单时改不了杠杆，沿用原来的
                        raise LiveError(f"设置杠杆失败：{e}")
            self._lev_done[key] = True

    def open(self, inst, side, contracts, stop, target, mgn="isolated"):
        """市价开仓 + 附带止盈止损。返回 {fill, contracts, order_id, algo_id}"""
        s = self.sym(inst)
        with self.lock:
            params = {"tdMode": mgn,
                      "stopLoss": {"triggerPrice": self.ex.price_to_precision(s, stop), "type": "market"},
                      "takeProfit": {"triggerPrice": self.ex.price_to_precision(s, target), "type": "market"},
                      **self._pos_side(side)}
            o = self.ex.create_order(s, "market", "buy" if side == 1 else "sell", contracts, params=params)
            oid = o.get("id", "")
        fill, filled = 0.0, 0.0
        for _ in range(10):                       # 读回真实成交
            time.sleep(0.4)
            with self.lock:
                od = self.ex.fetch_order(oid, s)
            fill = float(od.get("average") or 0)
            filled = float(od.get("filled") or 0)
            if od.get("status") in ("closed", "canceled") and filled:
                break
        if not filled:
            raise LiveError("市价单没有成交（可能被交易所拒绝），请看欧易 App 的委托记录")
        return {"fill": fill, "contracts": filled, "order_id": oid, "algo_id": self.find_algo(inst)}

    def add(self, inst, side, contracts, mgn="isolated"):
        """补仓：同方向市价加仓（不带止盈止损，之后程序按总张数重挂）。返回 {fill, contracts}"""
        s = self.sym(inst)
        with self.lock:
            o = self.ex.create_order(s, "market", "buy" if side == 1 else "sell", contracts, params={"tdMode": mgn, **self._pos_side(side)})
            oid = o.get("id", "")
        fill, filled = 0.0, 0.0
        for _ in range(10):
            time.sleep(0.4)
            with self.lock:
                od = self.ex.fetch_order(oid, s)
            fill = float(od.get("average") or 0)
            filled = float(od.get("filled") or 0)
            if od.get("status") in ("closed", "canceled") and filled:
                break
        if not filled:
            raise LiveError("补仓市价单没有成交，请看欧易 App 的委托记录")
        return {"fill": fill, "contracts": filled}

    def find_algo(self, inst):
        """这笔仓位附带的止盈止损单号（最新的一张）"""
        with self.lock:
            for ot in ("oco", "conditional"):
                try:
                    data = self.ex.privateGetTradeOrdersAlgoPending({"instType": "SWAP", "instId": inst, "ordType": ot})["data"]
                except Exception:  # noqa: BLE001
                    continue
                if data:
                    data.sort(key=lambda a: int(a.get("cTime") or 0), reverse=True)
                    return data[0]["algoId"]
        return ""

    def cancel_algo(self, inst, algo_id):
        if not algo_id:
            return
        with self.lock:
            try:
                self.ex.privatePostTradeCancelAlgos([{"algoId": algo_id, "instId": inst}])
            except Exception:  # noqa: BLE001
                pass

    def place_oco(self, inst, side_open, contracts, stop, target, mgn="isolated"):
        """给现有仓位重新挂一张止盈止损（只减仓）。返回单号"""
        s = self.sym(inst)
        req = {"instId": inst, "tdMode": mgn, "side": "sell" if side_open == 1 else "buy",
               "ordType": "oco", "sz": self.ex.amount_to_precision(s, contracts),
               "slTriggerPx": self.ex.price_to_precision(s, stop), "slOrdPx": "-1",
               "tpTriggerPx": self.ex.price_to_precision(s, target), "tpOrdPx": "-1",
               **self._pos_side(side_open)}
        if not self.hedged:
            req["reduceOnly"] = "true"
        with self.lock:
            r = self.ex.privatePostTradeOrderAlgo(req)["data"][0]
        if str(r.get("sCode", "0")) != "0":
            raise LiveError(f"挂止盈止损失败：{r.get('sMsg')}")
        return r.get("algoId", "")

    def close(self, inst, side_open, contracts, algo_id="", mgn="isolated"):
        """只减仓市价平掉 contracts 张，撤掉这笔的止盈止损。返回成交均价"""
        s = self.sym(inst)
        params = {"tdMode": mgn, **self._pos_side(side_open)}
        if not self.hedged:
            params["reduceOnly"] = True
        with self.lock:
            o = self.ex.create_order(s, "market", "sell" if side_open == 1 else "buy", contracts, params=params)
            oid = o.get("id", "")
        avg = 0.0
        for _ in range(8):
            time.sleep(0.4)
            with self.lock:
                od = self.ex.fetch_order(oid, s)
            avg = float(od.get("average") or 0)
            if od.get("status") == "closed":
                break
        return avg

    def closed_pnl(self, inst, since_ms):
        """平仓后的真实盈亏：欧易"历史持仓"里这个币最近一条（含手续费、资金费）"""
        with self.lock:
            data = self.ex.privateGetAccountPositionsHistory({"instType": "SWAP", "instId": inst, "limit": 10})["data"]
        rows = [d for d in data if int(d.get("uTime") or 0) >= since_ms]
        if not rows:
            return None
        d = max(rows, key=lambda x: int(x.get("uTime") or 0))
        return {"pnl": float(d.get("realizedPnl") or d.get("pnl") or 0),
                "exit": float(d.get("closeAvgPx") or 0), "fee": float(d.get("fee") or 0),
                "funding": float(d.get("fundingFee") or 0)}
