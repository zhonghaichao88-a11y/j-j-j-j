"""策略机（stratbot）：出场规则和 freqtrade 一致、策略加载、模拟盘开平仓、网页接口。"""
import asyncio
import json
import os
import sys
import types

import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("talib")
pytest.importorskip("technical")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "stratbot"))

import sb_engine as E  # noqa: E402
import sb_loader as L  # noqa: E402

STRAT = "ElliotV8_original_ichiv3"


class FakeSt:
    """最小的策略设置（和 sb_loader.Strategy 同样的字段）"""
    name = "fake"
    minimal_roi = {0: 0.99, 200: -1}
    stoploss = -0.20
    trailing_stop = True
    trailing_stop_positive = 0.001
    trailing_stop_positive_offset = 0.02
    trailing_only_offset_is_reached = True
    use_exit_signal = True
    exit_profit_only = True
    exit_profit_offset = 0.01
    ignore_roi_if_entry_signal = False
    tf_min = 5


def mk(price=100.0, tick=0.01):
    t = E.Trade("X/USDT", "fake", 0, price, 1.0, 0.0007, 0.0007, tick=tick, stake=price)
    t.adjust_stop_loss(t.open_rate, FakeSt.stoploss, initial=True)
    return t


def test_initial_stop_is_20pct_below_and_rounded_up_to_tick():
    t = mk(100.0, 0.01)
    assert t.stop_loss == pytest.approx(80.0)
    t2 = mk(1.2345, 0.001)
    assert t2.stop_loss == pytest.approx(0.988)        # 0.9876 向上取整到 0.001


def test_profit_ratio_includes_fees_both_sides():
    t = mk(100.0)
    assert t.ratio(100.0) == pytest.approx(1 * (1 - 0.0007) / (1 + 0.0007) - 1, abs=1e-8)


def test_trailing_not_armed_below_offset():
    st, t = FakeSt(), mk(100.0)
    ex = E.should_exit(st, t, 101.0, 10, False, False, low=100.5, high=101.5)   # 最高只赚 1.4%（含手续费）
    assert ex == [] and not t.is_stop_loss_trailing and t.stop_loss == pytest.approx(80.0)


def test_trailing_armed_above_offset_then_hit():
    st, t = FakeSt(), mk(100.0)
    ex = E.should_exit(st, t, 101.0, 10, False, False, low=102.95, high=103.0)  # 最高赚 >2%：止损抬到 103*0.999
    assert t.is_stop_loss_trailing and t.stop_loss == pytest.approx(102.9)       # 102.897 向上取整
    assert ex == []                                                             # 这根最低 102.95 没碰到
    ex = E.should_exit(st, t, 103.0, 15, False, False, low=102.5, high=103.0)
    assert ex == [("trailing_stop_loss", "trailing_stop_loss")]
    assert E.close_rate_backtest(st, t, "trailing_stop_loss", (103.0, 103.0, 102.5, 102.6), 15) == pytest.approx(102.9)


def test_roi_minus_one_exits_at_open_after_200_minutes():
    st, t = FakeSt(), mk(100.0)
    assert E.should_exit(st, t, 99.0, 195, False, False, low=98.0, high=99.5) == []
    ex = E.should_exit(st, t, 99.0, 200, False, False, low=98.0, high=99.5)
    assert ex == [("roi", "roi")]
    assert E.close_rate_backtest(st, t, "roi", (99.0, 99.5, 98.0, 99.2), 200) == 99.0


def test_exit_signal_only_when_profit_above_offset():
    st, t = FakeSt(), mk(100.0)
    assert E.should_exit(st, t, 100.5, 20, False, True, low=100.4, high=100.6) == []      # 只赚 0.36%：不卖
    assert E.should_exit(st, t, 101.5, 25, False, True, low=101.4, high=101.6) == [("exit_signal", "exit_signal")]
    t2 = mk(100.0)
    assert E.should_exit(st, t2, 101.5, 25, True, True, low=101.4, high=101.6) == []     # 同时有买入信号：不卖


def test_stoploss_hit_closes_at_stop_price_or_open_if_gapped():
    st, t = FakeSt(), mk(100.0)
    assert E.should_exit(st, t, 85.0, 30, False, False, low=79.0, high=86.0) == [("stop_loss", "stop_loss")]
    assert E.close_rate_backtest(st, t, "stop_loss", (85.0, 86.0, 79.0, 80.0), 30) == pytest.approx(80.0)
    t2 = mk(100.0)
    E.should_exit(st, t2, 75.0, 30, False, False, low=70.0, high=78.0)
    assert E.close_rate_backtest(st, t2, "stop_loss", (75.0, 78.0, 70.0, 72.0), 30) == 75.0    # 跳空：按开盘价


def test_live_check_uses_current_price_only():
    st, t = FakeSt(), mk(100.0)
    assert E.check_exit_live(st, t, 103.0, 10 * 60_000, 0, 0) is None      # 赚 2.86%：移动止盈启动，止损抬到 102.9
    assert t.is_stop_loss_trailing
    assert E.check_exit_live(st, t, 102.8, 11 * 60_000, 0, 0) == ("trailing_stop_loss", "trailing_stop_loss")
    t2 = mk(100.0)
    assert E.check_exit_live(st, t2, 99.0, 200 * 60_000, 0, 0) == ("roi", "roi")


def test_backtest_signal_shift_and_pending_exit_order():
    """信号往后挪一根：第 0 根收盘出买入信号 → 第 1 根开盘买；平仓价不在K线范围内就挂着下一根成交"""
    st = FakeSt()
    ts = pd.date_range("2025-01-01", periods=50, freq="5min", tz="UTC")
    d = pd.DataFrame({"date": ts, "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 1.0,
                      "enter_long": 0, "exit_long": 0})
    d.loc[0, "enter_long"] = 1
    bt = E.Backtest(st, max_open=1, fee=0.0007, ticks={"X/USDT": 0.01})
    out = bt.run({"X/USDT": d}, ["X/USDT"])
    assert len(out) == 1
    o = out[0]
    assert o["open_ts"] == int(ts[1].timestamp() * 1000)          # 第 0 根收盘的信号，第 1 根开盘成交（freqtrade：信号挪一根、去掉第一行）
    assert o["exit_reason"] == "roi" and o["trade_duration"] == 200               # 不涨不跌：200 分钟到点按开盘价平


def test_stake_respects_exchange_minimum_like_freqtrade():
    bt = E.Backtest(FakeSt(), max_open=10, wallet=1000, markets={"A/USDT": {"amin": 1.0, "amax": 1e12, "cmin": None, "cmax": 1e6, "aprec": 1e-6}})
    stake, amt = bt.stake_for("A/USDT", 200.0, 0.0, 0.0)      # 每单 99U，最少要 1 个 × 200 × 1.05 = 210U → 开不了
    assert stake == 0
    stake, amt = bt.stake_for("A/USDT", 80.0, 0.0, 0.0)       # 最少 84U，每单 99U → 能开
    assert stake == pytest.approx(99.0, rel=1e-3)


def test_loader_runs_original_file_without_freqtrade():
    st = L.Strategy(STRAT)
    assert "freqtrade" not in sys.modules or not hasattr(sys.modules["freqtrade"], "__fake__")
    assert st.unsupported == []
    assert st.minimal_roi == {0: 0.99, 200: -1.0} and st.stoploss == -0.2
    assert st.exit_profit_only and st.exit_profit_offset == 0.01 and st.use_exit_signal
    assert st.trailing_stop_positive == 0.001 and st.trailing_stop_positive_offset == 0.02
    import numpy as np
    n = 1200
    c = 100 * np.cumprod(np.r_[np.full(1100, 1.001), np.full(6, 0.99), np.full(94, 1.0005)])   # 一路涨，然后急跌 6 根
    df = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=n, freq="5min", tz="UTC"), "open": c, "high": c * 1.002,
                       "low": c * 0.998, "close": c, "volume": 1.0})
    a = st.analyze(df)
    assert len(a) == n and set(a.enter_long.unique()) <= {0, 1} and set(a.exit_long.unique()) <= {0, 1}
    assert a.enter_long.sum() > 0 and a.index[a.enter_long == 1][0] == 1102      # 急跌第 3 根收盘出买入信号


def test_loader_rejects_unsupported_features(tmp_path):
    src = '''
from freqtrade.strategy import IStrategy
class Bad(IStrategy):
    minimal_roi = {"0": 0.1}
    stoploss = -0.1
    position_adjustment_enable = True
    def custom_entry_price(self, **k): return None
    def populate_indicators(self, df, m): return df
    def populate_buy_trend(self, df, m): return df
    def populate_sell_trend(self, df, m): return df
'''
    p = tmp_path / "Bad.py"
    p.write_text(src)
    st = L.Strategy("Bad", str(p))
    assert any("position_adjustment" in x for x in st.unsupported) and "custom_entry_price" in st.unsupported


def test_fill_missing_like_freqtrade():
    ts = pd.to_datetime(["2025-01-01 00:00", "2025-01-01 00:05", "2025-01-01 00:20"], utc=True)
    d = pd.DataFrame({"date": ts, "open": [1, 2, 3.0], "high": [1, 2, 3.0], "low": [1, 2, 3.0], "close": [1, 2.5, 3.0], "volume": [5, 5, 5.0]})
    f = L.fill_missing(d)
    assert len(f) == 5 and f.loc[2, "open"] == 2.5 and f.loc[2, "volume"] == 0


# ---------------------------------------------------------------- 模拟盘 + 网页接口（不连网：假的行情）
@pytest.fixture
def app_client(tmp_path, monkeypatch):
    import shutil
    root = os.path.join(os.path.dirname(__file__), "..", "stratbot")
    work = tmp_path / "sb"
    shutil.copytree(root, work, ignore=shutil.ignore_patterns(".env", "sb_config.json", "sb_state.json", "sb_trades.jsonl",
                                                                "sb_log.txt", "__pycache__", ".venv"))
    monkeypatch.chdir(work)
    for m in ("sb_app", "sb_bot", "sb_engine", "sb_loader", "sb_data", "sb_notify", "sb_live"):
        sys.modules.pop(m, None)
    sys.path.insert(0, str(work))
    import sb_app
    from fastapi.testclient import TestClient
    yield sb_app, TestClient(sb_app.app)
    sys.path.remove(str(work))
    for m in ("sb_app", "sb_bot", "sb_engine", "sb_loader", "sb_data", "sb_notify", "sb_live"):
        sys.modules.pop(m, None)


def test_paper_open_close_and_api(app_client, monkeypatch):
    sb_app, cli = app_client
    bot = sb_app.bot
    monkeypatch.setattr(bot, "start", lambda: asyncio.sleep(0))           # 不连欧易
    assert os.path.exists("sb_config.json")                                # 第一次运行复制了一份默认设置
    bot.inst = {"BTC-USDT-SWAP": {"tick": 0.1, "ctVal": 0.01, "lotSz": 0.01, "minSz": 0.01, "listTime": 0, "state": "live"}}
    bot.coins = ["BTC-USDT-SWAP"]
    bot.prices = {"BTC-USDT-SWAP": 80000.0}
    bot.last_candle = 1_000_000
    bot.sig[(STRAT, "BTC-USDT-SWAP")] = {"ts": 1_000_000, "enter": 1, "exit": 0}
    asyncio.run(bot._entries())
    assert len(bot.trades) == 1
    t = bot.trades[0]
    assert t.open_rate == pytest.approx(80000 * 1.0002)
    assert t.contracts == pytest.approx(0.12)          # 每单 100U ÷ (8 万 × 0.01) = 0.125 张 → 按 0.01 张取整
    assert t.stop_loss == pytest.approx(80016 * 0.8, abs=0.11)    # 成交价 80016（含滑点）下方 20%
    r = cli.get("/api/status").json()
    assert r["mode"] == "paper" and len(r["open"]) == 1 and r["open"][0]["coin"] == "BTC"
    # 同一根K线不重复开
    asyncio.run(bot._entries())
    assert len(bot.trades) == 1
    # 涨 3% 后回落：移动止盈平仓
    bot.prices["BTC-USDT-SWAP"] = 82500.0
    asyncio.run(bot._exits())
    assert len(bot.trades) == 1 and bot.trades[0].is_stop_loss_trailing
    bot.prices["BTC-USDT-SWAP"] = 82300.0
    asyncio.run(bot._exits())
    assert bot.trades == []
    rows = cli.get("/api/trades").json()["rows"]
    assert len(rows) == 1 and rows[0]["reason"] == "trailing_stop_loss" and rows[0]["pnl"] > 0
    st = json.load(open("sb_state.json"))
    assert st["ledger"][f"paper:{STRAT}"] == pytest.approx(rows[0]["pnl"])


def test_api_validation_and_live_guard(app_client):
    sb_app, cli = app_client
    assert cli.post("/api/strategy", json={"name": STRAT, "max_open": 0}).status_code == 400
    assert cli.post("/api/strategy", json={"name": STRAT, "capital": "abc"}).status_code == 400
    assert cli.post("/api/strategy", json={"name": STRAT, "capital": 80, "max_open": 5}).status_code == 200
    cfg = json.load(open("sb_config.json", encoding="utf-8"))
    assert cfg["strategies"][STRAT]["capital"] == 80 and cfg["strategies"][STRAT]["max_open"] == 5
    sb_app.bot.allow_live = False
    r = cli.post("/api/mode", json={"mode": "live", "confirm": "我确认开实盘"})
    assert r.status_code == 400 and "SB_ALLOW_LIVE" in r.json()["detail"]
    sb_app.bot.allow_live = True
    r = cli.post("/api/mode", json={"mode": "live", "confirm": "随便"})
    assert r.status_code == 400 and "我确认开实盘" in r.json()["detail"]
    assert cli.post("/api/close", json={"coin": "NOPE"}).status_code == 404
    assert cli.get("/").status_code == 200


def test_too_small_stake_is_skipped(app_client):
    sb_app, cli = app_client
    bot = sb_app.bot
    bot.cfg["strategies"][STRAT]["capital"] = 50                       # 每单 5U：BTC 最小 0.01 张 ≈ 800U
    bot.inst = {"BTC-USDT-SWAP": {"tick": 0.1, "ctVal": 0.01, "lotSz": 0.01, "minSz": 0.01, "listTime": 0, "state": "live"}}
    bot.coins = ["BTC-USDT-SWAP"]
    bot.prices = {"BTC-USDT-SWAP": 80000.0}
    bot.last_candle = 1_000_000
    bot.sig[(STRAT, "BTC-USDT-SWAP")] = {"ts": 1_000_000, "enter": 1, "exit": 0}
    asyncio.run(bot._entries())
    assert bot.trades == []
    assert "最小下单" in bot.events[0]["text"]


class FakeLive:
    """假的欧易实盘接口：记录下了什么单（不连网、不用真密钥）"""

    def __init__(self):
        self.calls = []
        self.pos = {}

    def account(self):
        return 1000.0, 900.0

    def prepare(self, inst, lev, mgn="isolated"):
        self.calls.append(("prepare", inst, lev, mgn))

    def open(self, inst, side, contracts, stop, target=None, mgn="isolated"):
        self.calls.append(("open", inst, side, contracts, round(stop, 2), target, mgn))
        self.pos[inst] = contracts
        return {"fill": 80010.0, "contracts": contracts, "order_id": "o1", "algo_id": "a1"}

    def close(self, inst, side_open, contracts, algo_id="", mgn="isolated"):
        self.calls.append(("close", inst, side_open, contracts, algo_id, mgn))
        self.pos.pop(inst, None)
        return 82000.0

    def closed_pnl(self, inst, since):
        return {"pnl": 2.3, "exit": 82001.0, "fee": -0.16, "funding": 0.0}

    def positions(self):
        return {i: [{"pos": n}] for i, n in self.pos.items()}


def test_live_open_close_with_fake_exchange(app_client):
    sb_app, cli = app_client
    bot = sb_app.bot
    bot.mode = "live"
    bot.live = FakeLive()
    bot.inst = {"BTC-USDT-SWAP": {"tick": 0.1, "ctVal": 0.01, "lotSz": 0.01, "minSz": 0.01, "listTime": 0, "state": "live"}}
    bot.coins = ["BTC-USDT-SWAP"]
    bot.prices = {"BTC-USDT-SWAP": 80000.0}
    bot.last_candle = 2_000_000
    bot.sig[(STRAT, "BTC-USDT-SWAP")] = {"ts": 2_000_000, "enter": 1, "exit": 0}
    asyncio.run(bot._entries())
    op = [c for c in bot.live.calls if c[0] == "open"][0]
    assert op[1:4] == ("BTC-USDT-SWAP", 1, 0.12) and op[5] is None and op[6] == "isolated"
    assert op[4] == pytest.approx(80000 * 0.79, abs=0.01)           # 交易所保护止损：比策略止损再远 1%
    assert ("prepare", "BTC-USDT-SWAP", 1, "isolated") in bot.live.calls        # 1 倍杠杆
    t = bot.trades[0]
    assert t.open_rate == 80010.0 and t.algo_id == "a1"
    bot.prices["BTC-USDT-SWAP"] = 82500.0
    asyncio.run(bot._exits())
    bot.prices["BTC-USDT-SWAP"] = 82300.0
    asyncio.run(bot._exits())
    cl = [c for c in bot.live.calls if c[0] == "close"][0]
    assert cl == ("close", "BTC-USDT-SWAP", 1, 0.12, "a1", "isolated")
    rows = cli.get("/api/trades").json()["rows"]
    assert rows[0]["mode"] == "live" and rows[0]["pnl"] == 2.3 and rows[0]["close"] == 82001.0   # 用欧易记的真实盈亏


def test_live_reconcile_marks_exchange_closed(app_client):
    sb_app, cli = app_client
    bot = sb_app.bot
    bot.mode = "live"
    bot.live = FakeLive()
    t = E.Trade("ETH-USDT-SWAP", STRAT, 1, 3000.0, 0.1, 0.0005, 0.0005, tick=0.01, stake=300.0)
    t.contracts = 1
    bot.trades = [t]
    bot.live.pos = {"SOL-USDT-SWAP": 3}                                     # 交易所上 ETH 已经没了；SOL 是手动开的
    asyncio.run(bot._reconcile())
    assert bot.trades == []
    assert bot.foreign == {"SOL-USDT-SWAP"}
    rows = cli.get("/api/trades").json()["rows"]
    assert rows[0]["reason"] == "exchange" and rows[0]["pnl"] == 2.3
    # 手动开的币不再开
    bot.inst = {"SOL-USDT-SWAP": {"tick": 0.01, "ctVal": 1, "lotSz": 0.01, "minSz": 0.01, "listTime": 0, "state": "live"}}
    bot.coins = ["SOL-USDT-SWAP"]
    bot.prices = {"SOL-USDT-SWAP": 150.0}
    bot.last_candle = 3_000_000
    bot.sig[(STRAT, "SOL-USDT-SWAP")] = {"ts": 3_000_000, "enter": 1, "exit": 0}
    asyncio.run(bot._entries())
    assert bot.trades == [] and not [c for c in bot.live.calls if c[0] == "open"]


def test_mode_switch_blocked_with_open_trades(app_client):
    sb_app, cli = app_client
    bot = sb_app.bot
    bot.allow_live = True
    bot.trades = [E.Trade("ETH-USDT-SWAP", STRAT, 1, 3000.0, 0.1, 0.0005, 0.0005, stake=300.0)]
    r = cli.post("/api/mode", json={"mode": "live", "confirm": "我确认开实盘"})
    assert r.status_code == 400 and "没平" in r.json()["detail"]


def _mk_strat(tmp_path, body, name="Cb"):
    src = "from freqtrade.strategy import IStrategy\nfrom datetime import timedelta\nclass %s(IStrategy):\n" % name + body
    p = tmp_path / f"{name}.py"
    p.write_text(src)
    return L.Strategy(name, str(p))


def test_custom_stoploss_and_confirm_exit_follow_freqtrade(tmp_path):
    body = '''
    minimal_roi = {"0": 10}
    stoploss = -0.5
    use_custom_stoploss = True
    use_sell_signal = True
    def custom_stoploss(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        # 开仓 30 分钟后，止损收紧到当前价下方 1%
        return -0.01 if current_time - timedelta(minutes=30) >= trade.open_date_utc else 1
    def confirm_trade_exit(self, pair, trade, order_type, amount, rate, time_in_force, exit_reason, current_time, **kwargs):
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        self.seen.append(len(df))
        return exit_reason != "exit_signal"           # 拒绝所有卖出信号平仓
    seen = []
    def populate_indicators(self, df, m): return df
    def populate_buy_trend(self, df, m):
        df.loc[df.index == 0, "buy"] = 1
        return df
    def populate_sell_trend(self, df, m):
        df.loc[df.index == 2, "sell"] = 1
        return df
'''
    st = _mk_strat(tmp_path, body)
    assert st.unsupported == [] and st.use_custom_stoploss and st.has_confirm_exit
    ts = pd.date_range("2025-01-01", periods=20, freq="5min", tz="UTC")
    px = [100.0] * 8 + [99.5, 98.0] + [98.0] * 10
    d = pd.DataFrame({"date": ts, "open": px, "high": [p + 0.2 for p in px], "low": [p - 0.2 for p in px], "close": px, "volume": 1.0})
    a, full = st.analyze(d, "X/USDT", keep_full=True)
    out = E.Backtest(st, max_open=1, fee=0.0, ticks={"X/USDT": 0.01}).run({"X/USDT": a}, ["X/USDT"], full={"X/USDT": full}, startup=0)
    assert len(out) == 1
    o = out[0]
    # 第 3 根K线收盘有卖出信号，但被 confirm_trade_exit 拒绝；30 分钟后止损收紧，跌下去被止损。
    # 自定义止损把止损抬高过，freqtrade 记成 trailing_stop_loss（RalliV1 的 freqtrade 回测里也是这样）
    assert o["exit_reason"] == "trailing_stop_loss" and o["open_ts"] == int(ts[1].timestamp() * 1000)
    assert o["close_ts"] == int(ts[9].timestamp() * 1000)
    assert st.s.seen and st.s.seen[0] == 3           # 回调里看到的最新K线 = 上一根收盘的（第 3 根K线开盘时只看到前 3 根）


def test_custom_exit_reason_is_recorded(tmp_path):
    body = '''
    minimal_roi = {"0": 10}
    stoploss = -0.5
    def custom_sell(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if current_time - trade.open_date_utc >= timedelta(minutes=15):
            return "my_time_exit"
    def populate_indicators(self, df, m): return df
    def populate_buy_trend(self, df, m):
        df.loc[df.index == 0, "buy"] = 1
        return df
    def populate_sell_trend(self, df, m): return df
'''
    st = _mk_strat(tmp_path, body, "Cx")
    assert st.has_custom_exit
    ts = pd.date_range("2025-01-01", periods=10, freq="5min", tz="UTC")
    d = pd.DataFrame({"date": ts, "open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0, "volume": 1.0})
    a = st.analyze(d, "X/USDT")
    out = E.Backtest(st, max_open=1, fee=0.0).run({"X/USDT": a}, ["X/USDT"])
    assert out[0]["exit_reason"] == "my_time_exit" and out[0]["trade_duration"] == 15
