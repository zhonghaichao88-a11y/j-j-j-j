"""
v5-XS 自动化运行时（alpha_xs_runtime）测试
覆盖：配置校验/持久化、1000前缀符号解析、看门狗快出、熔断判定、
      run_rebalance 干跑、熔断全平、scheduler_tick（关闭/未连接/未到点/到点/当天去重/宕机补跑）、
      调度线程幂等。全程 mock，不连接真实交易所。
运行: python3 backtest/test_xs_runtime.py
"""
import os, sys, tempfile, datetime as dt
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import alpha_xs_runtime as XRT

fails = []
def chk(cond, name):
    if cond:
        print("  OK  ", name)
    else:
        print("  FAIL", name); fails.append(name)

# 用独立临时状态文件，避免污染交付目录里的真实状态
_tmp = os.path.join(tempfile.gettempdir(), "xs_runtime_test_state.json")
if os.path.exists(_tmp): os.remove(_tmp)
XRT.STATE_FILE = _tmp
XRT._state = {"config": dict(XRT.DEFAULTS), "last_run_date": "", "last_run_at": 0.0,
              "last_run": None, "equity_anchor": None,
              "breaker": {"active": False, "date": "", "pct": None, "reason": ""}, "recent_logs": []}
XRT._save_state_locked()

def base_of(symbol):
    b = symbol.replace("/USDT:USDT", "").replace("USDT", "")
    if b.startswith("1000"): b = b[4:]
    return b

class MockRT:
    """合成行情的 mock OKX 客户端：31 币确定性趋势，供 run_rebalance 全链路调用。"""
    is_connected = True
    def __init__(self, bases, n=300, qv=None, connected=True, positions=None, equity=10000.0):
        self.bases = bases; self.n = n; self.qv = qv or {}
        self.is_connected = connected; self.positions = positions or []
        self.equity = equity; self.orders = []
        rng = np.random.default_rng(7)
        self.cl = {}
        for i, b in enumerate(bases):
            slope = (i - len(bases) / 2.0) * 0.0009
            self.cl[b] = np.cumprod(1.0 + slope + rng.normal(0, 0.002, n))
    def get_ohlcv(self, symbol, tf="5m", limit=None):
        b = base_of(symbol)
        x = self.cl.get(b)
        if x is None: return []
        rows = [[1 + i * 300000, float(p), float(p), float(p), float(p), 1000.0] for i, p in enumerate(x)]
        return rows[-(limit or len(rows)):]
    def fetch_swap_tickers(self):
        out = []
        for b in self.bases:
            cc = ("1000" + b) if b in ("PEPE", "SHIB", "FLOKI", "BONK") else b
            out.append({"ccxt": cc + "/USDT:USDT", "symbol": b, "last": float(self.cl[b][-1]),
                        "bid": float(self.cl[b][-1]), "ask": float(self.cl[b][-1]),
                        "quote_volume": self.qv.get(b, 1_000_000.0)})
        return out
    def get_balance(self):
        return {"equity": self.equity, "available_equity": self.equity}
    def get_positions(self):
        return self.positions
    def set_leverage_for_symbol(self, s, l): return True
    def price_to_precision(self, s, p): return round(float(p), 6)
    def get_ticker(self, s):
        b = base_of(s); x = self.cl.get(b); p = float(x[-1]) if x is not None else 1.0
        return {"last": p, "bid": p * 0.9999, "ask": p * 1.0001}
    def place_order(self, side, ot, amount=None, price=None, symbol=None, reduce_only=False, post_only=False):
        self.orders.append((symbol, side, ot, amount, reduce_only, post_only)); return {"id": "x", "status": "closed", "filled": amount}
    def market_spec(self, symbol): return {"contractSize": 1.0}

BASES = list(XRT.FIXED_UNIVERSE)

# 1 配置校验与持久化
def expect_error(patch, label):
    try:
        XRT.set_config(patch); ok = False
    except ValueError: ok = True
    chk(ok, label)
expect_error({"schedule_time": "25:00"}, "非法时间被拒绝")
expect_error({"unknown_key": 1}, "未知配置键被拒绝")
expect_error({"breaker_pct": 0.05}, "熔断阈值必须为负")
expect_error({"maker_ttl": 9999}, "超出范围的 maker_ttl 被拒绝")
XRT.set_config({"schedule_enabled": True, "maker_ttl": 12})
chk(XRT.get_config()["schedule_enabled"] is True and XRT.get_config()["maker_ttl"] == 12, "配置更新生效")
XRT._load_state_locked()
chk(XRT.get_config()["maker_ttl"] == 12, "配置持久化到状态文件并可重载")
XRT.set_config({"schedule_enabled": False, "maker_ttl": 8})

# 2 1000 前缀符号解析
mc = MockRT(BASES)
sym, canon = XRT.resolve_ccxt(mc, "PEPE")
chk(sym == "1000PEPE/USDT:USDT" and canon == "1000PEPE", "PEPE 自动解析为 1000PEPE")
sym, canon = XRT.resolve_ccxt(mc, "WIF")
chk(sym == "WIF/USDT:USDT" and canon == "WIF", "WIF 保持普通符号")
sym, canon = XRT.resolve_ccxt(mc, "NOTACOIN")
chk(sym == "NOTACOIN/USDT:USDT", "未知币回退标准符号(交给K线failed)")

# 3 看门狗（纯函数，qv 用 canonical 短名）
qv = {b: 1_000_000.0 for b in BASES}; qv["SAND"] = 1.0
good, rejected = XRT.watchdog_screen(BASES, qv, 0.10)
chk("SAND" not in good and any(r["base"] == "SAND" for r in rejected), "看门狗踢出低成交额坏币")
chk(len(good) == len(BASES) - 1, "看门狗只踢坏币、其余保留")
good2, rejected2 = XRT.watchdog_screen(BASES, {}, 0.10)
chk(len(good2) == len(BASES) and rejected2 == [], "成交额快照缺失时不踢任何币(防误杀)")
# 部分币成交额取不到（q=0，永续 quoteVolume 字段缺失）：保守保留，只踢"明确取到且很低"的币
qv3 = {b: 10_000_000.0 for b in BASES}
for _b in ["ARKM", "IMX", "RENDER", "ALT", "JTO", "MEME", "AXS"]:
    qv3[_b] = 0.0
qv3["SAND"] = 1.0
good3, rejected3 = XRT.watchdog_screen(BASES, qv3, 0.10)
chk(len(rejected3) == 1 and rejected3[0]["base"] == "SAND", "成交额取不到(q=0)的币保留、只踢明确低成交额的SAND")
chk(all(_b in good3 for _b in ["ARKM", "IMX", "RENDER", "ALT", "JTO", "MEME", "AXS"]), "7个成交额缺失币不被误杀(踢币7回归)")

# 4 熔断判定
chk(XRT._breaker_triggered(10000.0, 8999.0, True, -0.10)[0] is True, "回撤 -10% 触发熔断")
chk(XRT._breaker_triggered(10000.0, 9000.0, True, -0.10)[0] is True, "回撤恰 -10% 触发(含等号)")
chk(XRT._breaker_triggered(10000.0, 9500.0, True, -0.10)[0] is False, "回撤 -5% 不触发")
chk(XRT._breaker_triggered(10000.0, 9000.0, False, -0.10)[0] is False, "熔断关闭不触发")
chk(XRT._breaker_triggered(10000.0, None, True, -0.10)[0] is False, "无锚点(首日)不触发")

# 5 run_rebalance 干跑（maker 关，走纯市价路径；无真实下单）
mcd = MockRT(BASES)
r = XRT.run_rebalance(mcd, live=False, confirm=False, manual=True,
                      cfg_override={"watchdog_enabled": False, "maker_enabled": False, "breaker_enabled": False})
chk(r["success"] and r["mode"] == "干跑预览(未下单)", "干跑成功且未下单")
chk(len(r["longs"]) == 5 and len(r["shorts"]) == 5, "多空各 5")
chk(r["n_orders"] >= 10 and all(x.get("status") == "dry" for x in r["executed"]), "干跑给出≥10条计划且标记dry")
chk(len(mcd.orders) == 0, "干跑绝不调用真实下单")
chk(r["breaker_triggered"] is False, "正常情况熔断不触发")

# 6 熔断触发：先设锚点 10000，再跌到 9000 且持有 WIF/SUI，应全平不开新仓
XRT._state["equity_anchor"] = 10000.0
positions = [{"symbol": "WIF/USDT:USDT", "side": "long", "contracts": 2000.0, "type": "swap"},
             {"symbol": "SUI/USDT:USDT", "side": "short", "contracts": 2000.0, "type": "swap"}]
mcb = MockRT(BASES, equity=9000.0, positions=positions)
rb = XRT.run_rebalance(mcb, live=False, confirm=False, manual=True,
                       cfg_override={"breaker_enabled": True, "breaker_pct": -0.10, "watchdog_enabled": False})
chk(rb["success"] and rb["breaker_triggered"] is True, "权益较锚点 -10% 触发熔断")
chk(rb["n_orders"] == 2 and all(s["reduce_only"] for s in rb["plan"]), "熔断当天只平仓(2条reduce_only)、不开新仓")
chk(len(rb["longs"]) == 0 and len(rb["shorts"]) == 0, "熔断当天新篮子为空")
XRT._state["equity_anchor"] = None
XRT._save_state_locked()

# 6.5 口径回归：已持仓时总权益74/可用余额23（占用保证金），目标仓位必须锚定【总权益】≈14.8U/腿，
#     绝不能用可用余额23（否则4.6U<6U最小额，新币全部开不出、仓位缩水——真实事故单14只留5仓）
class MockRTTotalEq(MockRT):
    def get_balance(self):
        return {"equity": 74.0, "available_equity": 23.0, "adjusted_equity": 74.0}
_mid = BASES[11:21]   # 取中间10个币作为旧持仓，与新篮子(动量最强/最弱的头尾)错开
_pos_held = [{"symbol": f"{b}/USDT:USDT", "side": ("long" if i < 5 else "short"),
              "contracts": 15.0, "type": "swap"} for i, b in enumerate(_mid)]
mcEq = MockRTTotalEq(BASES, positions=_pos_held)
reEq = XRT.run_rebalance(mcEq, live=False, confirm=False, manual=True,
                         cfg_override={"watchdog_enabled": False, "maker_enabled": False, "breaker_enabled": False})
opens_eq = [s for s in reEq["plan"] if not s["reduce_only"]]
targets = set(reEq["longs"]) | set(reEq["shorts"])
open_bases = set(s["symbol"].split("/")[0] for s in opens_eq)
chk(reEq["success"], "总权益74/可用23(已持仓): 调仓成功")
chk(len(opens_eq) == 10 and targets <= open_bases, f"口径回归: 10个目标新仓全部开出(实际{len(opens_eq)})，未被6U最小额跳过")
chk(all(11.0 < s["notional"] < 18.0 for s in opens_eq), "口径回归: 开仓名义锚定总权益(≈14.8U/腿)，不是可用余额(≈4.6U)")

# 7~11 调度心跳（注入固定时间，避免依赖当前钟点）
def at(h, m, day=18):
    return dt.datetime(2026, 9, day, h, m, tzinfo=dt.timezone.utc)

XRT.set_config({"schedule_enabled": True, "schedule_time": "08:30"})
XRT._state["last_run_date"] = ""

XRT._client_provider = lambda: MockRT(BASES, connected=False)
r = XRT.scheduler_tick(now=at(8, 30))
chk(r["ran"] is False and r["reason"] == "not_connected", "到点但未连接: 不触发,等心跳重试")

XRT._client_provider = lambda: mc
r = XRT.scheduler_tick(now=at(8, 29))
chk(r["ran"] is False and r["reason"] == "not_due", "未到点不跑")

calls = []
def fake_rebalance(client, *, live, confirm, manual=False, **kw):
    calls.append({"live": live, "manual": manual}); return {"success": True, "mode": "测试", "n_orders": 0, "failed": [], "blocked": []}
orig_run = XRT.run_rebalance
XRT.run_rebalance = fake_rebalance
XRT._state["last_run_date"] = ""
r1 = XRT.scheduler_tick(now=at(8, 30))
r2 = XRT.scheduler_tick(now=at(8, 31))
chk(r1["ran"] is True and calls and calls[0]["live"] is True and calls[0]["manual"] is False, "到点自动实盘调仓(live=True,manual=False)")
chk(r2["ran"] is False and r2["reason"] == "already_run" and len(calls) == 1, "当天只跑一次,不重复")

# 宕机补跑：第二天到点，昨天跑过，今天没跑 -> 补跑
calls.clear()
XRT._state["last_run_date"] = "2026-09-18"
r3 = XRT.scheduler_tick(now=at(8, 30, day=19))
chk(r3["ran"] is True and len(calls) == 1, "宕机重启后第二天到点补跑")
XRT.run_rebalance = orig_run

# next_run_time 显示必须与执行层一致：今天已实盘则即使还没到点，下次也显示明天
XRT.set_config({"schedule_time": "13:30"})
XRT._state["last_run_date"] = ""
nr_today = XRT.next_run_time(now=at(11, 0, day=18))
XRT._state["last_run_date"] = "2026-09-18"
nr_next = XRT.next_run_time(now=at(11, 0, day=18))
chk(nr_today.startswith("2026-09-18T13:30") and nr_next.startswith("2026-09-19T13:30"),
    "今天已实盘则下次显示明天(显示与执行一致)")
XRT._state["last_run_date"] = ""

XRT.set_config({"schedule_enabled": False})
r = XRT.scheduler_tick(now=at(8, 30, day=19))
chk(r["ran"] is False and r["reason"] == "disabled", "定时开关关闭则不跑")

# 12 调度线程幂等
t1 = XRT.start_scheduler(lambda: mc)
t2 = XRT.start_scheduler(lambda: mc)
chk(t1 is True and t2 is False and XRT._scheduler_thread is not None, "调度线程只启动一次(幂等)")
XRT.stop_scheduler()
chk(XRT._scheduler_thread is None, "调度线程可停止")

# 恢复默认配置并清理
XRT._state = {"config": dict(XRT.DEFAULTS), "last_run_date": "", "last_run_at": 0.0, "last_run": None,
              "equity_anchor": None, "breaker": {"active": False, "date": "", "pct": None, "reason": ""},
              "recent_logs": []}
XRT._save_state_locked()
if os.path.exists(_tmp): os.remove(_tmp)

print("\n运行时测试：", "全部通过 ✅" if not fails else f"{len(fails)}项失败 ❌ {fails}")
sys.exit(1 if fails else 0)
