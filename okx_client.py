"""
欧易交易客户端
基于 ccxt 封装, 提供现货/合约的统一交易接口
支持模拟盘和实盘, 支持干跑(Dry Run)模式
"""
import time
import math
import os
import ccxt
from typing import Optional, Dict, List, Any
from loguru import logger

from config import config


# OKX/CCXT 统一限流保护：只对“可安全重试”的读取类请求做指数退避。
# 订单创建等有副作用的写请求绝不自动重试，避免 429/网络抖动造成重复下单。
def _install_okx_rate_limit_guard(exchange):
    import random
    import threading
    safe_methods = {
        "load_markets", "fetch_ticker", "fetch_tickers", "fetch_ohlcv", "fetch_balance",
        "fetch_positions", "fetch_order", "fetch_open_orders", "fetch_my_trades",
        "fetch_closed_orders", "fetch_orders", "milliseconds",
    }
    lock = threading.RLock()
    max_attempts = max(1, int(os.getenv("ALPHAX_OKX_RETRY_ATTEMPTS", "4")))
    base_delay = max(0.25, float(os.getenv("ALPHAX_OKX_BACKOFF_BASE", "1.0")))
    max_delay = max(base_delay, float(os.getenv("ALPHAX_OKX_BACKOFF_MAX", "12.0")))

    def _retry_after(exc):
        headers = getattr(exc, "headers", None) or {}
        value = headers.get("Retry-After") or headers.get("retry-after")
        try:
            return max(0.0, min(max_delay, float(value))) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _is_retryable(exc):
        if isinstance(exc, (ccxt.RateLimitExceeded, ccxt.DDoSProtection, ccxt.NetworkError)):
            return True
        text = str(exc).lower()
        status = getattr(exc, "http_status", None) or getattr(exc, "status_code", None)
        return status == 429 or "429" in text or "too many requests" in text or "rate limit" in text

    for name in safe_methods:
        original = getattr(exchange, name, None)
        if not callable(original) or getattr(original, "_alphax_rate_guard", False):
            continue
        def guarded(*args, __orig=original, __name=name, **kwargs):
            last = None
            for attempt in range(max_attempts):
                try:
                    # 同一个 OKX 客户端的请求串行化，避免多币种扫描瞬间并发打爆限流。
                    with lock:
                        return __orig(*args, **kwargs)
                except Exception as exc:
                    last = exc
                    if not _is_retryable(exc) or attempt >= max_attempts - 1:
                        raise
                    retry_after = _retry_after(exc)
                    delay = retry_after if retry_after is not None else min(max_delay, base_delay * (2 ** attempt))
                    delay *= (0.80 + random.random() * 0.40)
                    logger.warning(
                        f"[OKX限流保护] {__name} 第{attempt+1}/{max_attempts}次失败，"
                        f"{delay:.2f}s后退避重试：{exc}"
                    )
                    time.sleep(delay)
            raise last
        guarded._alphax_rate_guard = True
        setattr(exchange, name, guarded)
    return exchange


class OKXClient:
    """欧易交易客户端封装"""

    def __init__(self):
        self._exchange: Optional[ccxt.okx] = None
        self._connected = False
        self._dry_run = config.risk.dry_run
        self.last_error: str = ""
        self._proxy_url: str = ""
        self._leverage_error: str = ""

    def _detect_proxy(self) -> str:
        """代理检测（已简化：只认config.py里手动配置的代理，不自动扫描环境变量和本地端口）
        原因：西瓜加速等系统全局代理会自动被ccxt/OKX SDK读取，再手动设置会导致双重代理冲突。
        如果需要手动指定代理，在config.py的proxy_url里填写即可。
        """
        if config.okx.proxy_url:
            logger.info(f"使用配置的代理: {config.okx.proxy_url}")
            return config.okx.proxy_url

        logger.info("未配置手动代理，使用系统网络（系统全局代理如西瓜加速会自动生效）")
        return ""

    # ------------------------------------------------------------------
    # 连接与初始化
    # ------------------------------------------------------------------
    def connect(self) -> bool:
        """连接欧易 API, 成功返回 True
        自动尝试多个节点, 优先www.okx.com, aws备用
        """
        self.last_error = ""
        if not config.okx.is_configured:
            self.last_error = "API 凭证未填写, 请在 .env 中配置 OKX_API_KEY / SECRET / PASSPHRASE"
            logger.error(self.last_error)
            return False

        # 尝试多个节点(优先www, aws备用)
        base_urls = [
            config.okx.base_url,
            "https://www.okx.com",
            "https://aws.okx.com",
        ]
        # 去重并保持顺序
        seen = set()
        urls_to_try = []
        for u in base_urls:
            if u and u not in seen:
                seen.add(u)
                urls_to_try.append(u)

        # 自动探测代理
        self._proxy_url = self._detect_proxy()

        last_err = None
        for base_url in urls_to_try:
            try:
                logger.info(f"尝试连接节点: {base_url}")
                hostname = base_url.replace("https://", "").replace("http://", "").split("/")[0]

                # ccxt OKX 使用 https://{hostname} 模板, 只需设置 hostname
                exchange_params = {
                    "apiKey": config.okx.api_key,
                    "secret": config.okx.api_secret,
                    "password": config.okx.api_passphrase,
                    "timeout": 15000,
                    "enableRateLimit": True,
                    "hostname": hostname,
                    "options": {
                        "defaultType": config.trading.trading_type,
                    },
                }

                # 配置代理
                if self._proxy_url:
                    exchange_params["proxies"] = {
                        "http": self._proxy_url,
                        "https": self._proxy_url,
                    }

                # 模拟盘使用专用域名
                if config.okx.is_demo:
                    exchange_params["sandbox"] = True
                    logger.info("当前为【模拟盘】模式")

                self._exchange = ccxt.okx(exchange_params)
                _install_okx_rate_limit_guard(self._exchange)
                # 再次确认 hostname
                if hasattr(self._exchange, "hostname"):
                    self._exchange.hostname = hostname
                # 设置代理 (双保险)
                if self._proxy_url:
                    try:
                        self._exchange.proxy = self._proxy_url
                    except Exception:
                        pass
                logger.info(f"当前节点: {self._exchange.hostname}, 代理: {self._proxy_url or '直连'}")

                self._exchange.load_markets()
                self._connected = True

                # 设置杠杆(合约和现货杠杆都尝试, 普通现货会记录错误)
                self._setup_leverage()

                mode = "模拟盘" if config.okx.is_demo else "实盘"
                dry = " (干跑模式-不会真实下单)" if self._dry_run else ""
                logger.info(f"欧易连接成功 | 节点: {base_url} | 模式: {mode}{dry} | 交易对: {config.trading.ccxt_symbol}")
                self.last_error = ""
                return True

            except ccxt.AuthenticationError as e:
                # 认证错误说明网络通了但 Key 错了, 不需要再试其他节点
                self.last_error = f"认证失败(Key或密码短语错误): {e}"
                logger.error(self.last_error)
                return False
            except ccxt.NetworkError as e:
                last_err = f"节点 {base_url} 网络错误: {e}"
                logger.warning(last_err)
                continue
            except ccxt.ExchangeError as e:
                self.last_error = f"交易所错误: {e}"
                logger.error(self.last_error)
                return False
            except Exception as e:
                last_err = f"节点 {base_url} 连接失败: {type(e).__name__}: {e}"
                logger.warning(last_err)
                continue

        # 所有节点都失败
        self.last_error = f"所有节点均无法连接(国内需开代理或使用aws节点): {last_err}"
        logger.error(self.last_error)
        return False

    def _setup_leverage(self):
        """设置杠杆(合约和现货杠杆都尝试设置, 失败不阻断)"""
        self._leverage_error = ""
        try:
            symbol = config.trading.ccxt_symbol
            self._exchange.set_leverage(
                config.trading.leverage,
                symbol,
                params={"mgnMode": config.trading.margin_mode}
            )
            logger.info(f"杠杆设置成功: {config.trading.leverage}x | 保证金: {config.trading.margin_mode} | {config.trading.trading_type}")
        except Exception as e:
            err_str = str(e)
            if "51021" in err_str or "exceed" in err_str.lower():
                self._leverage_error = f"杠杆倍数{config.trading.leverage}x超出该币种上限, 请调低杠杆。"
            else:
                self._leverage_error = f"杠杆设置提示: {err_str} (不影响下单, 交易所可能使用默认杠杆)"
            logger.warning(self._leverage_error)

    def set_leverage_for_symbol(self, symbol: str, leverage: Optional[int] = None) -> bool:
        """为指定币种设置杠杆。
        leverage 显式传入时优先使用该值，避免多币/ML配置与全局配置不同步。
        返回 True=设置成功, False=设置失败。
        """
        try:
            target_leverage = max(1, int(leverage if leverage is not None else config.trading.leverage))
            ccxt_sym = symbol_to_ccxt(symbol, config.trading.trading_type)
            self._exchange.set_leverage(
                target_leverage,
                ccxt_sym,
                params={"mgnMode": config.trading.margin_mode}
            )
            logger.info(f"[杠杆设置] {symbol} 杠杆={target_leverage}x 设置成功")
            return True
        except Exception as e:
            err_str = str(e)
            if "51021" in err_str or "exceed" in err_str.lower():
                logger.warning(f"[杠杆设置] {symbol} 杠杆{target_leverage}x超出上限, 交易所可能使用默认杠杆: {err_str}")
            else:
                logger.warning(f"[杠杆设置] {symbol} 杠杆设置提示(不影响下单): {err_str}")
            return False

    @property
    def is_connected(self) -> bool:
        return self._connected and self._exchange is not None

    def disconnect(self):
        """断开连接"""
        self._connected = False
        self._exchange = None
        logger.info("已断开欧易连接")

    def get_all_symbols(self) -> Dict[str, Any]:
        """获取欧易全部可交易币种(现货+永续合约)"""
        self._ensure_connected()
        try:
            markets = self._exchange.markets
            spot_symbols = []
            swap_symbols = []
            for symbol, info in markets.items():
                if not info.get("active", True):
                    continue
                mtype = info.get("type", "")
                if mtype == "spot" and symbol.endswith("/USDT"):
                    spot_symbols.append({
                        "symbol": symbol,
                        "base": info.get("base", ""),
                        "label": f"{info.get('base', '')}/USDT (现货)",
                    })
                elif mtype == "swap" and "USDT" in symbol:
                    base = info.get("base", "")
                    okx_symbol = f"{base}-USDT-SWAP"
                    swap_symbols.append({
                        "symbol": okx_symbol,
                        "ccxt_symbol": symbol,
                        "base": base,
                        "label": f"{base}-USDT 永续",
                    })
            spot_symbols.sort(key=lambda x: x["base"])
            swap_symbols.sort(key=lambda x: x["base"])
            return {
                "spot": spot_symbols,
                "swap": swap_symbols,
                "total": len(spot_symbols) + len(swap_symbols),
            }
        except Exception as e:
            logger.error(f"获取币种列表失败: {e}")
            return {"spot": [], "swap": [], "total": 0, "error": str(e)}

    def _ensure_connected(self):
        if not self.is_connected:
            raise RuntimeError("未连接欧易 API, 请先调用 connect()")

    # ------------------------------------------------------------------
    # 账户信息
    # ------------------------------------------------------------------
    def get_balance(self) -> Dict[str, Any]:
        """读取 OKX 账户实时权益；直接使用 /api/v5/account/balance，避免 CCXT fetch_balance 触发 asset/currencies。"""
        self._ensure_connected()
        try:
            raw = self._exchange.request("account/balance", "private", "GET", {})
            rows = (raw or {}).get("data") or []
            if not rows:
                raise RuntimeError("OKX 账户余额接口返回为空")
            acct = rows[0] or {}
            total_eq = float(acct.get("totalEq") or 0)
            avail_eq = float(acct.get("availEq") or 0)
            adj_eq = float(acct.get("adjEq") or total_eq)
            details = acct.get("details") or []
            usdt = next((x for x in details if str(x.get("ccy","")).upper()=="USDT"), {}) or {}
            usdt_cash = float(usdt.get("cashBal") or usdt.get("eq") or usdt.get("availEq") or 0)
            usdt_avail = float(usdt.get("availEq") or usdt.get("availBal") or usdt_cash)
            result = {
                "USDT": {"free": usdt_avail, "used": max(usdt_cash-usdt_avail, 0.0), "total": usdt_cash},
                "equity": total_eq,
                "available_equity": avail_eq,
                "adjusted_equity": adj_eq,
                "raw": raw,
            }
            # ALPHA-X 风控以账户总权益/可用权益为准；不再调用 fetch_balance。
            if total_eq <= 0 and usdt_cash > 0:
                result["equity"] = usdt_cash
            if avail_eq <= 0 and usdt_avail > 0:
                result["available_equity"] = usdt_avail
            return result
        except Exception as e:
            logger.error(f"获取实时权益失败（OKX account/balance）: {e}")
            raise

    def get_positions(self) -> List[Dict[str, Any]]:
        """获取当前持仓
        - 合约模式(swap): 从 fetch_positions 获取合约持仓
        - 现货模式(spot): 只从 fetch_balance 获取现货余额, 不查合约持仓(避免ccxt误报)
        """
        self._ensure_connected()
        all_positions = []
        current_ccxt_symbol = config.trading.ccxt_symbol
        # ===== 只有合约模式才查合约持仓 =====
        if config.trading.trading_type == "swap":
            try:
                swap_positions = self._exchange.fetch_positions()
                for p in swap_positions:
                    contracts = float(p.get("contracts", 0) or 0)
                    if contracts == 0:
                        continue
                    # 获取合约面值, 张数 -> 币数(全系统统一用币数, 下单时内部转张数)
                    market = self._exchange.markets.get(p.get("symbol", ""))
                    contract_size = 1.0
                    if market:
                        contract_size = float(market.get("contractSize", 0) or 0)
                        if contract_size <= 0:
                            minfo = market.get("info", {}) or {}
                            contract_size = float(minfo.get("ctVal", 1) or 1)
                    if contract_size <= 0:
                        contract_size = 1.0
                    coin_amount = abs(contracts) * contract_size
                    # 从原始数据判断方向: pos>0=多仓, pos<0=空仓
                    raw_pos = 0
                    info = p.get("info", {}) or {}
                    try:
                        raw_pos = float(info.get("pos", 0) or 0)
                    except (ValueError, TypeError):
                        raw_pos = 0
                    if raw_pos > 0:
                        side = "long"
                    elif raw_pos < 0:
                        side = "short"
                    else:
                        ccxt_side = p.get("side", "")
                        side = ccxt_side if ccxt_side in ("long", "short") else "long"
                    all_positions.append({
                        "symbol": p.get("symbol", ""),
                        "side": side,
                        "contracts": coin_amount,  # 币数(统一单位)
                        "contracts_raw": abs(contracts),  # 原始张数(调试用)
                        "contract_size": contract_size,
                        "entry_price": p.get("entryPrice", 0),
                        "mark_price": p.get("markPrice", 0),
                        "unrealized_pnl": p.get("unrealizedPnl", 0),
                        "leverage": p.get("leverage", 0),
                        "margin_mode": p.get("marginMode", ""),
                        "type": "swap",
                        "raw_pos": raw_pos,
                    })
            except Exception as e:
                logger.error(f"获取合约持仓失败: {e}")
        # ===== 现货模式: 只从余额获取当前交易对持仓 =====
        if config.trading.trading_type == "spot":
            try:
                balance = self._exchange.fetch_balance()
                base_coin = current_ccxt_symbol.replace("/USDT", "").replace("/USDC", "")
                for coin, bal in balance.items():
                    if coin in ("info", "free", "used", "total"):
                        continue
                    if not isinstance(bal, dict):
                        continue
                    if coin != base_coin:
                        continue
                    total = float(bal.get("total", 0) or 0)
                    free = float(bal.get("free", 0) or 0)
                    if total > 0:
                        # 过滤灰尘余额
                        spot_market = self._exchange.markets.get(f"{coin}/USDT")
                        spot_min = 0
                        if spot_market:
                            spot_min = float(spot_market.get("limits", {}).get("amount", {}).get("min", 0) or 0)
                        if spot_min > 0 and total < spot_min:
                            logger.debug(f"[灰尘过滤] {coin} 余额={total} < 最小下单={spot_min}, 不计入持仓")
                            continue
                        all_positions.append({
                            "symbol": f"{coin}/USDT",
                            "side": "long",
                            "contracts": total,
                            "free": free,
                            "entry_price": 0,
                            "unrealized_pnl": 0,
                            "type": "spot",
                        })
            except Exception as e:
                logger.error(f"获取现货持仓失败: {e}")
        return all_positions

    # ------------------------------------------------------------------
    # 行情数据
    # ------------------------------------------------------------------
    def get_ticker(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """获取最新行情"""
        self._ensure_connected()
        sym = symbol or config.trading.ccxt_symbol
        try:
            ticker = self._exchange.fetch_ticker(sym)
            return {
                "symbol": sym,
                "last": ticker.get("last"),
                "bid": ticker.get("bid"),
                "ask": ticker.get("ask"),
                "high": ticker.get("high"),
                "low": ticker.get("low"),
                "volume": ticker.get("baseVolume"),
                "timestamp": ticker.get("timestamp"),
            }
        except Exception as e:
            logger.error(f"获取行情失败: {e}")
            raise

    def fetch_swap_tickers(self) -> list:
        """一次性获取OKX全部USDT永续24h快照（自动选币L0粗筛用，仅1个请求）。
        返回标准化列表：[{symbol(内部BTC-USDT-SWAP),ccxt,last,bid,ask,quote_volume,pct}]，失败返回[]。"""
        out=[]
        try:
            self._ensure_connected(); ex=self._exchange
            if ex is None: return out
            tickers=ex.fetch_tickers(params={"instType":"SWAP"}) or {}
            for cs,t in tickers.items():
                try:
                    # 只要 USDT 本位永续（ccxt 形如 BASE/USDT:USDT）
                    if not isinstance(cs,str) or "/USDT" not in cs or not cs.endswith(":USDT"):
                        continue
                    base=cs.split("/")[0]
                    last=float(t.get("last") or 0)
                    if last<=0: continue
                    bid=float(t.get("bid") or 0); ask=float(t.get("ask") or 0)
                    qv=float(t.get("quoteVolume") or 0)
                    if qv <= 0:
                        qv=float((t.get("info") or {}).get("volCcy24h") or 0)*last
                    pct=float(t.get("percentage") or 0)
                    info=t.get("info") or {}
                    def _pct_sod(sod_key):
                        try:
                            sod=float(info.get(sod_key) or 0)
                            return (last/sod-1)*100 if sod>0 else None
                        except (TypeError,ValueError):
                            return None
                    out.append({"symbol":f"{base}-USDT-SWAP","ccxt":cs,"last":last,"bid":bid,"ask":ask,
                                "quote_volume":qv,"pct":pct,
                                "pct_utc8":_pct_sod("sodUtc8"),"pct_utc0":_pct_sod("sodUtc0")})
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"获取全市场永续快照失败: {e}")
            return []
        return out

    def market_spec(self, symbol: str) -> dict:
        """读取某合约的规格（最小张数/合约面值/最小名义额），自动选币'买得起'预判用；失败返回{}。"""
        try:
            ex=self._exchange
            if ex is None: return {}
            cs=config.trading.get_ccxt_symbol(symbol)
            m=(ex.markets or {}).get(cs) or {}
            amt=(m.get("limits",{}).get("amount",{}) or {})
            ct=float(m.get("contractSize") or (m.get("info") or {}).get("ctVal") or 1)
            price=float((m.get("info") or {}).get("last") or 0)
            return {"min_amount":float(amt.get("min") or 0),"contract_size":ct,
                    "min_notional":float(amt.get("min") or 0)*ct*price,"price":price}
        except Exception:
            return {}

    def price_to_precision(self, symbol: str, price: float) -> Optional[float]:
        """按交易所最小报价精度格式化限价（maker 挂单必须是合法价格档位）。失败返回 None。"""
        try:
            if self._exchange is None or price is None or float(price) <= 0:
                return None
            return float(self._exchange.price_to_precision(symbol, float(price)))
        except Exception as e:
            logger.warning(f"价格精度处理失败({symbol} {price}): {e}")
            return None

    @staticmethod
    def _timeframe_ms(timeframe: str) -> int:
        """返回K线周期毫秒数，用于剔除当前未收盘K线。"""
        return {
            "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
            "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
            "4h": 14_400_000, "6h": 21_600_000, "12h": 43_200_000,
            "1d": 86_400_000, "1w": 604_800_000,
        }.get(timeframe, 900_000)

    def get_ohlcv(self, symbol: Optional[str] = None, timeframe: Optional[str] = None,
                  limit: int = 100) -> List[List[float]]:
        """获取精确数量的已收盘K线。

        关键约束：
        1. limit 表示最终交给训练/预测的“完整已收盘K线”数量，不是API原始返回数量。
        2. OKX单次最多300根，因此大于300自动分页。
        3. 当前未收盘K线会被剔除；剔除后继续向历史分页，直到凑够limit。
        4. 去重、排序后严格返回最后limit根，避免后台显示7999/299等数量漂移。
        """
        self._ensure_connected()
        sym = symbol or config.trading.ccxt_symbol
        tf = timeframe or config.trading.timeframe
        limit = max(1, int(limit))
        tf_ms = self._timeframe_ms(tf)
        max_per_request = 300

        def _fetch_batch(since=None, batch_limit=300):
            # 统一限流保护已经安装在 exchange.fetch_ohlcv；这里不再做固定1秒重试，
            # 避免“本地重试 + CCXT限流 + OKX 429”多层叠加导致请求风暴。
            if since is not None:
                return self._exchange.fetch_ohlcv(sym, timeframe=tf, limit=batch_limit, since=since)
            return self._exchange.fetch_ohlcv(sym, timeframe=tf, limit=batch_limit)


        try:
            # 为“剔除未收盘K线”预留至少一根冗余；分页时仍以“最终已收盘数量”作为停止条件。
            all_candles = []
            since = None
            safety_rounds = 0
            max_rounds = max(4, (limit // max_per_request) + 6)

            while safety_rounds < max_rounds:
                safety_rounds += 1
                # 首次和每次分页都按API上限拉取；这样即使最后一根是未收盘K线，也能补足目标数量。
                batch = _fetch_batch(since=since, batch_limit=max_per_request)
                if not batch:
                    break

                all_candles.extend(batch)

                # 下一页必须继续向更早时间推进。
                timestamps = [int(c[0]) for c in batch if len(c) >= 6]
                if not timestamps:
                    break
                oldest_ts = min(timestamps)
                next_since = oldest_ts - (max_per_request * tf_ms)
                if since is not None and next_since >= since:
                    next_since = oldest_ts - tf_ms
                since = next_since

                # 先去重+过滤未收盘，再判断是否已经凑够最终数量。
                now_ms = int(time.time() * 1000)
                seen = set()
                completed = []
                for c in sorted(all_candles, key=lambda x: int(x[0])):
                    if len(c) < 6:
                        continue
                    ts = int(c[0])
                    if ts in seen:
                        continue
                    seen.add(ts)
                    if ts + tf_ms <= now_ms:
                        completed.append(c)

                if len(completed) >= limit:
                    result = completed[-limit:]
                    logger.info(f"K线获取完成 [{sym}] [{tf}] 请求={limit}根，最终={len(result)}根（已收盘）")
                    return result

                # 如果这一页已经不足API请求量，通常代表历史数据到头了。
                if len(batch) < max_per_request:
                    break
                time.sleep(0.15)

            # 历史数据确实不足时，不伪造数量，返回实际完整K线并明确记录。
            now_ms = int(time.time() * 1000)
            seen = set()
            completed = []
            for c in sorted(all_candles, key=lambda x: int(x[0])):
                if len(c) < 6:
                    continue
                ts = int(c[0])
                if ts in seen:
                    continue
                seen.add(ts)
                if ts + tf_ms <= now_ms:
                    completed.append(c)
            result = completed[-limit:]
            if len(result) < limit:
                logger.warning(f"K线历史数据不足 [{sym}] [{tf}] 请求={limit}根，实际={len(result)}根")
            else:
                logger.info(f"K线获取完成 [{sym}] [{tf}] 请求={limit}根，最终={len(result)}根（已收盘）")
            return result
        except Exception as e:
            logger.error(f"获取K线失败(已重试3次): {e}")
            raise

    # ------------------------------------------------------------------
    # 交易操作
    # ------------------------------------------------------------------
    def place_order(self, side: str, order_type: str = "market",
                    amount: Optional[float] = None, price: Optional[float] = None,
                    symbol: Optional[str] = None, reduce_only: bool = False,
                    post_only: bool = False) -> Dict[str, Any]:
        """下单

        Args:
            side: buy / sell
            order_type: market / limit
            amount: 下单数量(币的数量, 不是USDT; 合约内部自动转成张数)
            price: 限价单价格
            symbol: 交易对
            reduce_only: 合约减仓/平仓单（只减仓不开反向仓）
            post_only: 仅做 maker（post-only 限价单）。若该价会立即吃单，交易所直接拒单，
                       绝不转成 taker；用于 XS 组合调仓把手续费从 taker 降到 maker。
        """
        self._ensure_connected()
        sym = symbol or config.trading.ccxt_symbol
        is_swap = config.trading.trading_type == "swap"

        # 如果没传数量, 按配置的 USDT 金额换算
        if amount is None:
            amount = self._calc_amount_from_usdt(sym)

        # 合约: 币数量 -> 张数 (OKX合约下单单位是张, 不是币)
        if is_swap:
            market = self._exchange.markets.get(sym)
            contract_size = 1.0
            if market:
                contract_size = float(market.get("contractSize", 0) or 0)
                if contract_size <= 0:
                    info = market.get("info", {}) or {}
                    contract_size = float(info.get("ctVal", 1) or 1)
            if contract_size <= 0:
                contract_size = 1.0
            amount_contracts = amount / contract_size
            # 最小下单量检查(张数)
            min_contracts = 0.0
            if market:
                min_contracts = float(market.get("limits", {}).get("amount", {}).get("min", 0) or 0)
            if min_contracts > 0 and amount_contracts < min_contracts:
                logger.warning(f"[下单] {sym} 币数{amount:.6f} -> {amount_contracts:.4f}张 < 最小{min_contracts}张, 自动调整为最小量")
                amount_contracts = min_contracts
            # OKX 合约下单单位是张；最终必须按该合约的 amount 精度格式化。
            try:
                precise_amount = float(self._exchange.amount_to_precision(sym, amount_contracts))
            except Exception:
                precise_amount = amount_contracts
            if precise_amount <= 0:
                raise ValueError(f"{sym} 精度处理后下单张数为0，原始={amount_contracts}")
            if min_contracts > 0 and precise_amount < min_contracts:
                # 精度向下取整可能再次跌破最小量，向上补到最小可交易量。
                precise_amount = min_contracts
                try:
                    precise_amount = float(self._exchange.amount_to_precision(sym, precise_amount))
                except Exception:
                    pass
                if precise_amount < min_contracts:
                    raise ValueError(f"{sym} 无法得到满足最小下单量的有效张数: min={min_contracts}, got={precise_amount}")
            amount_contracts = precise_amount
            logger.info(f"[下单] 合约单位转换: {amount:.6f}币 = {amount_contracts}张 (面值1张={contract_size}币, 已按交易所精度处理)")
            amount = amount_contracts

        # 调试: 打印下单前所有关键参数
        logger.info(f"[下单调试] 币种={sym} | 类型={config.trading.trading_type} | 杠杆={config.trading.leverage}x | 保证金模式={config.trading.margin_mode} | 方向={side} | 数量={amount} | 每笔金额={config.trading.order_amount_usdt}U")

        if self._dry_run:
            logger.info(f"[干跑] 模拟下单: {side} {order_type} {amount} {sym} @ {price or '市价'}")
            return {
                "dry_run": True,
                "id": f"dry_{int(time.time()*1000)}",
                "symbol": sym,
                "side": side,
                "type": order_type,
                "amount": amount,
                "price": price,
                "status": "closed",
            }

        try:
            params = {}
            # 合约总是传保证金模式; 现货只有杠杆>1时传
            if is_swap:
                params["tdMode"] = config.trading.margin_mode
            elif config.trading.leverage > 1:
                params["tdMode"] = config.trading.margin_mode
                # 现货做空时指定USDT为保证金币种
                if side == "sell":
                    params["ccy"] = "USDT"
            # 减仓单(分批止盈/平仓): 只有合约才传 reduceOnly, 现货不支持该参数
            if reduce_only and is_swap:
                params["reduceOnly"] = True
            # post-only 限价单：只做 maker；若会立即吃单则交易所拒单（不会变成 taker）
            if post_only and order_type == "limit":
                params["postOnly"] = True

            order = self._exchange.create_order(
                symbol=sym,
                type=order_type,
                side=side,
                amount=amount,
                price=price,
                params=params,
            )
            mode_text = f"杠杆{config.trading.leverage}x {config.trading.margin_mode}" if config.trading.leverage > 1 else "普通现货(无杠杆)"
            logger.info(f"下单成功: {order['id']} | {side} {order_type} {amount} {sym} | {mode_text}")
            return order
        except ccxt.InsufficientFunds as e:
            logger.error(f"余额不足: {e}")
            raise
        except Exception as e:
            err_str = str(e)
            if "51008" in err_str or "margin" in err_str.lower():
                # 判断是做多还是做空, 给出不同提示
                if side == "sell" and config.trading.trading_type == "spot":
                    detail = "现货做空失败: 现货杠杆做空需要账户持有该币种作为保证金(借币卖出)。解决方案: 1)改用永续合约(-SWAP)做空, 合约用USDT保证金; 2)先买入该币持有后再做空。"
                else:
                    detail = "保证金不足。原因可能: 1)USDT在资金账户, 需划转到交易账户; 2)该币种不支持现货杠杆, 请换合约(-SWAP); 3)账户余额不足。"
                logger.error(f"下单失败: {detail} | 原始错误: {err_str}")
                raise RuntimeError(detail) from e
            if "54094" in err_str or "cool" in err_str.lower():
                detail = "该币种处于冷却期, 交易所限制下单, 请等待几分钟或换其他币种。"
                logger.error(f"下单失败: {detail} | 原始错误: {err_str}")
                raise RuntimeError(detail) from e
            logger.error(f"下单失败: {e}")
            raise

    def fetch_order(self, order_id: str, symbol: Optional[str] = None) -> Dict[str, Any]:
        """获取订单详情, 拿到实际成交价(解决入场价不准问题)"""
        self._ensure_connected()
        sym = symbol or config.trading.ccxt_symbol
        try:
            order = self._exchange.fetch_order(order_id, sym)
            return order
        except Exception as e:
            logger.warning(f"获取订单详情失败({order_id}): {e}, 使用下单返回价")
            return {}

    def wait_for_order_final(self, order: Dict, symbol: Optional[str] = None,
                             timeout_sec: float = 6.0, poll_sec: float = 0.4) -> Dict[str, Any]:
        """等待订单状态明确。

        不把“下单请求已接受”直接当成“已经成交”。返回最后一次订单详情。
        终态通常为 closed/filled/canceled/rejected/expired；live/open 等状态会继续轮询。
        """
        order_id = str(order.get("id", "") or "")
        if not order_id:
            return order or {}
        latest = order or {}
        deadline = time.time() + max(0.0, timeout_sec)
        terminal = {"closed", "filled", "canceled", "cancelled", "rejected", "expired"}
        while time.time() <= deadline:
            status = str(latest.get("status", "") or "").lower()
            if status in terminal:
                return latest
            try:
                detail = self.fetch_order(order_id, symbol)
                if detail:
                    latest = detail
            except Exception:
                pass
            status = str(latest.get("status", "") or "").lower()
            if status in terminal:
                return latest
            time.sleep(max(0.1, poll_sec))
        logger.warning(f"订单{order_id}在{timeout_sec:.1f}秒内未进入最终状态，最后状态={latest.get('status')}")
        return latest

    def get_filled_amount(self, order: Dict, symbol: Optional[str] = None) -> float:
        """把订单 filled 转成与 place_order 入参一致的币数量。合约的filled通常是张数，需要乘contractSize。"""
        try:
            filled = float(order.get("filled", 0) or 0)
        except Exception:
            return 0.0
        if filled <= 0:
            return 0.0
        if config.trading.trading_type != "swap":
            return filled
        sym = symbol or config.trading.ccxt_symbol
        market = self._exchange.markets.get(sym) if self._exchange else None
        contract_size = 1.0
        if market:
            contract_size = float(market.get("contractSize", 0) or 0)
            if contract_size <= 0:
                info = market.get("info", {}) or {}
                contract_size = float(info.get("ctVal", 1) or 1)
        return filled * max(contract_size, 1.0)

    def get_fill_price(self, order: Dict, symbol: Optional[str] = None, wait_sec: float = 0.8) -> float:
        """只返回可确认的实际成交均价；无法确认时返回0，不用行情价冒充成交价。"""
        def _avg(o: Dict) -> float:
            try:
                avg = float(o.get("average", 0) or 0)
                if avg > 0:
                    return avg
                filled = float(o.get("filled", 0) or 0)
                cost = float(o.get("cost", 0) or 0)
                if filled > 0 and cost > 0:
                    return cost / filled
            except Exception:
                pass
            return 0.0

        avg = _avg(order or {})
        if avg > 0:
            return avg
        order_id = (order or {}).get("id", "")
        if order_id:
            time.sleep(max(0.0, wait_sec))
            detail = self.fetch_order(order_id, symbol)
            avg = _avg(detail)
            if avg > 0:
                logger.info(f"[成交价] 订单{order_id} 实际成交均价={avg}")
                return avg
        logger.warning(f"[成交价] 无法确认订单{order_id or '<无ID>'}的实际成交价，不使用行情价冒充")
        return 0.0

    def close_position(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """平仓(市价全平)
        支持现货(spot)和合约(swap):
        - 优先平配置类型的持仓
        - 配置类型无持仓时, 自动平同交易对的其他类型持仓(防止漏平)
        - 现货: 直接市价卖出(用可用数量 free)
        - 合约: reduceOnly 市价平仓
        """
        self._ensure_connected()
        sym = symbol or config.trading.ccxt_symbol
        positions = self.get_positions()
        configured_type = "swap" if config.trading.trading_type == "swap" else "spot"
        # 按 symbol 筛选所有持仓(不区分类型), 优先配置类型
        same_symbol_positions = [p for p in positions if p.get("symbol") == sym]
        # 优先配置类型的持仓
        target_positions = [p for p in same_symbol_positions if p.get("type") == configured_type]
        # 配置类型没有时, 用同 symbol 的其他类型持仓(防止漏平)
        if not target_positions and same_symbol_positions:
            other_type = "合约" if configured_type == "spot" else "现货"
            logger.warning(f"[平仓] 配置类型({configured_type})无持仓, 发现{other_type}持仓, 自动平仓")
            target_positions = same_symbol_positions
        pos_type_name = "合约" if target_positions and target_positions[0].get("type") == "swap" else "现货"
        # 调试日志
        logger.info(f"[平仓调试] 配置类型={configured_type} | 全部持仓={len(positions)} | 目标持仓={len(target_positions)}")
        for i, p in enumerate(positions):
            logger.info(f"[平仓调试] 持仓[{i}] symbol={p.get('symbol')} type={p.get('type')} "
                        f"side={p.get('side')} contracts={p.get('contracts')} free={p.get('free', 'N/A')} raw_pos={p.get('raw_pos', 'N/A')}")
        if not target_positions:
            logger.info(f"当前无持仓({sym}), 无需平仓")
            return {"status": "no_position"}
        pos = target_positions[0]
        pos_is_swap = (pos.get("type") == "swap")
        side = pos.get("side", "")
        if pos_is_swap:
            amount = float(pos.get("contracts", 0) or 0)
        else:
            amount = float(pos.get("free", 0) or pos.get("contracts", 0) or 0)
        if amount <= 0:
            logger.info(f"{pos_type_name}持仓可用数量为0, 无需平仓")
            return {"status": "no_position"}
        # 最小下单量检查: 灰尘余额直接跳过, 不报51020
        market = self._exchange.markets.get(sym)
        min_amount = 0
        if market:
            min_amount = float(market.get("limits", {}).get("amount", {}).get("min", 0) or 0)
        # 合约: amount是币数, 最小下单量是张数, 需要转成张数比较
        compare_amount = amount
        if pos_is_swap and min_amount > 0:
            contract_size = float(pos.get("contract_size", 1.0) or 1.0)
            compare_amount = amount / contract_size if contract_size > 0 else amount
        if min_amount > 0 and compare_amount < min_amount:
            logger.info(f"[灰尘跳过] {pos_type_name}可用数量={amount}币(约{compare_amount:.4f}张) 小于最小下单量={min_amount}张, 跳过平仓(不报错)")
            return {"status": "dust_skip", "reason": f"数量{amount}<最小{min_amount}", "amount": amount}
        # 平仓方向: 多仓卖出, 空仓买入
        close_side = "sell" if side == "long" else "buy"
        logger.info(f"[平仓] {pos_type_name} | 方向={side}->{close_side} | 数量={amount}币 | {sym}")
        if self._dry_run:
            logger.info(f"[干跑] 模拟平仓: {close_side} {amount} {sym}")
            return {"dry_run": True, "status": "closed", "side": close_side, "amount": amount}
        # 下单参数
        close_params = {}
        if pos_is_swap:
            close_params["reduceOnly"] = True
            close_params["tdMode"] = config.trading.margin_mode
            # 合约: amount是币数, create_order期望张数, 需要转换
            contract_size = float(pos.get("contract_size", 1.0) or 1.0)
            if contract_size > 0:
                amount_contracts = amount / contract_size
                logger.info(f"[平仓] 单位转换: {amount:.6f}币 = {amount_contracts:.4f}张 (面值1张={contract_size}币)")
                amount = amount_contracts
        elif config.trading.leverage > 1:
            close_params["tdMode"] = config.trading.margin_mode
        try:
            order = self._exchange.create_order(
                symbol=sym,
                type="market",
                side=close_side,
                amount=amount,
                params=close_params,
            )
            logger.info(f"平仓成功: {order.get('id', 'N/A')} | {close_side} {amount} {sym}")
            return order
        except Exception as e:
            err_str = str(e)
            logger.error(f"平仓失败(主方式): {err_str}")
            if pos_is_swap:
                try:
                    logger.info("尝试兜底平仓(ccxt close_position)...")
                    fallback = self._exchange.close_position(symbol=sym, params={
                        "tdMode": config.trading.margin_mode,
                    })
                    logger.info(f"兜底平仓成功: {fallback}")
                    return fallback
                except Exception as e2:
                    logger.error(f"兜底平仓也失败: {e2}")
            raise RuntimeError(f"平仓失败: {err_str}") from e

    def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> Dict[str, Any]:
        """撤销订单"""
        self._ensure_connected()
        sym = symbol or config.trading.ccxt_symbol
        try:
            result = self._exchange.cancel_order(order_id, sym)
            logger.info(f"撤单成功: {order_id}")
            return result
        except Exception as e:
            logger.error(f"撤单失败: {e}")
            raise

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取未成交订单"""
        self._ensure_connected()
        sym = symbol or config.trading.ccxt_symbol
        try:
            return self._exchange.fetch_open_orders(sym)
        except Exception as e:
            logger.error(f"获取未成交订单失败: {e}")
            return []

    def get_trade_history(self, symbol: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """获取历史成交记录"""
        self._ensure_connected()
        sym = symbol or config.trading.ccxt_symbol
        try:
            trades = self._exchange.fetch_my_trades(sym, limit=limit)
            result = []
            for t in trades:
                result.append({
                    "id": t.get("id", ""),
                    "timestamp": t.get("timestamp", 0),
                    "time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t.get("timestamp", 0)/1000)) if t.get("timestamp") else "",
                    "symbol": t.get("symbol", ""),
                    "side": t.get("side", ""),
                    "price": float(t.get("price", 0) or 0),
                    "amount": float(t.get("amount", 0) or 0),
                    "cost": float(t.get("cost", 0) or 0),
                    "fee": float(t.get("fee", {}).get("cost", 0) or 0),
                    "fee_currency": t.get("fee", {}).get("currency", ""),
                    "type": t.get("type", ""),
                    "taker_or_maker": t.get("takerOrMaker", ""),
                })
            return result
        except Exception as e:
            logger.error(f"获取历史成交记录失败: {e}")
            return []

    def set_stop_loss_take_profit(self, side: str, entry_price: float,
                                   symbol: Optional[str] = None) -> Dict[str, Any]:
        """设置止盈止损(合约)
        修复: amount 不能为 None, 用实际持仓数量(张数); 同时加 closePosition 兜底
        """
        self._ensure_connected()
        sym = symbol or config.trading.ccxt_symbol
        if config.trading.trading_type != "swap":
            logger.info("现货不支持止盈止损单, 跳过")
            return {"status": "skipped", "reason": "spot_not_supported"}
        sl_pct = config.risk.stop_loss_pct
        tp_pct = config.risk.take_profit_pct
        if side == "long":
            sl_price = round(entry_price * (1 - sl_pct), 2)
            tp_price = round(entry_price * (1 + tp_pct), 2)
        else:
            sl_price = round(entry_price * (1 + sl_pct), 2)
            tp_price = round(entry_price * (1 - tp_pct), 2)
        if self._dry_run:
            logger.info(f"[干跑] 模拟设置止盈止损: SL={sl_price}, TP={tp_price}")
            return {"dry_run": True, "stop_loss": sl_price, "take_profit": tp_price}
        # 获取当前持仓数量(张数)
        positions = self.get_positions()
        swap_pos = [p for p in positions
                    if p.get("type") == "swap" and p.get("symbol") == sym]
        if not swap_pos:
            logger.warning("无合约持仓, 跳过止盈止损设置")
            return {"status": "skipped", "reason": "no_position"}
        pos_amount = float(swap_pos[0].get("contracts", 0) or 0)
        if pos_amount <= 0:
            logger.warning("持仓数量为0, 跳过止盈止损设置")
            return {"status": "skipped", "reason": "zero_position"}
        close_side = "sell" if side == "long" else "buy"
        logger.info(f"[止盈止损] 持仓={pos_amount}张 | 方向={close_side} | SL={sl_price} TP={tp_price}")
        try:
            # 止损单
            sl_order = self._exchange.create_order(
                symbol=sym, type="market", side=close_side,
                amount=pos_amount, params={
                    "tdMode": config.trading.margin_mode,
                    "reduceOnly": True,
                    "stopLossPrice": sl_price,
                    "closePosition": True,
                }
            )
            # 止盈单
            tp_order = self._exchange.create_order(
                symbol=sym, type="market", side=close_side,
                amount=pos_amount, params={
                    "tdMode": config.trading.margin_mode,
                    "reduceOnly": True,
                    "takeProfitPrice": tp_price,
                    "closePosition": True,
                }
            )
            logger.info(f"止盈止损设置成功: SL={sl_price}, TP={tp_price}")
            return {"stop_loss": sl_price, "take_profit": tp_price,
                    "sl_order": sl_order, "tp_order": tp_order}
        except Exception as e:
            logger.error(f"设置止盈止损失败: {e}")
            return {"status": "failed", "error": str(e)}

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    def _calc_amount_from_usdt(self, symbol: str) -> float:
        """根据 USDT 金额换算下单数量(全系统统一用币数, 下单时place_order内部转张数)
        永续合约和现货都返回币数量
        """
        try:
            ticker = self.get_ticker(symbol)
            price = ticker["last"]
            if not price or price <= 0:
                raise ValueError(f"无效价格: {price}")
            # 统一定义：页面“下单金额(USDT)”=保证金金额；永续合约实际仓位名义价值=保证金×杠杆。
            # 现货不使用杠杆，因此名义价值=下单金额。
            margin_usdt = float(config.trading.order_amount_usdt)
            leverage = max(1, int(config.trading.leverage or 1))
            is_swap = config.trading.trading_type == "swap"
            position_value = margin_usdt * leverage if is_swap else margin_usdt
            coin_amount = position_value / price
            market = self._exchange.markets.get(symbol)
            # 合约面值(用于最小下单量换算)
            contract_size = 1.0
            if is_swap and market:
                contract_size = float(market.get("contractSize", 0) or 0)
                if contract_size <= 0:
                    info = market.get("info", {}) or {}
                    contract_size = float(info.get("ctVal", 1) or 1)
                if contract_size <= 0:
                    contract_size = 1.0
            amount = coin_amount
            # 检查交易所最小下单量和精度
            if market:
                min_amount = float(market.get("limits", {}).get("amount", {}).get("min", 0) or 0)
                precision = market.get("precision", {}).get("amount", 8)
                # 合约: 最小下单量是张数, 转成币数比较
                if is_swap and min_amount > 0:
                    min_coin = min_amount * contract_size
                    if amount < min_coin:
                        logger.warning(f"下单量{amount:.6f}币 < 最小{min_coin:.6f}币({min_amount}张), 自动调整为最小量")
                        amount = min_coin
                elif min_amount > 0 and amount < min_amount:
                    logger.warning(f"下单量{amount:.6f}币 < 最小{min_amount}币, 自动调整为最小量")
                    amount = min_amount
                # 精度兼容两种格式: 整数(小数位数) / 浮点数(步长tick size)
                if precision:
                    if isinstance(precision, int):
                        amount = round(amount, precision)
                    else:
                        step = float(precision)
                        if step > 0:
                            amount = math.floor(amount / step) * step
                            amount = round(amount, 10)
            else:
                amount = round(amount, 6)
            logger.info(f"数量换算: 保证金={margin_usdt:.2f}U × 杠杆={leverage}x → 仓位名义价值={position_value:.2f}U ≈ {coin_amount:.6f}币 @ {price}")
            return amount
        except Exception as e:
            logger.error(f"数量换算失败: {e}")
            raise

    def get_server_time(self) -> int:
        """获取服务器时间戳(毫秒)"""
        self._ensure_connected()
        try:
            return self._exchange.milliseconds()
        except Exception:
            return int(time.time() * 1000)


# 全局单例
okx_client = OKXClient()
