"""ALPHA-X ULTRA MAX 5.5 独立 OKX 执行层。
默认禁止生产交易。只允许 USDT SWAP，并要求独立总开关。
"""
from __future__ import annotations
import math, os, time, uuid
from typing import Dict, Any, Optional
import ccxt
from loguru import logger
from config import config, symbol_to_ccxt


def _safe_num(v, default=0.0):
    """把交易所返回值安全转 float：兼容 str/int/float/np 标量；个别小币种字段可能是 dict/list，
    递归取常见数值键，取不到就回退 default，绝不让 float(dict) 中断开仓链路。"""
    try:
        if v is None:
            return default
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, dict):
            for kk in ("value", "val", "price", "amount", "min", "max", "ctVal", "contractSize", "tickSz", "lotSz"):
                if kk in v:
                    got = _safe_num(v[kk], None)
                    if got is not None:
                        return got
            return default
        if isinstance(v, (list, tuple)):
            return _safe_num(v[0], default) if v else default
        s = str(v).strip().rstrip("%")
        return float(s) if s else default
    except (TypeError, ValueError):
        return default


def _install_live_rate_limit_guard(exchange):
    import random
    import threading
    safe_methods = {
        "load_markets", "fetch_ticker", "fetch_ohlcv", "fetch_balance",
        "fetch_positions", "fetch_order", "fetch_open_orders", "fetch_my_trades",
        "fetch_closed_orders", "fetch_orders", "milliseconds",
        "privateGetAccountConfig", "private_get_account_config",
    }
    lock = threading.RLock()
    max_attempts = max(1, int(os.getenv("ALPHAX_OKX_RETRY_ATTEMPTS", "4")))
    base_delay = max(0.25, float(os.getenv("ALPHAX_OKX_BACKOFF_BASE", "1.0")))
    max_delay = max(base_delay, float(os.getenv("ALPHAX_OKX_BACKOFF_MAX", "12.0")))

    def retry_after(exc):
        headers = getattr(exc, "headers", None) or {}
        value = headers.get("Retry-After") or headers.get("retry-after")
        try: return max(0.0, min(max_delay, float(value))) if value is not None else None
        except (TypeError, ValueError): return None

    def retryable(exc):
        if isinstance(exc, (ccxt.RateLimitExceeded, ccxt.DDoSProtection, ccxt.NetworkError)):
            return True
        text = str(exc).lower()
        status = getattr(exc, "http_status", None) or getattr(exc, "status_code", None)
        return status == 429 or "429" in text or "too many requests" in text or "rate limit" in text

    for name in safe_methods:
        original = getattr(exchange, name, None)
        if not callable(original) or getattr(original, "_alphax_rate_guard", False): continue
        def guarded(*args, __orig=original, __name=name, **kwargs):
            last=None
            for attempt in range(max_attempts):
                try:
                    with lock:
                        return __orig(*args, **kwargs)
                except Exception as exc:
                    last=exc
                    if not retryable(exc) or attempt >= max_attempts-1: raise
                    ra=retry_after(exc)
                    delay=ra if ra is not None else min(max_delay, base_delay*(2**attempt))
                    delay*=0.80+random.random()*0.40
                    logger.warning(f"[OKX限流保护] 实盘{__name} 第{attempt+1}/{max_attempts}次失败，{delay:.2f}s后退避：{exc}")
                    time.sleep(delay)
            raise last
        guarded._alphax_rate_guard=True
        setattr(exchange,name,guarded)
    return exchange


class AlphaLiveExecutor:
    PREFIX="AX"
    TAG="ALPHAX"
    def __init__(self): self._exchange=None; self._markets={}
    @property
    def allowed(self): return os.getenv("ALPHA_LIVE_ALLOWED","0").lower() in {"1","true","yes","on"}
    def _base_url(self): return os.getenv("ALPHA_OKX_BASE_URL", config.okx.base_url).rstrip("/")
    def _ensure_exchange(self):
        if config.okx.is_demo: raise RuntimeError("OKX_DEMO_MODE=1，禁止 ALPHA-X 生产下单")
        if not config.okx.is_configured: raise RuntimeError("OKX API 凭证未配置")
        if config.trading.trading_type!="swap": raise RuntimeError("ALPHA-X ULTRA 只允许 SWAP")
        if self._exchange is None:
            base=self._base_url(); host=base.replace("https://","").replace("http://","").split("/")[0]
            params={"apiKey":config.okx.api_key,"secret":config.okx.api_secret,"password":config.okx.api_passphrase,
                    "hostname":host,"timeout":15000,"enableRateLimit":True,"options":{"defaultType":"swap"}}
            self._exchange=ccxt.okx(params)
            _install_live_rate_limit_guard(self._exchange)
            self._exchange.hostname=host
            proxy=getattr(config.okx,"proxy_url","") or ""
            if proxy:
                try: self._exchange.proxy=proxy
                except Exception: pass
            self._markets=self._exchange.load_markets()
        return self._exchange

    def _ensure(self):
        if not self.allowed: raise RuntimeError("ALPHA_LIVE_ALLOWED 未开启")
        return self._ensure_exchange()
    @staticmethod
    def _inst_id(cs: str) -> str:
        base, quote = cs.split("/")[0], cs.split("/")[1].split(":")[0]
        return f"{base}-{quote}-SWAP"
    def market(self, cs):
        ex=self._ensure(); m=(ex.markets or {}).get(cs)
        if not m or m.get("type")!="swap" or m.get("quote")!="USDT": raise RuntimeError(f"ALPHA-X 只允许 USDT 永续: {cs}")
        return m
    def pos_mode(self):
        ex=self._ensure(); fn=getattr(ex,"privateGetAccountConfig",None) or getattr(ex,"private_get_account_config",None)
        if fn is None: raise RuntimeError("当前 CCXT 不提供 OKX account/config 接口")
        r=fn({}); data=(r or {}).get("data",[]); mode=data[0].get("posMode","") if data else ""
        if mode not in ("net_mode","long_short_mode"): raise RuntimeError(f"无法确认 OKX 持仓模式: {mode}")
        return mode
    def account(self):
        """只读账户权益：不要求 ALPHA_LIVE_ALLOWED，直接读取 OKX account/balance。"""
        ex=self._ensure_exchange()
        raw=ex.request("account/balance","private","GET",{})
        rows=(raw or {}).get("data") or []
        if not rows: raise RuntimeError("OKX 账户余额接口返回为空")
        acct=rows[0] or {}
        total=float(acct.get("totalEq") or 0)
        free=float(acct.get("availEq") or 0)
        details=acct.get("details") or []
        us=next((x for x in details if str(x.get("ccy","")).upper()=="USDT"),{}) or {}
        usdt_total=float(us.get("cashBal") or us.get("eq") or 0)
        usdt_free=float(us.get("availEq") or us.get("availBal") or usdt_total)
        return {"free":free or usdt_free,"used":max(usdt_total-usdt_free,0.0),"total":total or usdt_total,
                "equity":total or usdt_total,"available_equity":free or usdt_free,"raw":raw}
    def clock_status(self, max_skew_ms: int = 3000) -> Dict[str, Any]:
        """Compare local clock with OKX public server time. Errors are not hidden."""
        ex=self._ensure_exchange()
        server_ms=None; source=""
        errors=[]
        try:
            fn=getattr(ex,"fetch_time",None)
            if callable(fn):
                server_ms=int(fn()); source="fetch_time"
        except Exception as exc:
            errors.append(str(exc))
        if server_ms is None:
            try:
                raw=ex.request("public/time","public","GET",{})
                row=((raw or {}).get("data") or [None])[0] or {}
                server_ms=int(row.get("ts"))
                source="public/time"
            except Exception as exc:
                errors.append(str(exc))
        if server_ms is None:
            return {"ok":False,"skew_ms":None,"server_ms":None,"source":source,"error":"无法获取OKX服务器时间: "+" | ".join(errors[-2:])}
        local_ms=int(time.time()*1000); skew=local_ms-server_ms
        return {"ok":abs(skew)<=int(max_skew_ms),"skew_ms":int(skew),"server_ms":server_ms,"local_ms":local_ms,"source":source,"max_skew_ms":int(max_skew_ms)}

    def positions(self):
        """Use one complete snapshot. Query failure is never proof of a flat account."""
        ex=self._ensure(); native_error=None
        try:
            raw=ex.request("account/positions","private","GET",{"instType":"SWAP"})
            if not isinstance(raw,dict) or str(raw.get("code"))!="0" or not isinstance(raw.get("data"),list):
                raise RuntimeError("原生持仓响应不完整或失败")
            out=[]
            for r in raw["data"]:
                inst=r.get("instId") or ""
                if not inst.endswith("-USDT-SWAP"): continue
                qty=float(r.get("pos") or 0)
                if qty==0: continue
                sym=inst[:-10]+"/USDT:USDT"
                side=r.get("posSide")
                if side not in ("long","short"): side="long" if qty>0 else "short"
                market=self.market(sym)
                ct=float(r.get("ctVal") or market.get("contractSize") or (market.get("info") or {}).get("ctVal") or 0)
                if ct<=0: raise RuntimeError("缺少合约面值")
                out.append(dict(symbol=sym,type="swap",side=side,contracts=abs(qty),entryPrice=float(r.get("avgPx") or 0),markPrice=float(r.get("markPx") or 0),contract_size=ct,closeOrderAlgo=r.get("closeOrderAlgo") or []))
            return out
        except Exception as exc:
            native_error=exc
        try:
            rows=ex.fetch_positions()
            if not isinstance(rows,list): raise RuntimeError("ccxt持仓响应不完整")
            out=[]
            for p in rows:
                sym=p.get("symbol") or "";info=p.get("info") or {}
                if not sym.endswith("/USDT:USDT"): continue
                qty=float(p.get("contracts") or info.get("pos") or 0)
                if qty==0: continue
                side=p.get("side")
                if side not in ("long","short"): side="long" if float(info.get("pos") or qty)>0 else "short"
                ct=float(p.get("contractSize") or self.market(sym).get("contractSize") or 0)
                if ct<=0: raise RuntimeError("缺少合约面值")
                out.append(dict(symbol=sym,type="swap",side=side,contracts=abs(qty),entryPrice=p.get("entryPrice"),markPrice=p.get("markPrice"),contract_size=ct,closeOrderAlgo=info.get("closeOrderAlgo") or p.get("closeOrderAlgo") or []))
            return out
        except Exception as exc:
            raise RuntimeError(f"持仓状态未知，不能认定空仓: native={native_error}; ccxt={exc}") from exc

    def recover_filled_entry(self, symbol, side, client_order_id):
        """Recover only the named parent order and its exact, active native protection."""
        import math
        ex=self._ensure();cs=symbol_to_ccxt(symbol,"swap")
        row=self.order_by_client_id(symbol,client_order_id) or {}
        if str(row.get("state")) in ("live","partially_filled"):
            ex.request("trade/cancel-order","private","POST",{"instId":self._inst_id(cs),"clOrdId":client_order_id})
            row=self.order_by_client_id(symbol,client_order_id) or {}
        if str(row.get("state")) not in ("filled","canceled"):
            raise RuntimeError("原始开仓订单未确认终态，禁止按全部成交恢复")
        if row.get("side") and row["side"]!=("buy" if side=="long" else "sell"):
            raise RuntimeError("原始开仓订单方向不匹配")
        qty=float(row.get("accFillSz") or 0);avg=float(row.get("avgPx") or 0)
        if not all(math.isfinite(x) and x>0 for x in (qty,avg)):
            raise RuntimeError("缺少实际成交数量或均价")
        attached=row.get("attachAlgoOrds") or []
        tp=next((x for x in attached if float(x.get("tpTriggerPx") or 0)>0),{})
        sl=next((x for x in attached if float(x.get("slTriggerPx") or 0)>0),{})
        ids=[str(x.get("attachAlgoClOrdId") or x.get("algoClOrdId") or "") for x in (tp,sl)]
        if not all(ids) or len(set(ids))!=2: raise RuntimeError("原始开仓订单缺少明确保护身份")
        status=self.protection_status(symbol,side,expected_ids=ids,wait_timeout=5.0)
        if not status.get("verified"): raise RuntimeError("恢复订单的原生保护未验证")
        by={self._algo_client_id(x):x for x in status["orders"]}
        tp_px=float(by[ids[0]].get("tpTriggerPx") or 0);sl_px=float(by[ids[1]].get("slTriggerPx") or 0)
        d=1 if side=="long" else -1
        if not all(math.isfinite(x) and x>0 for x in (tp_px,sl_px)) or d*(tp_px-avg)<=0 or d*(avg-sl_px)<=0:
            raise RuntimeError("恢复保护价格不符合实际成交方向")
        ct=float(self.market(cs).get("contractSize") or 0)
        if ct<=0: raise RuntimeError("无法核实恢复仓位合约面值")
        return dict(live=True,symbol=symbol,side=side,order_id=row.get("ordId"),client_order_id=client_order_id,filled=qty,average=avg,tp=tp_px,sl=sl_px,notional_usdt=qty*avg*ct,recovered=True,status=row["state"],tp_attach_clordid=ids[0],sl_attach_clordid=ids[1])

    def _ensure_leverage(self, cs, leverage):
        ex=self._ensure(); self.market(cs)
        try: ex.set_leverage(int(leverage),cs,params={"mgnMode":config.trading.margin_mode})
        except Exception as exc: raise RuntimeError(f"OKX 杠杆设置失败，已停止开仓: {exc}") from exc
    def _contracts(self,cs,notional,price):
        ex=self._ensure(); m=self.market(cs); ct=_safe_num(m.get("contractSize"), 0.0) or _safe_num((m.get("info") or {}).get("ctVal"), 0.0) or 1.0
        raw=float(notional)/(ct*float(price)); amount=float(ex.amount_to_precision(cs,raw)); min_amt=_safe_num((m.get("limits",{}).get("amount",{}) or {}).get("min"), 0.0)
        if amount<=0 or (min_amt and amount<min_amt): raise RuntimeError(f"下单张数 {amount} 小于最小量 {min_amt}")
        return amount
    def order_by_client_id(self, symbol:str, client_order_id:str) -> Optional[Dict[str,Any]]:
        """Query a main order by clOrdId; used for crash-safe idempotent recovery."""
        ex=self._ensure(); cs=symbol_to_ccxt(symbol,"swap")
        raw=ex.request("trade/order","private","GET",{"instId":self._inst_id(cs),"clOrdId":client_order_id})
        rows=(raw or {}).get("data") or []
        return rows[0] if rows else None
    def pending_orders(self, symbol:Optional[str]=None) -> list:
        ex=self._ensure(); params={"instType":"SWAP"}
        if symbol: params["instId"]=self._inst_id(symbol_to_ccxt(symbol,"swap"))
        raw=ex.request("trade/orders-pending","private","GET",params)
        return (raw or {}).get("data") or []
    def pending_algos(self, symbol:Optional[str]=None, ord_type:str="conditional") -> list:
        ex=self._ensure(); params={"instType":"SWAP","ordType":ord_type}
        if symbol: params["instId"]=self._inst_id(symbol_to_ccxt(symbol,"swap"))
        raw=ex.request("trade/orders-algo-pending","private","GET",params)
        return (raw or {}).get("data") or []

    @staticmethod
    def _close_side(side: str) -> str:
        return "sell" if str(side) == "long" else "buy"

    def _algo_base_params(self, cs: str, position_side: str) -> dict:
        mode=self.pos_mode(); hedged=mode=="long_short_mode"
        params={"tdMode":config.trading.margin_mode,"hedged":hedged,
                "posSide":position_side if hedged else "net"}
        if not hedged:
            params["reduceOnly"]=True
        return params

    def _amount_step(self, cs: str) -> float:
        m=self.market(cs); raw=(m.get("info") or {}).get("lotSz")
        step=_safe_num(raw, 0.0)
        if step <= 0:
            precision=(m.get("precision") or {}).get("amount")
            if isinstance(precision, int):
                step=10 ** (-precision)
            else:
                step=_safe_num(precision, 1.0)
        return max(float(step), 1e-12)

    def split_contracts(self, symbol: str, total: float, fractions) -> list:
        cs=symbol_to_ccxt(symbol,"swap"); ex=self._ensure(); self.market(cs)
        step=self._amount_step(cs)
        total=float(ex.amount_to_precision(cs,float(total)))
        raw=[total*float(x) for x in fractions]
        out=[math.floor(x/step + 1e-10)*step for x in raw]
        units=max(0,int(round((total-sum(out))/step)))
        order=sorted(range(len(raw)), key=lambda i: raw[i]/step-math.floor(raw[i]/step), reverse=True)
        for k in range(units):
            out[order[k % len(order)]] += step
        out=[float(ex.amount_to_precision(cs,x)) for x in out]
        if abs(sum(out)-total)>step/2:
            raise RuntimeError("分批数量拆分后与总张数不一致")
        return out

    def _require_expected(self, symbol, side, ids, timeout=5.0):
        status=self.protection_status_set(symbol,side,ids,wait_timeout=timeout)
        if not status.get("verified"):
            for cid in ids:
                try: self.cancel_algo(symbol,algo_cl_ord_id=cid)
                except Exception: pass
            raise RuntimeError(status.get("error") or "交易所原生订单未确认")
        return status

    def place_native_tp(self, symbol, side, contracts, trigger_price, client_id=None):
        cs=symbol_to_ccxt(symbol,"swap"); ex=self._ensure(); cid=client_id or self.PREFIX+uuid.uuid4().hex[:24]
        params={**self._algo_base_params(cs,side),"algoClOrdId":cid,
                "takeProfitPrice":float(trigger_price),"tpOrdPx":"-1","tpTriggerPxType":"last"}
        order=ex.create_order(cs,"conditional",self._close_side(side),float(contracts),None,params)
        status=self._require_expected(symbol,side,[cid],timeout=5.0)
        return {"id":order.get("id"),"client_id":cid,"order":order,"status":status}

    def place_native_sl(self, symbol, side, contracts, trigger_price, client_id=None, trigger_type="mark"):
        cs=symbol_to_ccxt(symbol,"swap"); ex=self._ensure(); cid=client_id or self.PREFIX+uuid.uuid4().hex[:24]
        params={**self._algo_base_params(cs,side),"algoClOrdId":cid,
                "stopLossPrice":float(trigger_price),"slOrdPx":"-1","slTriggerPxType":trigger_type}
        order=ex.create_order(cs,"conditional",self._close_side(side),float(contracts),None,params)
        status=self._require_expected(symbol,side,[cid],timeout=5.0)
        return {"id":order.get("id"),"client_id":cid,"order":order,"status":status}

    def place_native_trailing(self, symbol, side, contracts, callback_ratio, active_price=0, client_id=None):
        cs=symbol_to_ccxt(symbol,"swap"); ex=self._ensure(); cid=client_id or self.PREFIX+uuid.uuid4().hex[:24]
        params={**self._algo_base_params(cs,side),"algoClOrdId":cid,
                "trailingPercent":f"{float(callback_ratio)*100:.8g}"}
        if float(active_price or 0)>0:
            params["activePx"]=ex.price_to_precision(cs,float(active_price))
        order=ex.create_order(cs,"move_order_stop",self._close_side(side),float(contracts),None,params)
        status=self._require_expected(symbol,side,[cid],timeout=5.0)
        return {"id":order.get("id"),"client_id":cid,"order":order,"status":status}

    def place_position_protection(self, symbol, side, sl_price, tp_price, client_id=None):
        """整仓保护单（closeFraction=1）：触发时平掉该方向全部仓位，不随加仓/减仓改数量。
        同一仓位交易所只允许一张；挂上后按 algoClOrdId 精确验证，验证失败会撤掉并报错。"""
        ex=self._ensure(); cs=symbol_to_ccxt(symbol,"swap"); inst=self._inst_id(cs); self.market(cs)
        cid=client_id or self.PREFIX+uuid.uuid4().hex[:24]
        mode=self.pos_mode(); d=1 if side=="long" else -1
        sl=float(ex.price_to_precision(cs,float(sl_price))); tp=float(ex.price_to_precision(cs,float(tp_price)))
        if sl<=0 or tp<=0 or d*(tp-sl)<=0: raise ValueError("整仓保护价格无效")
        body={"instId":inst,"tdMode":config.trading.margin_mode,"side":self._close_side(side),
              "posSide":side if mode=="long_short_mode" else "net","ordType":"conditional","closeFraction":"1",
              "algoClOrdId":cid,"tag":self.TAG,
              "slTriggerPx":str(sl),"slOrdPx":"-1","slTriggerPxType":"last",
              "tpTriggerPx":str(tp),"tpOrdPx":"-1","tpTriggerPxType":"last"}
        if mode!="long_short_mode": body["reduceOnly"]=True
        raw=ex.request("trade/order-algo","private","POST",body)
        data=(raw or {}).get("data") or []; item=data[0] if data else {}
        if str((raw or {}).get("code"))!="0" or str(item.get("sCode","0"))!="0":
            raise RuntimeError("整仓保护单下单失败："+str(item.get("sMsg") or raw))
        status=self._require_expected(symbol,side,[cid],timeout=5.0)
        return {"client_id":cid,"algo_id":item.get("algoId"),"sl":sl,"tp":tp,"status":status}

    def add_to_position(self, symbol, side, notional_usdt, leverage, client_order_id=None):
        """同方向市价加仓（不附带 TP/SL；调用方必须先挂好整仓保护单）。只按交易所确认的成交记账。"""
        if side not in ("long","short"): raise ValueError("side 必须是 long/short")
        ex=self._ensure(); cs=symbol_to_ccxt(symbol,"swap"); self.market(cs); self._ensure_leverage(cs,leverage)
        bal=self.account(); free=float(bal["free"])
        if free<=0: raise RuntimeError("OKX 可用余额不足，不能加仓")
        tick=ex.fetch_ticker(cs); px=float((tick.get("ask") if side=="long" else tick.get("bid")) or tick.get("last") or 0)
        if px<=0: raise RuntimeError("无法取得有效价格")
        notional_usdt=min(float(notional_usdt),free*int(leverage)*.85); amount=self._contracts(cs,notional_usdt,px); mode=self.pos_mode()
        clid=client_order_id or self.PREFIX+uuid.uuid4().hex[:28]
        params={"tdMode":config.trading.margin_mode,"posSide":side if mode=="long_short_mode" else "net","clOrdId":clid,"tag":self.TAG}
        logger.warning(f"[ALPHA-X LIVE] {symbol} 加仓 {side} {amount}张 ≈ {notional_usdt:.2f}U clOrdId={clid}")
        order=ex.create_order(cs,"market","buy" if side=="long" else "sell",amount,None,params); oid=order.get("id")
        if not oid: raise RuntimeError(f"OKX 加仓返回无 ordId: {order}")
        detail=self.wait_order_terminal(symbol,oid,timeout=8); state=str(detail.get("status") or order.get("status") or "").lower()
        if state not in ("closed","canceled","rejected"):
            try: ex.cancel_order(str(oid),cs)
            except Exception: pass
            detail=self.wait_order_terminal(symbol,oid,timeout=5.0); state=str(detail.get("status") or "").lower()
        filled=float(detail.get("filled") or order.get("filled") or 0); avg=float(detail.get("average") or order.get("average") or 0)
        fee=self._fee_cost(detail) if detail.get("fee") is not None else self._fee_cost(order)
        ct=float(self.market(cs).get("contractSize") or 0)
        return {"order_id":oid,"client_order_id":clid,"filled":filled,"average":avg,"fee":fee,"status":state,
                "notional_usdt":filled*avg*ct if ct>0 else 0.0}

    def cancel(self,symbol,order_id):
        ex=self._ensure(); cs=symbol_to_ccxt(symbol,"swap")
        return ex.cancel_order(str(order_id),cs)
    def wait_order_terminal(self,symbol,order_id,timeout=8.0):
        ex=self._ensure(); cs=symbol_to_ccxt(symbol,"swap"); deadline=time.time()+float(timeout); detail={}
        while time.time()<deadline:
            try:
                detail=ex.fetch_order(str(order_id),cs) or detail
                state=str(detail.get("status") or "").lower(); filled=float(detail.get("filled") or 0)
                # Partial fills are not terminal.  Do not let a live/partially_filled
                # entry escape as a completed order.  The caller will reconcile/cancel
                # the residual and then manage only the actually filled quantity.
                if state in ("closed","canceled","rejected","expired"): return self._normalize_order(detail)
            except Exception: pass
            time.sleep(.35)
        return self._normalize_order(detail)
    @staticmethod
    def _fee_cost(order):
        fee=order.get('fee')
        if isinstance(fee,dict): return _safe_num(fee.get('cost'),0.0)
        if fee is not None: return _safe_num(fee,0.0)
        fees=order.get('fees') or []
        if fees: return sum(_safe_num(f.get('cost'),0.0) for f in fees if isinstance(f,dict))
        # OKX native fee is negative for charges, positive for rebates.
        raw=order.get('fillFee', (order.get('info') or {}).get('fee',0))
        return -_safe_num(raw,0.0)

    @staticmethod
    def _normalize_order(x):
        x=x or {}; info=x.get('info') or {}
        state=str(x.get('status') or x.get('state') or info.get('state') or '').lower()
        state={'filled':'closed','live':'open','partially_filled':'open','mmp_canceled':'canceled'}.get(state,state)
        filled=x.get('filled')
        if filled is None: filled=x.get('accFillSz',info.get('accFillSz',0))
        average=x.get('average')
        if average is None: average=x.get('avgPrice') or x.get('avgPx') or info.get('avgPx') or 0
        return {**x,'status':state,'filled':_safe_num(filled,0.0),'average':_safe_num(average,0.0),
                'pnl':_safe_num(x.get('pnl',x.get('fillPnl',0)),0.0),'fee':AlphaLiveExecutor._fee_cost(x)}

    def _cancel_confirm_entry(self,symbol,oid,clid):
        ex=self._ensure();cs=symbol_to_ccxt(symbol,'swap')
        try: ex.cancel_order(str(oid),cs)
        except Exception: pass  # 查询终态确认结果，撤单响应本身不是证明。
        detail=self.wait_order_terminal(symbol,oid,timeout=5.0)
        if detail.get('status') not in ('closed','canceled','rejected','expired'):
            try: detail=self._normalize_order(self.order_by_client_id(symbol,clid) or {})
            except Exception: pass
        if detail.get('status') not in ('closed','canceled','rejected','expired'):
            exc=RuntimeError('撤单/成交终态未确认，保留原订单编号等待对账，禁止重挂或转市价')
            exc.client_order_id=clid
            raise exc
        if detail.get('filled',0)>0 and detail.get('average',0)<=0:
            exc=RuntimeError('订单存在实际成交但均价未返回，禁止补发订单')
            exc.client_order_id=clid
            raise exc
        return detail

    def _entry_protection(self, ex, cs, side, price, tp_pct, sl_pct, protection=None):
        d=1 if side=='long' else -1
        tp=price*(1+d*tp_pct); sl=price*(1-d*sl_pct)
        if protection:
            tp=float(protection['tp']); sl=float(protection['sl'])
            ref=float(protection['reference']); risk=d*(ref-sl)
            if risk<=0 or abs(price-ref)>float(protection.get('max_chase_r',.25))*risk:
                raise ValueError('V6最新执行价偏离结构，不追价')
        tp=float(ex.price_to_precision(cs,tp)); sl=float(ex.price_to_precision(cs,sl))
        if d*(tp-price)<=0 or d*(price-sl)<=0:
            raise ValueError('TP/SL结构价格在最新报价或精度处理后无效')
        if protection:
            cost=float(protection['cost']); reward=d*(tp-price)/price; risk=d*(price-sl)/price
            if reward<float(protection.get('min_target_cost',2.5))*cost or (reward-cost)/(risk+cost)<float(protection.get('min_net_rr',1.1)):
                raise ValueError('V6执行报价扣费后空间不足')
        return tp,sl

    def _fallback_protection(self, symbol, side, contracts, tp_price, sl_price, attached_ids, rebased):
        """成交后附带的 TP/SL 在 OKX 上没出现（常见于限价单部分成交后撤单、或交易所延迟）：
        不直接平仓，改为按实际成交数量单独挂止盈单+止损单（只减仓），两张都按 ID 验证通过才算有保护。
        只处理“附带单没出现”这一种情况；价格越界等其他失败照旧返回 None（由调用方平仓）。"""
        if not str(rebased.get("error") or "").startswith("OKX 附加TP/SL尚未出现") or float(contracts or 0)<=0:
            return None
        tp_cid=self.PREFIX+uuid.uuid4().hex[:24]; sl_cid=self.PREFIX+uuid.uuid4().hex[:24]
        try:
            self.place_native_sl(symbol,side,contracts,sl_price,client_id=sl_cid,trigger_type="mark")
            self.place_native_tp(symbol,side,contracts,tp_price,client_id=tp_cid)
        except Exception as exc:
            for cid in (tp_cid,sl_cid):
                try: self.cancel_algo(symbol,algo_cl_ord_id=cid)
                except Exception: pass
            logger.warning(f"[ALPHA-X LIVE] {symbol} 单独补挂TP/SL失败：{exc}")
            return None
        for cid in attached_ids or []:          # 原附带单若之后才出现，撤掉避免重复（撤不掉也无害：只减仓）
            try: self.cancel_algo(symbol,algo_cl_ord_id=cid)
            except Exception: pass
        logger.warning(f"[ALPHA-X LIVE] {symbol} 附带TP/SL未出现，已单独补挂并验证：TP={tp_price} SL={sl_price} 数量={contracts}")
        return tp_cid,sl_cid

    def open(self,symbol,side,notional_usdt,tp_pct,sl_pct,leverage,client_order_id:Optional[str]=None,protection=None):
        if side not in ("long","short"): raise ValueError("side 必须是 long/short")
        if not 0<tp_pct<.5 or not 0<sl_pct<.5: raise ValueError("TP/SL 参数异常")
        ex=self._ensure(); cs=symbol_to_ccxt(symbol,"swap"); self.market(cs); self._ensure_leverage(cs,leverage)
        bal=self.account(); free=float(bal["free"]); total=float(bal["total"])
        if free<=0 or total<=0: raise RuntimeError("OKX USDT 权益/可用余额不足")
        tick=ex.fetch_ticker(cs); px=float((tick.get("ask") if side=="long" else tick.get("bid")) or tick.get("last") or 0)
        if px<=0: raise RuntimeError("无法取得有效价格")
        notional_usdt=min(float(notional_usdt),free*int(leverage)*.85); amount=self._contracts(cs,notional_usdt,px); mode=self.pos_mode()
        entry_side="buy" if side=="long" else "sell"; pos_side=side if mode=="long_short_mode" else "net"
        tp,sl=self._entry_protection(ex,cs,side,px,tp_pct,sl_pct,protection)
        clid=client_order_id or self.PREFIX+uuid.uuid4().hex[:28]
        if len(clid)>32 or not clid.isalnum(): raise ValueError("client_order_id 必须为 <=32 位字母数字串")
        tp_id=self.PREFIX+uuid.uuid4().hex[:24]; sl_id=self.PREFIX+uuid.uuid4().hex[:24]
        params={"tdMode":config.trading.margin_mode,"posSide":pos_side,"clOrdId":clid,"tag":self.TAG,
                "attachAlgoOrds":[{"attachAlgoClOrdId":tp_id,"tpTriggerPx":str(tp),"tpOrdPx":"-1","tpTriggerPxType":"last"},
                                  {"attachAlgoClOrdId":sl_id,"slTriggerPx":str(sl),"slOrdPx":"-1","slTriggerPxType":"last" if protection else "mark"}]}
        logger.warning(f"[ALPHA-X LIVE] {symbol} {side} {amount}张 ≈ {notional_usdt:.2f}U TP={tp} SL={sl} clOrdId={clid}")
        self.entry_submits=getattr(self,"entry_submits",0)+1      # 开仓单发出计数（引擎据此判断失败是否发生在下单之前）
        order=ex.create_order(cs,"market",entry_side,amount,None,params); oid=order.get("id")
        if not oid: raise RuntimeError(f"OKX 下单返回无 ordId: {order}")
        detail=self.wait_order_terminal(symbol,oid,timeout=8); state=str(detail.get("status") or order.get("status") or "").lower()
        # If the order timed out while partially/live, cancel the unfilled residual
        # and re-read the exchange order.  Never create local state from a non-terminal
        # entry order.
        if state not in ("closed","canceled","rejected"):
            try:
                ex.cancel_order(str(oid),cs)
            except Exception:
                pass
            detail=self.wait_order_terminal(symbol,oid,timeout=5.0)
            state=str(detail.get("status") or "").lower()
        filled=float(detail.get("filled") or order.get("filled") or 0); avg=float(detail.get("average") or order.get("average") or 0)
        if filled<=0 or state not in ("closed","canceled","expired"): raise RuntimeError(f"真实订单未确认成交: ordId={oid}, state={state}, filled={filled}")
        # The final filled quantity is the exchange-confirmed quantity; residual
        # quantity, if any, has already been canceled and is never booked locally.
        if avg<=0:
            # Never substitute the pre-submit quote for the real fill average.
            # Protection must be based on an exchange-confirmed execution price.
            raise RuntimeError(f"OKX 已确认成交数量但未返回有效实际成交均价: ordId={oid}, filled={filled}")
        # The entry order must carry native protection immediately, but the final
        # protection basis is the exchange-confirmed average fill price, not the
        # pre-submit quote.  Rebase TP/SL once after the fill and verify both algos.
        actual_tp=float(ex.price_to_precision(cs,avg*(1+tp_pct if side=="long" else 1-tp_pct)))
        actual_sl=float(ex.price_to_precision(cs,avg*(1-sl_pct if side=="long" else 1+sl_pct)))
        if protection: actual_tp,actual_sl=tp,sl
        rebased=self.amend_protection(symbol,side,actual_tp,actual_sl,expected_ids=[tp_id,sl_id])
        if protection and ((side=='long' and not actual_sl<avg<actual_tp) or (side=='short' and not actual_tp<avg<actual_sl)):
            rebased={'verified':False,'error':'真实成交价已越过结构保护边界'}
        if not rebased.get("verified"):
            fb=self._fallback_protection(symbol,side,filled,actual_tp,actual_sl,[tp_id,sl_id],rebased)
            if fb: tp_id,sl_id=fb; rebased={"verified":True}
        if not rebased.get("verified"):
            # A live position must never be left exposed without verified native
            # protection.  Immediately flatten the just-opened position and require
            # the exchange to report zero contracts before surfacing the error.
            flat_error=None; flat_result=None
            try:
                flat_result=self.close(symbol,side)
                if not flat_result.get("flat_confirmed"):
                    flat_error="交易所未确认仓位归零"
            except Exception as exc:
                flat_error=str(exc)
            detail=rebased.get('error') or rebased.get('errors') or rebased.get('count',0)
            if flat_error:
                raise RuntimeError(f"成交后重设TP/SL失败，且强制平仓未确认归零: {detail}; {flat_error}")
            err=RuntimeError(f"成交后重设TP/SL失败，系统已强制平仓并确认仓位归零: {detail}")
            err.flat_terminal=True; raise err
        return {"live":True,"symbol":symbol,"ccxt_symbol":cs,"side":side,"order_side":entry_side,"order_id":oid,"client_order_id":clid,"status":state,"filled":filled,"average":avg,
                "tp":actual_tp,"sl":actual_sl,"pretrade_tp":tp,"pretrade_sl":sl,"notional_usdt":filled*avg*float(self.market(cs).get("contractSize") or 1.0),"leverage":leverage,"pos_mode":mode,"tp_attach_clordid":tp_id,"sl_attach_clordid":sl_id,"protection_rebased":True}

    def open_maker(self,symbol,side,notional_usdt,tp_pct,sl_pct,leverage,client_order_id:Optional[str]=None,
                   ttl:float=45.0,max_reprice:int=2,improve_bps:float=0.5,chase_bps:float=12.0,
                   fallback_to_market:bool=False,ref_price:float=0.0,protection=None,fallback_on_runaway:bool=False):
        """FAST maker 进场：post-only 限价单，只做 maker、绝不主动吃单（会立即吃单的报价会被OKX直接拒单，不会产生taker）。
        成交前不持仓、无风险；挂单时即附带原生 TP/SL，成交瞬间交易所侧就有保护，随后按真实成交均价 rebase 并逐笔校验，
        校验失败立即强平（与市价 open() 完全同一套安全保护）。
        返回：成功=与 open() 同构的成交 dict（多 maker=True 字段）；未成交={'filled':0,'maker_status':...}（不抛错，调用方据此跳过）。
        """
        if side not in ("long","short"): raise ValueError("side 必须是 long/short")
        if not 0<tp_pct<.5 or not 0<sl_pct<.5: raise ValueError("TP/SL 参数异常")
        ex=self._ensure(); cs=symbol_to_ccxt(symbol,"swap"); self.market(cs); self._ensure_leverage(cs,leverage)
        bal=self.account(); free=float(bal["free"]); total=float(bal["total"])
        if free<=0 or total<=0: raise RuntimeError("OKX USDT 权益/可用余额不足")
        mode=self.pos_mode(); pos_side=side if mode=="long_short_mode" else "net"
        entry_side="buy" if side=="long" else "sell"; m=self.market(cs)
        try: tick=_safe_num((m.get("info") or {}).get("tickSz"), 0.0)
        except Exception: tick=0.0
        if tick<=0:
            try: tick=_safe_num(m.get("precision",{}).get("price"), 0.0)
            except Exception: tick=0.0
        if tick<=0: tick=1e-8
        notional_cap=min(float(notional_usdt),free*int(leverage)*.85)
        root_clid=client_order_id or self.PREFIX+uuid.uuid4().hex[:28]
        def market_fallback():
            child=root_clid[:30]+'M1'
            try:
                return self.open(symbol,side,notional_usdt,tp_pct,sl_pct,leverage,client_order_id=child,protection=protection)
            except Exception as exc:
                exc.client_order_id=child
                raise
        last_reject=None
        for attempt in range(int(max_reprice)+1):
            tk=ex.fetch_ticker(cs) or {}
            bid=float(tk.get("bid") or 0); ask=float(tk.get("ask") or 0); last=float(tk.get("last") or ref_price or 0)
            if bid<=0 or ask<=0 or last<=0: raise RuntimeError("无法取得有效盘口(bid/ask/last)")
            improve=max(tick,last*float(improve_bps)/1e4)
            if side=="long":
                lim=bid+improve
                if ask-bid>tick: lim=min(lim,ask-tick)   # 严格不跨价，保证 maker
                if lim>=ask: lim=bid
                lim=float(ex.price_to_precision(cs,lim))
            else:
                lim=ask-improve
                if ask-bid>tick: lim=max(lim,bid+tick)
                if lim<=bid: lim=ask
                lim=float(ex.price_to_precision(cs,lim))
            if lim<=0: raise RuntimeError("maker 限价计算异常")
            amount=self._contracts(cs,notional_cap,lim)
            clid=root_clid if attempt==0 else root_clid[:29]+f"R{attempt}"
            if len(clid)>32 or not clid.isalnum(): raise ValueError("client_order_id 必须为 <=32 位字母数字串")
            tp_id=self.PREFIX+uuid.uuid4().hex[:24]; sl_id=self.PREFIX+uuid.uuid4().hex[:24]
            tp,sl=self._entry_protection(ex,cs,side,lim,tp_pct,sl_pct,protection)
            params={"tdMode":config.trading.margin_mode,"posSide":pos_side,"clOrdId":clid,"tag":self.TAG,
                    "attachAlgoOrds":[{"attachAlgoClOrdId":tp_id,"tpTriggerPx":str(tp),"tpOrdPx":"-1","tpTriggerPxType":"last"},
                                      {"attachAlgoClOrdId":sl_id,"slTriggerPx":str(sl),"slOrdPx":"-1","slTriggerPxType":"last" if protection else "mark"}]}
            logger.warning(f"[ALPHA-X MAKER] {symbol} {side} post-only {amount}张 @ {lim} (bid={bid}/ask={ask}) ttl={ttl:.0f}s 第{attempt+1}次 clOrdId={clid}")
            try:
                self.entry_submits=getattr(self,"entry_submits",0)+1
                order=ex.create_order(cs,"post_only",entry_side,amount,lim,params)
            except Exception as submit_exc:
                last_reject=submit_exc
                logger.warning(f"[ALPHA-X MAKER] {symbol} post-only 提交异常，正在核查原订单，禁止直接重发: {submit_exc}")
                # 超时、断网或未知异常不能当成拒单；先查同一个客户订单号。
                try: existing=self.order_by_client_id(symbol,clid) or {}
                except Exception: existing={}
                if not existing.get('ordId'):
                    err=RuntimeError(f'提交结果未确认，禁止自动重发；clOrdId={clid}；{submit_exc}')
                    err.client_order_id=clid
                    raise err from submit_exc
                order={'id':existing['ordId']}
            oid=order.get("id")
            if not oid:
                info=order.get("info") or {}; sc=str(info.get("sCode") or "")
                if sc and sc!="0":
                    last_reject=RuntimeError(f"post_only 拒单 sCode={sc} {info.get('sMsg') or ''}")
                    time.sleep(0.4); continue
                raise RuntimeError(f"OKX post-only 返回无 ordId: {order}")
            ref=float(ref_price or last); deadline=time.time()+float(ttl); final=None
            def _finalize_partial():
                fd=self._cancel_confirm_entry(symbol,oid,clid)
                return fd if float(fd.get('filled') or 0)>0 else None
            while time.time()<deadline:
                time.sleep(2.0)
                try: d=ex.fetch_order(str(oid),cs)
                except Exception: d={}
                d=self._normalize_order(d)
                state=str(d.get("status") or "").lower(); filled=float(d.get("filled") or 0)
                if filled>0 and float(d.get("average") or 0)<=0:
                    err=RuntimeError("挂单已有成交但均价未知，必须对账，禁止补发")
                    err.client_order_id=clid
                    raise err
                if state in ("rejected","canceled","expired"):
                    if filled>0 and float(d.get("average") or 0)>0: final=self._normalize_order(d)
                    else: return {"filled":0,"maker_status":"rejected","order_id":oid,"client_order_id":clid}
                    break
                if state=="closed" and filled>0 and float(d.get("average") or 0)>0:
                    final=self._normalize_order(d); break
                if filled>0:  # 部分成交：立刻撤剩余，锁定已成交部分并补保护，避免裸露
                    final=_finalize_partial(); break
                try:  # 价格朝有利方向跑掉则提前撤单、不追价
                    nt=ex.fetch_ticker(cs) or {}; nl=float(nt.get("last") or 0)
                    if nl>0 and ((side=="long" and nl>=ref*(1+float(chase_bps)/1e4)) or (side=="short" and nl<=ref*(1-float(chase_bps)/1e4))):
                        final=_finalize_partial()
                        if final is None:
                            if fallback_to_market and fallback_on_runaway:   # 方案三：价格跑开也要进场（回测按开盘价必进）
                                logger.warning(f"[ALPHA-X MAKER] {symbol} 价格跑开未成交，按配置回退市价(taker) clOrdId={clid}")
                                return market_fallback()
                            return {"filled":0,"maker_status":"ran_away","order_id":oid,"client_order_id":clid}
                        break
                except Exception as exc:
                    if getattr(exc,'client_order_id',None): raise
                    raise RuntimeError(f'挂单状态查询异常，禁止补发: {exc}') from exc
            if final is None:  # TTL 到点
                final=_finalize_partial()
                if final is None:
                    if fallback_to_market:
                        logger.warning(f"[ALPHA-X MAKER] {symbol} 挂单超时未成交，按配置回退市价(taker) clOrdId={clid}")
                        return market_fallback()
                    return {"filled":0,"maker_status":"timeout","order_id":oid,"client_order_id":clid}
            filled=float(final.get("filled") or 0); avg=float(final.get("average") or 0)
            if filled<=0 or avg<=0:
                if fallback_to_market:
                    logger.warning(f"[ALPHA-X MAKER] {symbol} 终态无成交，回退市价(taker) clOrdId={clid}")
                    return market_fallback()
                return {"filled":0,"maker_status":"no_fill","order_id":oid,"client_order_id":clid}
            actual_tp=float(ex.price_to_precision(cs,avg*(1+tp_pct if side=="long" else 1-tp_pct)))
            actual_sl=float(ex.price_to_precision(cs,avg*(1-sl_pct if side=="long" else 1+sl_pct)))
            if protection: actual_tp,actual_sl=tp,sl
            rebased=self.amend_protection(symbol,side,actual_tp,actual_sl,expected_ids=[tp_id,sl_id])
            if protection and ((side=='long' and not actual_sl<avg<actual_tp) or (side=='short' and not actual_tp<avg<actual_sl)):
                rebased={'verified':False,'error':'真实成交价已越过结构保护边界'}
            if not rebased.get("verified"):
                fb=self._fallback_protection(symbol,side,filled,actual_tp,actual_sl,[tp_id,sl_id],rebased)
                if fb: tp_id,sl_id=fb; rebased={"verified":True}
            if not rebased.get("verified"):
                # 与市价路径一致：成交后保护无法验证，立即强平并要求交易所确认归零
                flat_error=None
                try:
                    flat_result=self.close(symbol,side)
                    if not flat_result.get("flat_confirmed"): flat_error="交易所未确认仓位归零"
                except Exception as exc: flat_error=str(exc)
                detail=rebased.get('error') or rebased.get('errors') or rebased.get('count',0)
                if flat_error: raise RuntimeError(f"[maker]成交后重设TP/SL失败，且强制平仓未确认归零: {detail}; {flat_error}")
                err=RuntimeError(f"[maker]成交后重设TP/SL失败，已强制平仓并确认归零: {detail}")
                err.flat_terminal=True; raise err
            logger.warning(f"[ALPHA-X MAKER] {symbol} {side} maker成交 {filled}张 @ {avg}（部分成交={bool(filled<amount)}），TP/SL已验证")
            return {"live":True,"symbol":symbol,"ccxt_symbol":cs,"side":side,"order_side":entry_side,"order_id":oid,"client_order_id":clid,
                    "status":"closed","filled":filled,"average":avg,"tp":actual_tp,"sl":actual_sl,"pretrade_tp":tp,"pretrade_sl":sl,
                    "notional_usdt":filled*avg*float(m.get("contractSize") or (m.get("info") or {}).get("ctVal") or 1.0),"leverage":leverage,"pos_mode":mode,"tp_attach_clordid":tp_id,"sl_attach_clordid":sl_id,
                    "protection_rebased":True,"maker":True,"partial_entry":bool(filled<amount)}
        if fallback_to_market:
            logger.warning(f"[ALPHA-X MAKER] {symbol} post-only 连续被拒，回退市价(taker): {last_reject}")
            return market_fallback()
        raise RuntimeError(f"post-only 挂单连续被拒，已放弃本单（未成交、无持仓）: {last_reject}")

    def _protection_rows(self, symbol, side=None, expected_ids=None):
        """Read only this position's native TP/SL algos and normalize client/algo ids.
        三级来源，逐级兜底：
        1. trade/order-algo 按 algoClOrdId 精确查询（最可靠，无分页/延迟窗口）
        2. 待处理算法单列表 orders-algo-pending
        3. 持仓 closeOrderAlgo
        """
        cs=symbol_to_ccxt(symbol,"swap")
        ids=[str(x) for x in (expected_ids or []) if x]
        rows=[]
        # 第一来源：按 algoClOrdId 直接精确查询，避免列表分页/延迟导致的查不到
        if ids:
            ex=self._ensure(); inst=self._inst_id(cs)
            for cid in ids:
                try:
                    raw=ex.request("trade/order-algo","private","GET",{"instId":inst,"algoClOrdId":cid})
                    data=(raw or {}).get("data") or []
                    for item in data:
                        if str(item.get("algoClOrdId") or item.get("attachAlgoClOrdId") or "")==cid:
                            rows.append(item)
                except Exception:
                    pass
        # 第二来源：待处理算法单列表
        try:
            for pending_type in ("conditional", "move_order_stop"):
                rows.extend({**x,"_active_source":"pending"} for x in self.pending_algos(symbol, pending_type))
        except Exception:
            pass
        # 第三来源：持仓 closeOrderAlgo
        try:
            try: mode=self.pos_mode()
            except Exception: mode="long_short_mode"
            for pos in self.positions():
                if pos.get("symbol")!=cs:
                    continue
                # net_mode下单向持仓side可能为None，只按symbol过滤；long_short_mode才匹配side
                if mode=="long_short_mode" and side and pos.get("side")!=side:
                    continue
                rows.extend({**x,"_active_source":"position"} for x in (pos.get("closeOrderAlgo") or []))
        except Exception:
            pass
        out=[]; seen=set()
        for x in rows:
            x=dict(x or {})
            client_id=str(x.get("algoClOrdId") or x.get("attachAlgoClOrdId") or "")
            algo_id=str(x.get("algoId") or "")
            if ids and client_id not in ids and algo_id not in ids:
                continue
            if not self._algo_is_active(x): continue
            key=(client_id,algo_id,str(x.get("ordType") or x.get("algoOrdType") or ""),
                 str(x.get("tpTriggerPx") or ""),str(x.get("slTriggerPx") or ""),
                 str(x.get("callbackRatio") or x.get("callbackSpread") or ""))
            if key in seen:
                continue
            seen.add(key); out.append(x)
        return out

    def _wait_for_attached_protection(self, symbol, side, expected_ids, timeout=10.0):
        """Attached algos are created asynchronously after the entry fills; poll before declaring failure."""
        deadline=time.time()+float(timeout); last=[]
        while time.time()<deadline:
            last=self._protection_rows(symbol,side,expected_ids)
            found={str(x.get("algoClOrdId") or x.get("attachAlgoClOrdId") or "") for x in last}
            if all(str(i) in found for i in expected_ids if i):
                return last
            time.sleep(.35)
        return last

    def cancel_algo(self, symbol, algo_id=None, algo_cl_ord_id=None):
        """Cancel one native OKX algo order and return the exchange result."""
        ex=self._ensure(); cs=symbol_to_ccxt(symbol,"swap"); inst=self._inst_id(cs)
        if not algo_id and not algo_cl_ord_id:
            raise ValueError("缺少 algoId/algoClOrdId")
        if not algo_id:
            rows=self._protection_rows(symbol,expected_ids=[str(algo_cl_ord_id)])
            row=next((x for x in rows if self._algo_client_id(x)==str(algo_cl_ord_id)),{})
            algo_id=row.get("algoId")
        if not algo_id: raise RuntimeError("无法解析精确algoId，未发送撤单")
        raw=ex.request("trade/cancel-algos","private","POST",[{"algoId":str(algo_id),"instId":inst}])
        data=(raw or {}).get("data") or []
        item=data[0] if data else {}
        if str((raw or {}).get("code"))!="0" or str(item.get("sCode"))!="0":
            raise RuntimeError(item.get("sMsg") or str(raw))
        return {"ok":True,"data":data,"raw":raw}

    def cancel_all_my_algos(self, symbol, side=None):
        """仓位已确认归零时，撤销该合约所有"本系统(AX前缀)"仍挂着的算法单。

        交易所端兜底：即使本地记录的订单ID不全（订单由人工/外部触发平仓、进程重启、
        或记录丢失），也直接从交易所拉 conditional + move_order_stop 两类待触发单，
        按 instId + AX前缀撤销，绝不误撤人工或其他系统的单。返回被撤销的 algoClOrdId。
        """
        ex=self._ensure(); cs=symbol_to_ccxt(symbol,"swap"); inst=self._inst_id(cs)
        cancelled=[]; errors=[]
        try:
            mode=self.pos_mode()
        except Exception:
            mode="long_short_mode"
        for ord_type in ("conditional","move_order_stop"):
            try:
                rows=self.pending_algos(symbol, ord_type)
            except Exception as exc:
                errors.append(f"{ord_type}:{exc}"); continue
            for x in rows or []:
                cid=str(x.get("algoClOrdId") or x.get("attachAlgoClOrdId") or "")
                # 只撤本系统挂的单：必须带 AX 前缀；人工单/其他系统单一律不碰。
                if not cid.startswith(self.PREFIX):
                    continue
                # 对冲模式下进一步按 posSide 精确匹配，避免误撤同币反向仓位的单。
                if mode=="long_short_mode" and side and str(x.get("posSide") or "").lower()!=str(side).lower():
                    continue
                algo_id=str(x.get("algoId") or "")
                if not algo_id:
                    continue
                try:
                    raw=ex.request("trade/cancel-algos","private","POST",[{"algoId":algo_id,"instId":inst}])
                    item=((raw or {}).get("data") or [{}])[0]
                    if str((raw or {}).get("code"))=="0" and str(item.get("sCode"))=="0":
                        cancelled.append(cid)
                    else:
                        # 订单可能已被触发/撤销，属正常；记录但不阻断。
                        errors.append(f"{cid}:{item.get('sMsg') or 'not_cancelled'}")
                except Exception as exc:
                    errors.append(f"{cid}:{exc}")
        if errors:
            logger.debug(f"[ALPHA-X] {symbol} 兜底撤单部分项未撤（可能已触发）: {';'.join(errors)}")
        return {"cancelled":cancelled,"errors":errors}

    @staticmethod
    def _algo_client_id(row):
        return str(row.get("algoClOrdId") or row.get("attachAlgoClOrdId") or "")

    @staticmethod
    def _algo_is_active(row):
        state=str(row.get("state") or row.get("algoState") or "").lower()
        # An already-triggered/canceled algo is not an outstanding protection.
        return state=="live" or (not state and row.get("_active_source") in ("pending","position"))

    def protection_status_sl_only(self, symbol, side=None, sl_id=None, wait_timeout=0.0):
        """Verify exactly one active native SL. Used only after NORMAL->TREND_TRAIL handoff."""
        ids=[str(sl_id)] if sl_id else []
        if wait_timeout:
            rows=self._wait_for_attached_protection(symbol,side,ids,timeout=wait_timeout) if ids else self._protection_rows(symbol,side)
        else:
            rows=self._protection_rows(symbol,side,ids)
        matched=[]
        for x in rows:
            cid=self._algo_client_id(x)
            if ids and cid not in ids: continue
            if not self._algo_is_active(x): continue
            if x.get("slTriggerPx") or x.get("slTriggerRatio"):
                matched.append(x)
        return {"verified":bool(matched) and (not ids or self._algo_client_id(matched[0])==ids[0]),
                "orders":matched,"count":len(matched),"expected_ids":ids,
                "missing":[i for i in ids if i not in {self._algo_client_id(x) for x in matched}]}

    def cancel_tp_and_verify(self, symbol, side, tp_id, sl_id=None, timeout=8.0):
        """Delete only the TP trigger on the existing attached algo, then verify TP is gone and SL remains.
        Uses OKX amend-algos with TP trigger=0, preserving the same attached protection identity for rollback.
        """
        ex=self._ensure(); cs=symbol_to_ccxt(symbol,"swap"); inst=self._inst_id(cs)
        if not tp_id: return {"verified":False,"error":"缺少原TP attachAlgoClOrdId"}
        ids=[str(tp_id)]+([str(sl_id)] if sl_id else [])
        rows=self._protection_rows(symbol,side,ids)
        tp=next((x for x in rows if self._algo_client_id(x)==str(tp_id) and (x.get("tpTriggerPx") or x.get("tpTriggerRatio"))),None)
        if not tp:
            if sl_id and self.protection_status_sl_only(symbol,side,sl_id,wait_timeout=1.5).get("verified"):
                return {"verified":True,"already_absent":True,"tp_absent":True,"sl_verified":True,"orders":rows}
            return {"verified":False,"error":"原TP不存在且无法确认现有SL，拒绝进入切换","orders":rows}
        algo_id=str(tp.get("algoId") or "")
        body={"instId":inst,"cxlOnFail":False,"newTpTriggerPx":"0","newTpOrdPx":"0"}
        if algo_id: body["algoId"]=algo_id
        else: body["algoClOrdId"]=str(tp_id)
        raw=ex.request("trade/amend-algos","private","POST",body)
        data=(raw or {}).get("data") or []; item=data[0] if data else {}
        if str(item.get("sCode","0"))!="0" or str(item.get("amendResult","0")) in ("-1","failed"):
            return {"verified":False,"error":item.get("sMsg") or str(raw),"orders":rows}
        deadline=time.time()+float(timeout); last=[]
        while time.time()<deadline:
            last=self._protection_rows(symbol,side,ids)
            active_tp=[x for x in last if self._algo_client_id(x)==str(tp_id) and self._algo_is_active(x) and (x.get("tpTriggerPx") or x.get("tpTriggerRatio"))]
            sl_ok=True
            if sl_id: sl_ok=self.protection_status_sl_only(symbol,side,sl_id,wait_timeout=0.5).get("verified")
            if not active_tp and sl_ok:
                return {"verified":True,"already_absent":False,"tp_absent":True,"sl_verified":sl_ok,"orders":last}
            time.sleep(.35)
        return {"verified":False,"error":"交易所未确认原TP已删除或SL仍有效","orders":last}

    def restore_tp_sl(self, symbol, side, tp_price, sl_price, old_tp_id=None, sl_id=None):
        """Rollback helper: restore the original TP on the same attached algo and re-verify TP+SL."""
        if not old_tp_id or not sl_id:
            return {"verified":False,"error":"回滚缺少原TP/SL身份ID"}
        result=self.amend_protection(symbol,side,float(tp_price),float(sl_price),expected_ids=[str(old_tp_id),str(sl_id)])
        if result.get("verified"):
            return {"verified":True,"tp_attach_clordid":str(old_tp_id),"sl_attach_clordid":str(sl_id),"tp":float(tp_price),"sl":float(sl_price)}
        return {"verified":False,"error":result.get("error") or ";".join(result.get("errors") or []) or "TP+SL回滚未验证","orders":result.get("orders",[])}

    def amend_tp_only(self, symbol, side, tp_price, tp_id=None, wait_timeout=5.0, trigger_type=None):
        """Amend only the existing native TP and verify the exact attached TP remains active."""
        ex=self._ensure(); cs=symbol_to_ccxt(symbol,"swap"); inst=self._inst_id(cs)
        if not tp_id: return {"verified":False,"error":"缺少TP attachAlgoClOrdId"}
        rows=self._wait_for_attached_protection(symbol,side,[tp_id],timeout=wait_timeout)
        tp=next((x for x in rows if self._algo_client_id(x)==str(tp_id) and (x.get("tpTriggerPx") or x.get("tpTriggerRatio"))),None)
        if not tp: return {"verified":False,"error":"TP不存在或ID不匹配","orders":rows}
        algo_id=str(tp.get("algoId") or "")
        trigger_type=trigger_type or tp.get("tpTriggerPxType") or "last"
        expected_price=float(ex.price_to_precision(cs,float(tp_price)))
        body={"instId":inst,"cxlOnFail":False,"newTpTriggerPx":str(expected_price),"newTpOrdPx":"-1","newTpTriggerPxType":trigger_type}
        if algo_id: body["algoId"]=algo_id
        else: body["algoClOrdId"]=str(tp_id)
        raw=ex.request("trade/amend-algos","private","POST",body)
        data=(raw or {}).get("data") or []; item=data[0] if data else {}
        if str(item.get("sCode","0"))!="0" or str(item.get("amendResult","0")) in ("-1","failed"):
            return {"verified":False,"error":item.get("sMsg") or str(raw),"orders":rows}
        verify=self._wait_for_attached_protection(symbol,side,[tp_id],timeout=3.0)
        ok=any(self._algo_client_id(x)==str(tp_id) and self._algo_is_active(x) and abs(float(x.get("tpTriggerPx") or 0)-expected_price)<=max(abs(expected_price)*1e-10,1e-14) and (x.get("tpTriggerPxType") or "last")==trigger_type for x in verify)
        return {"verified":ok,"changed":True,"orders":verify,"count":len(verify),"expected_ids":[str(tp_id)]}

    def amend_sl_only(self, symbol, side, sl_price, sl_id=None, wait_timeout=5.0):
        """Amend only the existing native SL; no TP is created/reintroduced."""
        ex=self._ensure(); cs=symbol_to_ccxt(symbol,"swap"); inst=self._inst_id(cs)
        if not sl_id: return {"verified":False,"error":"缺少SL attachAlgoClOrdId"}
        rows=self._wait_for_attached_protection(symbol,side,[sl_id],timeout=wait_timeout)
        sl=next((x for x in rows if self._algo_client_id(x)==str(sl_id) and (x.get("slTriggerPx") or x.get("slTriggerRatio"))),None)
        if not sl: return {"verified":False,"error":"原SL不存在或ID不匹配","orders":rows}
        algo_id=str(sl.get("algoId") or "")
        expected_price=float(ex.price_to_precision(cs,float(sl_price)))
        trigger_type=sl.get('slTriggerPxType') or 'mark'
        body={"instId":inst,"cxlOnFail":False,"newSlTriggerPx":str(expected_price),"newSlOrdPx":"-1","newSlTriggerPxType":trigger_type}
        if algo_id: body["algoId"]=algo_id
        else: body["algoClOrdId"]=str(sl_id)
        raw=ex.request("trade/amend-algos","private","POST",body)
        data=(raw or {}).get("data") or []; item=data[0] if data else {}
        if str(item.get("sCode","0"))!="0" or str(item.get("amendResult","0")) in ("-1","failed"):
            return {"verified":False,"error":item.get("sMsg") or str(raw),"orders":rows}
        verify=self.protection_status_sl_only(symbol,side,sl_id,wait_timeout=3.0)
        matching=next((x for x in verify.get('orders',[]) if self._algo_client_id(x)==str(sl_id)),{})
        exact=abs(float(matching.get('slTriggerPx') or 0)-expected_price)<=max(abs(expected_price)*1e-10,1e-14)
        exact=exact and (matching.get('slTriggerPxType') or 'mark')==trigger_type
        return {"verified":bool(verify.get("verified") and exact),"changed":True,"orders":verify.get("orders",[]),"count":verify.get("count",0),"expected_ids":[str(sl_id)]}

    def amend_protection(self, symbol, side, tp_price, sl_price, expected_ids=None):
        """Amend the exact attached TP/SL orders, using OKX algoId or algoClOrdId, then verify exact IDs."""
        ex=self._ensure(); cs=symbol_to_ccxt(symbol,"swap"); inst=self._inst_id(cs)
        ids=[str(x) for x in (expected_ids or []) if x]
        if len(ids)!=2:
            return {"verified":False,"error":"缺少完整的TP/SL attachAlgoClOrdId","orders":[]}
        rows=self._wait_for_attached_protection(symbol,side,ids,timeout=10.0)
        by_id={str(x.get("algoClOrdId") or x.get("attachAlgoClOrdId") or ""):x for x in rows}
        missing=[i for i in ids if i not in by_id]
        if missing:
            return {"verified":False,"error":"OKX 附加TP/SL尚未出现或ID不匹配: "+','.join(missing),"orders":rows}
        # Transaction-like protection amendment: snapshot the current TP/SL first.
        # OKX amends the two algos sequentially, so if either leg fails we immediately
        # roll back any successful leg and verify the pair again before returning.
        old_tp=float(by_id[ids[0]].get("tpTriggerPx") or 0)
        old_sl=float(by_id[ids[1]].get("slTriggerPx") or 0)
        changed=0; errors=[]; amended=[]
        targets=((ids[0],"tp",float(tp_price)),(ids[1],"sl",float(sl_price)))
        for client_id, kind, price in targets:
            x=by_id[client_id]; algo_id=str(x.get("algoId") or "")
            body={"instId":inst,"cxlOnFail":False}
            if algo_id: body["algoId"]=algo_id
            else: body["algoClOrdId"]=client_id
            if kind=="tp":
                body.update({"newTpTriggerPx":str(ex.price_to_precision(cs,price)),"newTpOrdPx":"-1","newTpTriggerPxType":"last"})
            else:
                body.update({"newSlTriggerPx":str(ex.price_to_precision(cs,price)),"newSlOrdPx":"-1","newSlTriggerPxType":x.get('slTriggerPxType') or 'mark'})
            try:
                raw=ex.request("trade/amend-algos","private","POST",body); data=(raw or {}).get("data") or []; item=data[0] if data else {}
                if str(item.get("sCode","0"))!="0" or str(item.get("amendResult","0")) in ("-1","failed"):
                    raise RuntimeError(item.get("sMsg") or str(item) or "amend failed")
                changed+=1; amended.append(kind)
            except Exception as exc:
                errors.append(f"{client_id}: {exc}")
                break
        if errors:
            # Roll back the leg that did change.  Use the exact original prices/IDs.
            try:
                if "tp" in amended and old_tp>0:
                    rr=self.amend_tp_only(symbol,side,old_tp,ids[0],wait_timeout=4.0,trigger_type=by_id[ids[0]].get("tpTriggerPxType") or "last")
                    if not rr.get("verified"): errors.append("TP回滚验证失败")
                if "sl" in amended and old_sl>0:
                    rr=self.amend_sl_only(symbol,side,old_sl,ids[1],wait_timeout=4.0)
                    if not rr.get("verified"): errors.append("SL回滚验证失败")
            except Exception as exc:
                errors.append(f"保护回滚异常: {exc}")
            verify=self.protection_status(symbol,side,expected_ids=ids,wait_timeout=3.0)
            return {"verified":False,"changed":changed,"errors":errors,"rolled_back":bool(amended) and len(errors)==1 and bool(verify.get("verified")),"orders":verify.get("orders",[]),"count":verify.get("count",0),"expected_ids":ids}
        verify=self.protection_status(symbol,side,expected_ids=ids,wait_timeout=3.0)
        actual={self._algo_client_id(x):x for x in verify.get('orders',[])}
        exact=True
        for client_id,kind,price in targets:
            expected=float(ex.price_to_precision(cs,price)); row=actual.get(client_id,{})
            exact=exact and abs(float(row.get(kind+'TriggerPx') or 0)-expected)<=max(abs(expected)*1e-10,1e-14)
            expected_type='last' if kind=='tp' else (by_id[client_id].get('slTriggerPxType') or 'mark')
            exact=exact and (row.get(kind+'TriggerPxType') or ('last' if kind=='tp' else 'mark'))==expected_type
        return {"verified":bool(changed==2 and verify.get("verified") and exact),"changed":changed,"errors":errors,"orders":verify.get("orders",[]),"count":verify.get("count",0),"expected_ids":ids}

    def protection_status_set(self, symbol, side=None, expected_ids=None, wait_timeout=0.0):
        ids=[str(x) for x in (expected_ids or []) if x]
        if wait_timeout:
            rows=self._wait_for_attached_protection(symbol,side,ids,timeout=wait_timeout)
        else:
            rows=self._protection_rows(symbol,side,ids)
        by={}
        for x in rows:
            cid=self._algo_client_id(x)
            if cid in ids and self._algo_is_active(x):
                by[cid]=x
        matched=[by[i] for i in ids if i in by]
        return {"verified":bool(ids and len(set(ids))==len(ids) and len(matched)==len(ids)),
                "orders":matched,"count":len(matched),"expected_ids":ids,
                "missing":[i for i in ids if i not in by]}

    def protection_status(self, symbol, side=None, expected_ids=None, wait_timeout=0.0):
        """Exact native TP/SL verification. With expected IDs, both exact client IDs must be present."""
        ids=[str(x) for x in (expected_ids or []) if x]
        if wait_timeout:
            rows=self._wait_for_attached_protection(symbol,side,ids,timeout=wait_timeout)
        else:
            rows=self._protection_rows(symbol,side,ids)
        if ids:
            by={str(x.get("algoClOrdId") or x.get("attachAlgoClOrdId") or ""):x for x in rows}
            by={k:v for k,v in by.items() if self._algo_is_active(v)}
            matched=[by[i] for i in ids if i in by]
            # 不依赖返回顺序，分别检查TP和SL（修复顺序依赖bug）
            tp_orders=[x for x in matched if x.get("tpTriggerPx") or x.get("tpTriggerRatio")]
            sl_orders=[x for x in matched if x.get("slTriggerPx") or x.get("slTriggerRatio")]
            tp_ok=bool(tp_orders)
            sl_ok=bool(sl_orders)
            return {"verified":bool(len(ids)==2 and len(set(ids))==2 and len(matched)==2 and tp_ok and sl_ok),"orders":matched,"count":len(matched),"expected_ids":ids,
                    "missing":[i for i in ids if i not in by]}
        return {"verified":False,"orders":rows,"count":len(rows),"error":"未提供期望TP/SL身份ID"}

    def wait_position_closed(self, symbol, side, timeout=10.0):
        deadline=time.time()+float(timeout); cs=symbol_to_ccxt(symbol,"swap"); last=None
        try: mode=self.pos_mode()
        except Exception: mode="long_short_mode"
        while time.time()<deadline:
            try:
                # net_mode下单向持仓，side可能为None，只按symbol过滤
                if mode=="long_short_mode":
                    rows=[p for p in self.positions() if p.get("symbol")==cs and p.get("side")==side and float(p.get("contracts") or 0)>0]
                else:
                    rows=[p for p in self.positions() if p.get("symbol")==cs and float(p.get("contracts") or 0)>0]
                last=rows[0] if rows else None
                if not last:
                    return {"closed":True,"position":None,"remaining_contracts":0.0}
            except Exception:
                pass
            time.sleep(.4)
        return {"closed":False,"position":last,"remaining_contracts":float((last or {}).get("contracts") or 0)}

    def close(self,symbol,side,timeout=60.0,max_attempts=6):
        """
        强制平仓状态机：以交易所真实剩余仓位为唯一依据，反复 reduceOnly 清扫残仓。
        任何一次部分成交/超时都不会被当成成功；只有 OKX 明确返回 0 张才 flat_confirmed。
        """
        ex=self._ensure(); cs=symbol_to_ccxt(symbol,"swap"); mode=self.pos_mode()
        close_side="sell" if side=="long" else "buy"
        deadline=time.time()+float(timeout)
        attempts=[]; first_error=None
        # First attempt uses native close-position; all later attempts use the
        # freshly observed residual quantity with reduceOnly.
        use_close_position=True
        while time.time()<deadline and len(attempts)<int(max_attempts):
            try:
                # net_mode下单向持仓，side可能为None或格式不一致，只按symbol过滤
                # long_short_mode下才严格匹配side
                if mode=="long_short_mode":
                    positions=[p for p in self.positions() if p.get("symbol")==cs and p.get("side")==side and float(p.get("contracts") or 0)>0]
                else:
                    positions=[p for p in self.positions() if p.get("symbol")==cs and float(p.get("contracts") or 0)>0]
            except Exception as exc:
                positions=[]; attempts.append({"attempt":len(attempts)+1,"error":f"读取仓位失败:{exc}"})
                time.sleep(.5); continue
            if not positions:
                # 双重确认：再查一次，防止瞬时查询为空导致误报
                time.sleep(.3)
                try:
                    if mode=="long_short_mode":
                        recheck=[p for p in self.positions() if p.get("symbol")==cs and p.get("side")==side and float(p.get("contracts") or 0)>0]
                    else:
                        recheck=[p for p in self.positions() if p.get("symbol")==cs and float(p.get("contracts") or 0)>0]
                except Exception:
                    recheck=positions
                if not recheck:
                    _cleanup=self.cancel_all_my_algos(symbol,side)
                    return {"live":True,"status":"closed","symbol":symbol,"side":side,"flat_confirmed":True,
                            "filled":sum(float(a.get("filled") or 0) for a in attempts),"average":0,"attempts":attempts,"algo_cleanup":_cleanup}
                positions=recheck
            remaining=sum(float(p.get("contracts") or 0) for p in positions)
            # Native close-position is preferred only on the first pass.
            if use_close_position:
                base={"instId":self._inst_id(cs),"mgnMode":config.trading.margin_mode,"autoCxl":True}
                if mode=="long_short_mode": base["posSide"]=side
                try:
                    raw=ex.request("trade/close-position","private","POST",base)
                    data=(raw or {}).get("data") or []; item=data[0] if data else {}
                    if str(item.get("sCode","0"))!="0":
                        raise RuntimeError(item.get("sMsg") or str(raw))
                    oid=item.get("ordId") or item.get("algoId")
                    detail=self.wait_order_terminal(symbol,oid,timeout=min(8.0,max(1.0,deadline-time.time()))) if oid else {}
                    attempts.append({"attempt":len(attempts)+1,"endpoint":"close-position","order_id":oid,
                                     "filled":float(detail.get("filled") or 0),"average":float(detail.get("average") or 0),"fee":float(detail.get("fee") or 0),"status":detail.get("status"),
                                     "remaining_before":remaining})
                except Exception as exc:
                    first_error=str(exc)
                    attempts.append({"attempt":len(attempts)+1,"endpoint":"close-position","error":str(exc),"remaining_before":remaining})
                use_close_position=False
            else:
                try:
                    contracts=float(ex.amount_to_precision(cs,remaining))
                    if contracts<=0:
                        raise RuntimeError(f"剩余仓位数量无法转换为有效下单张数: {remaining}")
                    params={"tdMode":config.trading.margin_mode,"posSide":side if mode=="long_short_mode" else "net",
                            "reduceOnly":True,"clOrdId":self.PREFIX+uuid.uuid4().hex[:28]}
                    order=ex.create_order(cs,"market",close_side,contracts,None,params)
                    oid=order.get("id")
                    detail=self.wait_order_terminal(symbol,oid,timeout=min(8.0,max(1.0,deadline-time.time()))) if oid else order
                    attempts.append({"attempt":len(attempts)+1,"endpoint":"reduceOnly-residual","order_id":oid,
                                     "filled":float(detail.get("filled") or order.get("filled") or 0),
                                     "average":float(detail.get("average") or order.get("average") or 0),
                                     "fee":self._fee_cost(detail) if detail.get("fee") is not None else self._fee_cost(order),
                                     "status":detail.get("status") or order.get("status"),
                                     "remaining_before":remaining})
                except Exception as exc:
                    attempts.append({"attempt":len(attempts)+1,"endpoint":"reduceOnly-residual","error":str(exc),"remaining_before":remaining})
            # Re-read the exchange immediately after each attempt. Never infer flat
            # from the order response alone.
            flat=self.wait_position_closed(symbol,side,timeout=min(5.0,max(.5,deadline-time.time())))
            if flat.get("closed"):
                _cleanup=self.cancel_all_my_algos(symbol,side)
                return {"live":True,"status":"closed","symbol":symbol,"side":side,"flat_confirmed":True,
                        "filled":sum(float(a.get("filled") or 0) for a in attempts),"average":(sum(float(a.get("filled") or 0)*float(a.get("average") or 0) for a in attempts)/max(sum(float(a.get("filled") or 0) for a in attempts),1e-12)),"fee":sum(float(a.get("fee") or 0) for a in attempts),"attempts":attempts,
                        "first_close_error":first_error,"algo_cleanup":_cleanup}
            if time.time()>=deadline:
                break
            # Always calculate the next order from the newest exchange position.
            time.sleep(.35)
        try:
            final=self.wait_position_closed(symbol,side,timeout=1.5)
        except Exception:
            final={"closed":False,"position":None,"remaining_contracts":None}
        if final.get("closed"):
            _cleanup=self.cancel_all_my_algos(symbol,side)
            return {"live":True,"status":"closed","symbol":symbol,"side":side,"flat_confirmed":True,
                    "filled":sum(float(a.get("filled") or 0) for a in attempts),"average":0,"attempts":attempts,"algo_cleanup":_cleanup}
        raise RuntimeError(f"强制平仓未完成：交易所仍有残仓 {symbol} {side}，remaining={final.get('remaining_contracts')}; attempts={attempts[-3:]}")

    def reduce_only_close_qty(self, symbol, side, contracts, timeout=20.0):
        """Reduce-only partial close using the current exchange position as the sole quantity source."""
        ex=self._ensure(); cs=symbol_to_ccxt(symbol,"swap"); mode=self.pos_mode()
        close_side="sell" if side=="long" else "buy"
        target=float(contracts)
        if target<=0: raise ValueError("分批止盈数量必须大于0")
        positions=self.positions()
        if mode=="long_short_mode":
            rows=[p for p in positions if p.get("symbol")==cs and p.get("side")==side and float(p.get("contracts") or 0)>0]
        else:
            rows=[p for p in positions if p.get("symbol")==cs and float(p.get("contracts") or 0)>0]
        remaining=sum(float(p.get("contracts") or 0) for p in rows)
        if remaining<=0: return {"status":"no_position","filled":0.0,"remaining_contracts":0.0,"flat_confirmed":True}
        target=min(target,remaining)
        amount=float(ex.amount_to_precision(cs,target))
        if amount<=0: raise RuntimeError(f"分批止盈数量精度处理后为0: {target}")
        params={"tdMode":config.trading.margin_mode,"posSide":side if mode=="long_short_mode" else "net","reduceOnly":True,"clOrdId":self.PREFIX+uuid.uuid4().hex[:28]}
        order=ex.create_order(cs,"market",close_side,amount,None,params); oid=order.get("id")
        if not oid: raise RuntimeError(f"分批止盈订单无ordId: {order}")
        detail=self.wait_order_terminal(symbol,oid,timeout=min(8.0,float(timeout)))
        state=str(detail.get("status") or order.get("status") or "").lower()
        filled=float(detail.get("filled") or order.get("filled") or 0); avg=float(detail.get("average") or order.get("average") or 0); fee=self._fee_cost(detail) if detail.get("fee") is not None else self._fee_cost(order)
        if state not in ("closed","canceled","rejected") and filled<=0:
            try: ex.cancel_order(str(oid),cs)
            except Exception: pass
            detail=self.wait_order_terminal(symbol,oid,timeout=5.0); state=str(detail.get("status") or "").lower(); filled=float(detail.get("filled") or filled); avg=float(detail.get("average") or avg); fee=self._fee_cost(detail) if detail.get("fee") is not None else fee
        if filled<=0: raise RuntimeError(f"分批止盈未确认成交: ordId={oid}, state={state}, requested={amount}")
        if avg<=0: raise RuntimeError(f"分批止盈已成交但无有效实际成交均价: ordId={oid}, filled={filled}")
        try: mode=self.pos_mode()
        except Exception: mode="long_short_mode"
        deadline=time.time()+float(timeout); remaining_after=None; flat=False
        while time.time()<deadline:
            try:
                ps=self.positions()
                if mode=="long_short_mode": rows=[p for p in ps if p.get("symbol")==cs and p.get("side")==side and float(p.get("contracts") or 0)>0]
                else: rows=[p for p in ps if p.get("symbol")==cs and float(p.get("contracts") or 0)>0]
                remaining_after=sum(float(p.get("contracts") or 0) for p in rows)
                flat=remaining_after<=0
                if flat or remaining_after < max(remaining-1e-12,0): break
            except Exception: pass
            time.sleep(.35)
        return {"status":"closed" if flat else "partial_filled","order_id":oid,"requested":amount,"filled":filled,"average":avg,"fee":fee,"remaining_contracts":float(remaining_after if remaining_after is not None else max(remaining-filled,0)),"flat_confirmed":bool(flat)}

    def kill(self): return True

alpha_live=AlphaLiveExecutor()
