import time
import math
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "orderflow"))

import of_core as C  # noqa: E402
import of_engine as E  # noqa: E402


def mkbar(t, o, h, l, c, rows):
    b = C.Bar(t=t, o=o, h=h, l=l, c=c, closed=True)
    b.rows = {r: list(v) for r, v in rows.items()}
    return b


def test_diagonal_imbalance_compares_ask_with_bid_one_row_below():
    b = mkbar(0, 100, 103, 100, 103, {100: [10, 1], 101: [1, 40], 102: [20, 5]})
    buy, sell = C.imbalances(b)
    assert 101 in buy            # ask 40 >= 3 x bid(100)=10
    assert 102 not in buy        # ask 5 < 3 x bid(101)=1? 5>=3 but below volume floor
    assert 102 in sell           # bid 20 >= 3 x ask(103)=0


def test_stacked_runs():
    assert C.stacked({1, 2, 3, 7, 8}, 3) == [(1, 3)]
    assert C.stacked({1, 2, 4, 5, 6, 7}, 3) == [(4, 7)]


def test_volume_profile_poc_and_value_area():
    b = mkbar(0, 0, 0, 0, 0, {10: [1, 1], 11: [5, 5], 12: [30, 30], 13: [5, 5], 14: [1, 1]})
    vp = C.volume_profile([b], 1.0)
    assert vp["poc"] == 12.5
    assert vp["val"] <= 12 and vp["vah"] >= 13


def _series(n=80):
    bars = []
    p = 100.0
    for i in range(n):
        o = p
        p = p + (1 if i % 7 < 4 else -1.5)
        h, l = max(o, p) + 0.5, min(o, p) - 0.5
        rows = {int(l) + k: [float((i * 7 + k) % 5 + 1), float((i * 3 + k) % 6 + 1)] for k in range(int(h - l) + 1)}
        bars.append(mkbar(i * 300_000, o, h, l, p, rows))
    return bars


def test_detector_signals_do_not_depend_on_future_bars():
    bars = _series()
    d1 = C.Detector(1.0)
    first = [[(s.kind, s.side) for s in d1.on_bar(copy.deepcopy(b))] for b in bars]
    changed = copy.deepcopy(bars)
    for b in changed[50:]:          # 改掉后面的K线
        b.c, b.h = b.c + 20, b.h + 20
    d2 = C.Detector(1.0)
    second = [[(s.kind, s.side) for s in d2.on_bar(b)] for b in changed]
    assert first[:50] == second[:50]


def test_zone_age_keeps_counting_after_bar_list_is_trimmed():
    d = C.Detector(1.0)
    for b in _series(600):
        d.on_bar(b)
    assert d.n == 600
    assert all(d.n - z["born"] <= d.p["zone_life"] for z in d.zones)


def test_risk_sizing_and_daily_limit():
    cfg = dict(E.DEFAULT_CFG)
    r = E.Risk(cfg)
    a = E.Account(equity=1000, start_equity=1000)
    qty = r.size(a, 100.0, 99.0)              # 0.5% of 1000 = 5U risk / 1U stop
    assert abs(qty - 5) < 1e-9
    assert r.size(a, 100.0, 99.999) <= 1000 * cfg["max_leverage"] / 100 + 1e-9
    a.day_pnl = -31
    ok, _ = r.can_open(a, "BTC-USDT-SWAP", 1)
    assert not ok


def test_paper_exit_rules():
    pb = E.PaperBroker()
    pos = E.Position("X", "absorption", 1, 1.0, 100, 99, 102, 0, 10**12)
    assert pb.check_exit(pos, 98.9) == "止损"
    assert pb.check_exit(pos, 102.1) == "止盈"
    assert pb.check_exit(pos, 100.5) is None


def test_live_requires_permission_and_phrase(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "STATE_FILE", str(tmp_path / "s.json"))
    app = E.OrderFlowApp({}, None, {"key": "k", "secret": "s", "passphrase": "p"}, allow_live=False)
    ok, msg = app.enable_live("我确认实盘")
    assert not ok and app.broker == "paper"
    app2 = E.OrderFlowApp({}, None, {"key": "k", "secret": "s", "passphrase": "p"}, allow_live=True)
    ok, msg = app2.enable_live("随便")
    assert not ok and app2.broker == "paper"


def test_auto_trading_off_never_opens(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(E, "TRADE_LOG", str(tmp_path / "t.jsonl"))
    app = E.OrderFlowApp({"auto": False, "enabled": list(C.SIGNAL_NAMES)}, None, None, False)

    class Eng:
        inst, tf = "BTC-USDT-SWAP", "5m"
    s = C.Signal("absorption", 1, 99.0, 102.0, 0)
    app.try_open(Eng(), s, {}, 100.0, 0)
    assert app.acct.positions == []


def _flat(n=30, px=100.0):
    out = []
    for i in range(n):
        b = mkbar(i * 300_000, px, px + 1, px - 1, px, {int(px) - 1: [5.0, 5.0], int(px): [5.0, 5.0]})
        out.append(b)
    return out


def test_liquidation_cascade_long_signal():
    d = C.Detector(1.0, enabled=["liq_cascade"])
    for b in _flat():
        d.on_bar(b)
    b = mkbar(30 * 300_000, 100, 100.5, 95, 99, {95: [20.0, 5.0], 96: [10.0, 5.0], 99: [5.0, 5.0]})
    b.liq_long = 10_000.0          # 100 万美元级别的多单爆仓
    sigs = d.on_bar(b)
    assert [(s.kind, s.side) for s in sigs] == [("liq_cascade", 1)]
    assert sigs[0].stop < 95


def test_book_wall_needs_wall_price():
    d = C.Detector(1.0, enabled=["book_wall"])
    for b in _flat():
        d.on_bar(b)
    b = mkbar(30 * 300_000, 100, 100.5, 98.2, 99.5, {98: [9.0, 1.0], 99: [5.0, 2.0]})
    assert d.on_bar(b) == []
    b2 = mkbar(31 * 300_000, 100, 100.5, 98.2, 99.5, {98: [9.0, 1.0], 99: [5.0, 2.0]})
    b2.wall_bid = 98.0
    sigs = d.on_bar(b2)
    assert sigs and sigs[0].side == 1 and sigs[0].stop <= 96.0


# ---------------------------------------------------------------- 实盘流程（假交易所，不连欧易）
import asyncio


class FakeLive:
    hedged = False

    def __init__(self, equity=1000.0, avail=1000.0):
        self.eq, self.av = equity, avail
        self.pos = {}            # inst -> 张数（带方向）
        self.algos = {}
        self.calls = []
        self.pnl = {}

    def account(self):
        return self.eq, self.av

    def positions(self):
        return {k: [{"pos": v}] for k, v in self.pos.items() if v}

    def contracts_for(self, inst, qty):
        return round(qty / 0.01, 2), 0.01, 0.01        # 每张 0.01 币

    def prepare(self, inst, lev, mgn="isolated"):
        self.mgn = mgn
        self.calls.append(("prepare", inst, lev))

    def open(self, inst, side, n, stop, target, mgn="isolated"):
        self.open_mgn = mgn
        self.calls.append(("open", inst, side, n, stop, target))
        self.pos[inst] = side * n
        self.algos[inst] = "A1"
        return {"fill": 100.0, "contracts": n, "order_id": "O1", "algo_id": "A1"}

    def close(self, inst, side_open, n, algo_id="", mgn="isolated"):
        self.close_mgn = mgn
        self.calls.append(("close", inst, n))
        self.pos[inst] = self.pos.get(inst, 0) - side_open * n
        return 101.0

    def cancel_algo(self, inst, algo_id):
        self.calls.append(("cancel", inst, algo_id))

    def add(self, inst, side, n, mgn="isolated"):
        self.calls.append(("add", inst, side, n))
        self.pos[inst] = self.pos.get(inst, 0) + side * n
        return {"fill": self.add_px, "contracts": n}

    add_px = 98.0

    def place_oco(self, inst, side, n, stop, target, mgn="isolated"):
        self.calls.append(("oco", inst, n, stop, target))
        return "A2"

    def closed_pnl(self, inst, since):
        return self.pnl.get(inst)


class FakeEng:
    def __init__(self, inst="BTC-USDT-SWAP"):
        self.inst, self.tf, self.last = inst, "5m", 100.0
        self.pb = type("PB", (), {"trades_today": 0, "record_result": lambda self, w: None})()


def _live_app(tmp_path, monkeypatch, **cfg):
    monkeypatch.setattr(E, "STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(E, "TRADE_LOG", str(tmp_path / "t.jsonl"))
    app = E.OrderFlowApp({"auto": True, "enabled": ["absorption", "pb_absorb"], "max_positions": 2, **cfg}, None, None, True)
    app.live = FakeLive()
    app.live_confirmed = True
    app.cfg["mode"] = "live"
    app.live_equity, app.live_avail, app.live_day_start = 1000.0, 1000.0, 1000.0
    return app


def _run(coro_fn):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro_fn())
    finally:
        loop.close()


def test_live_open_then_exchange_stop_records_real_pnl(tmp_path, monkeypatch):
    app = _live_app(tmp_path, monkeypatch)
    eng = FakeEng()
    app.engines[eng.inst] = eng

    async def go():
        s = C.Signal("absorption", 1, 99.0, 102.0, 0)
        d = {}
        app.try_open(eng, s, d, 100.0, 0)
        assert eng.inst in app.opening
        await asyncio.sleep(0.05)
        assert d.get("traded") and len(app.acct.positions) == 1
        pos = app.acct.positions[0]
        assert pos.live and pos.algo_id == "A1" and pos.contracts > 0
        # 交易所止损触发：仓位没了，历史持仓给出真实盈亏
        app.live.pos[eng.inst] = 0
        app.live.pnl[eng.inst] = {"pnl": -5.3, "exit": 98.9, "fee": -0.1, "funding": 0}
        pos.t_open = 0
        await app.reconcile_live()
        assert app.acct.positions == []
        assert app.history[-1]["pnl"] == -5.3 and app.history[-1]["real"] and app.history[-1]["why"] == "止损"
    _run(go)


def test_live_blocks_external_position_and_small_margin(tmp_path, monkeypatch):
    app = _live_app(tmp_path, monkeypatch)
    eng = FakeEng()
    app.external = {eng.inst}
    d = {}
    app.try_open(eng, C.Signal("absorption", 1, 99.0, 102.0, 0), d, 100.0, 0)
    assert d["skip"] == "账户里这个币已经有别的仓位"
    app.external = set()
    app.live_avail = 1.0

    async def go():
        d2 = {}
        app.try_open(eng, C.Signal("absorption", 1, 99.0, 102.0, 0), d2, 100.0, 0)
        await asyncio.sleep(0.05)
        assert "可用保证金不够" in d2["skip"] and app.acct.positions == []
        assert not any(c[0] == "open" for c in app.live.calls)
    _run(go)


def test_live_time_exit_closes_and_cancels_algo(tmp_path, monkeypatch):
    app = _live_app(tmp_path, monkeypatch)
    eng = FakeEng()
    app.engines[eng.inst] = eng

    async def go():
        app.try_open(eng, C.Signal("absorption", 1, 99.0, 102.0, 0), {}, 100.0, 0)
        await asyncio.sleep(0.05)
        pos = app.acct.positions[0]
        app.live.pnl[eng.inst] = {"pnl": 1.2, "exit": 100.6, "fee": -0.1, "funding": 0}
        app.check_exits(eng, 100.5, pos.max_until + 1)
        await asyncio.sleep(2.8)
        kinds = [c[0] for c in app.live.calls]
        assert "close" in kinds and ("cancel", eng.inst, "A1") in app.live.calls
        assert app.acct.positions == [] and app.history[-1]["why"] == "到时间"
    _run(go)


def test_live_playbook_half_close_moves_stop_to_breakeven(tmp_path, monkeypatch):
    app = _live_app(tmp_path, monkeypatch)
    eng = FakeEng()
    app.engines[eng.inst] = eng

    async def go():
        app.try_open(eng, C.Signal("pb_absorb", 1, 99.0, 103.0, 0), {}, 100.0, 0)
        await asyncio.sleep(0.05)
        pos = app.acct.positions[0]
        n0 = pos.contracts
        app.check_exits(eng, 101.1, 1)          # 到 1R（100 + 1）
        await asyncio.sleep(0.05)
        assert pos.half_done and pos.stop == pos.entry and pos.contracts < n0
        assert any(c[0] == "oco" and c[3] == pos.entry for c in app.live.calls)
    _run(go)


def test_paper_daily_loss_limit_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "STATE_FILE", str(tmp_path / "s.json"))
    app = E.OrderFlowApp({"auto": True, "enabled": ["absorption"]}, None, None, False)
    app._roll_day()
    app.acct.day_pnl = -100
    d = {}
    app.try_open(FakeEng(), C.Signal("absorption", 1, 99.0, 102.0, 0), d, 100.0, 0)
    assert d["skip"].startswith("今天亏损")


def test_signal_tf_switch_is_instant(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(E, "TRADE_LOG", str(tmp_path / "t.jsonl"))
    app = E.OrderFlowApp({"auto": False, "enabled": list(C.SIGNAL_NAMES)}, None, None, False)
    eng = E.SymbolEngine(app, "BTC-USDT-SWAP", "5m", {t: 1.0 for t in E.VIEW_TFS}, 0.01)
    for t in range(0, 4 * 3_600_000, 20_000):
        for b in eng.builders.values():
            b.add(100.0, 1.0, True, t)
    n15 = len(eng.builders["15m"].bars)
    eng.set_signal_tf("15m")
    assert eng.tf == "15m" and eng.builder is eng.builders["15m"]
    assert eng.det.n == n15                                   # 识别器已经用 15 分钟历史K线重新跑过
    assert eng.builders["15m"].on_close.fns == [eng._on_bar]
    assert eng.builders["5m"].on_close.fns == []
    assert eng.builders["1m"].on_close.fns == [eng._on_pb_bar]      # 实战打法还在 1 分钟
    eng.set_signal_tf("1m")
    assert eng.builders["1m"].on_close.fns == [eng._on_bar, eng._on_pb_bar]   # 两种打法挂在一起


def test_cross_stats_spot_vs_perp_and_context():
    import time as _t
    import of_xfeed as X
    x = X.CrossHub()
    now = int(_t.time() // 60)
    m = x.min["BTC-USDT-SWAP"] = {}
    for k in range(now - 300, now + 1):
        # 合约：主动买 40%（偏卖）；现货：主动买 70%（偏买）
        m[k] = [40.0, 100.0, 35.0, 50.0, 100.0, 100.0]
    s = x.stats("BTC-USDT-SWAP", okx_min={k: [10.0, 10.0] for k in range(now - 300, now + 1)})
    assert abs(s["pf_60"] - (-0.2)) < 1e-9 and abs(s["sf_60"] - 0.4) < 1e-9
    assert abs(s["div_60"] - 0.6) < 1e-9
    assert s["all_pf_60"] < 0                      # 币安 40 买/60 卖 + 欧易 10/10
    assert abs(s["spot_share"] - 50 / 150) < 1e-9
    # 大背景：前两天 POC 在 100，之后价格一直在 120 以上 → 100 是没被碰过的 POC
    rows = []
    t0 = (int(_t.time() * 1000) // 86_400_000 - 3) * 86_400_000
    for i in range(96 * 3 + 10):
        px = 100.0 if i < 96 * 2 else 125.0
        rows.append([t0 + i * 900_000, px, px + 0.5, px - 0.5, px, 1, 0, 1000.0])
    c = X.context_from_15m(rows)
    assert any(abs(p - 100) < 1 for p in c["naked_pocs"]) and c["trend"] == "上涨"


def test_cross_venue_liquidation_goes_into_bars(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(E, "TRADE_LOG", str(tmp_path / "t.jsonl"))
    app = E.OrderFlowApp({"auto": False, "enabled": list(C.SIGNAL_NAMES)}, None, None, False)
    eng = E.SymbolEngine(app, "BTC-USDT-SWAP", "5m", {t: 1.0 for t in E.VIEW_TFS}, 0.01)
    eng.on_trade(100.0, 1, True, 1_000)
    eng.on_liq_usd(100.0, 5_000.0, -1, 1_500, "币安")
    assert eng.builders["5m"].cur.liq_long == 50.0
    assert eng.ext["liqs"][-1]["venue"] == "币安"


def test_flush_spot_signal_and_paper_trade(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(E, "TRADE_LOG", str(tmp_path / "t.jsonl"))
    app = E.OrderFlowApp({"auto": True, "enabled": ["flush_spot"], "flush_filters": {"oi24": False}}, None, None, False)
    app.btc_bull = True                          # 大盘多头

    class X:          # 假的全网数据：币安现货 1 小时主动买 > 卖 10%
        def stats(self, inst, okx_min=None):
            return {"sf_60": 0.10}
    app.xx = X()
    app.cfg["squeeze"] = {"rise": 9.9}          # 这个测试只看清洗接盘
    eng = E.SymbolEngine(app, "SOL-USDT-SWAP", "5m", {t: 0.01 for t in E.VIEW_TFS}, 1.0)
    app.engines = {eng.inst: eng}
    t0 = 10 * 86_400_000
    # 1 小时从 100 跌到 96（-4%），持仓量 1 小时降 8%
    eng.ext["oi_hist"] = [(t0, 1000.0), (t0 + 60 * 60_000, 920.0)]
    for k in range(13):
        px = 100.0 - k * 4.0 / 13
        eng.on_trade(px, 1, False, t0 + k * 300_000)
    eng.on_trade(97.0, 1, True, t0 + 13 * 300_000)      # 收盘第 14 根 → 检查 → 出信号（这一笔反弹在挂单价上面，不成交）
    sig = [s for s in eng.signals if s["kind"] == "flush_spot"]
    assert len(sig) == 1 and sig[0]["side"] == 1
    sig_px = sig[0]["price"]
    eng.on_trade(sig_px + 0.5, 1, True, t0 + 13 * 300_000 + 1000)     # 价格没回到挂单价：不成交
    assert app.acct.positions == []
    eng.on_trade(sig_px - 0.01, 1, False, t0 + 13 * 300_000 + 60_000)  # 回到挂单价：成交
    pos = app.acct.positions
    assert len(pos) == 1 and pos[0].kind == "flush_spot"
    assert pos[0].max_until - pos[0].t_open == 12 * 3600_000             # 拿 12 小时
    assert abs(pos[0].qty * sig_px - 1000 * 0.10) < 1.0                  # 每笔用权益 10%
    assert pos[0].stop < sig_px * (1 - 0.09)                             # 止损 = 3 倍跌幅
    # 12 小时内不重复
    eng.on_trade(95.0, 1, True, t0 + 16 * 300_000)
    assert len([s for s in eng.signals if s["kind"] == "flush_spot"]) == 1


def test_flush_limit_order_expires(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(E, "TRADE_LOG", str(tmp_path / "t.jsonl"))
    app = E.OrderFlowApp({"auto": True, "enabled": ["flush_spot"]}, None, None, False)

    class X:
        def stats(self, inst, okx_min=None):
            return {"sf_60": 0.10}
    app.xx = X()
    eng = E.SymbolEngine(app, "SOL-USDT-SWAP", "5m", {t: 0.01 for t in E.VIEW_TFS}, 1.0)
    t0 = 10 * 86_400_000
    eng.ext["oi_hist"] = [(t0, 1000.0), (t0 + 60 * 60_000, 940.0)]       # -6%
    for k in range(13):
        eng.on_trade(100.0 - k * 3.0 / 13, 1, False, t0 + k * 300_000)
    eng.on_trade(98.0, 1, True, t0 + 13 * 300_000)
    sig = [s for s in eng.signals if s["kind"] == "flush_spot"]
    assert len(sig) == 1
    eng.on_trade(sig[0]["price"] + 1, 1, True, t0 + 13 * 300_000 + 11 * 60_000)   # 超过 5 分钟还没回来 → 撤单
    assert app.acct.positions == [] and sig[0].get("skip") == "限价没成交，已撤"


def test_big_trade_threshold_not_recomputed_every_trade(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(E, "TRADE_LOG", str(tmp_path / "t.jsonl"))
    app = E.OrderFlowApp({"auto": False, "enabled": []}, None, None, False)
    eng = E.SymbolEngine(app, "BTC-USDT-SWAP", "5m", {t: 1.0 for t in E.VIEW_TFS}, 0.01)
    calls = {"n": 0}
    real_sorted = sorted

    def counting_sorted(x, *a, **k):
        calls["n"] += 1
        return real_sorted(x, *a, **k)
    import builtins
    monkeypatch.setattr(builtins, "sorted", counting_sorted)
    for i in range(6000):                   # 每笔不同毫秒 → 6000 笔合并后的成交
        eng.on_trade(100.0, 1, i % 2 == 0, 1_000 + i)
    monkeypatch.setattr(builtins, "sorted", real_sorted)
    assert len(eng._sizes) == 3000
    assert calls["n"] <= 6000 // 300 + 50   # 大约每 300 笔一次，而不是每笔一次


def test_live_cross_margin_used_for_open_and_close(tmp_path, monkeypatch):
    app = _live_app(tmp_path, monkeypatch)
    app.cfg["margin_mode"] = "cross"
    eng = FakeEng()
    app.engines[eng.inst] = eng

    async def go():
        app.try_open(eng, C.Signal("absorption", 1, 99.0, 102.0, 0), {}, 100.0, 0)
        await asyncio.sleep(0.05)
        pos = app.acct.positions[0]
        assert pos.mgn == "cross" and app.live.mgn == "cross" and app.live.open_mgn == "cross"
        app.cfg["margin_mode"] = "isolated"          # 改设置不影响已经开着的单
        app.live.pnl[eng.inst] = {"pnl": 1.0, "exit": 100.5, "fee": -0.1, "funding": 0}
        app.check_exits(eng, 100.5, pos.max_until + 1)
        await asyncio.sleep(1.7)
        assert app.live.close_mgn == "cross"
    _run(go)


def test_squeeze_long_signal_market_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(E, "TRADE_LOG", str(tmp_path / "t.jsonl"))
    app = E.OrderFlowApp({"auto": True, "enabled": ["squeeze_long"]}, None, None, False)
    app.btc_bull = True                          # 大盘多头

    class X:
        def stats(self, inst, okx_min=None):
            return {"sf_60": -0.2}                  # 轧空追多不看现货
    app.xx = X()
    eng = E.SymbolEngine(app, "DOGE-USDT-SWAP", "5m", {t: 0.0001 for t in E.VIEW_TFS}, 1.0)
    app.engines = {eng.inst: eng}
    t0 = 10 * 86_400_000
    eng.ext["oi_hist"] = [(t0, 1000.0), (t0 + 60 * 60_000, 970.0)]       # 持仓 -3%（空单被平）
    for k in range(13):
        eng.on_trade(0.100 + k * 0.004 / 12, 1, True, t0 + k * 300_000)  # 1 小时涨 4%
    eng.on_trade(0.1045, 1, True, t0 + 13 * 300_000)                    # 收盘 → 出信号
    sig = [s for s in eng.signals if s["kind"] == "squeeze_long"]
    assert len(sig) == 1 and sig[0]["side"] == 1
    eng.on_trade(0.1046, 1, True, t0 + 13 * 300_000 + 500)              # 下一笔成交市价进场
    pos = app.acct.positions
    assert len(pos) == 1 and pos[0].kind == "squeeze_long"
    assert pos[0].max_until - pos[0].t_open == 12 * 3600_000
    rise = sig[0]["price"] / 0.100 - 1
    assert abs(pos[0].stop - sig[0]["price"] * (1 - rise)) < 1e-9         # 止损 = 涨幅那么远
    assert abs(pos[0].qty * pos[0].entry - 1000 * 0.10) < 1.0             # 每笔 10%


def test_live_position_due_while_live_off_is_not_fake_closed(tmp_path, monkeypatch):
    app = _live_app(tmp_path, monkeypatch)
    eng = FakeEng()
    app.engines[eng.inst] = eng

    async def go():
        app.try_open(eng, C.Signal("absorption", 1, 99.0, 102.0, 0), {}, 100.0, 0)
        await asyncio.sleep(0.05)
        pos = app.acct.positions[0]
        app.live_confirmed = False                    # 像重启后还没重新打开实盘
        app.check_exits(eng, 100.5, pos.max_until + 1)
        await asyncio.sleep(0.1)
        assert app.acct.positions == [pos]            # 没有在本地假装平掉
        assert "close" not in [c[0] for c in app.live.calls]
        assert any("实盘还没打开" in l for l in app.log)
    _run(go)


def test_heartbeat_line(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(E, "TRADE_LOG", str(tmp_path / "t.jsonl"))
    monkeypatch.setattr(E, "LOG_FILE", str(tmp_path / "log.txt"))
    app = E.OrderFlowApp({"auto": False, "enabled": []}, None, None, False)
    app.heartbeat()
    assert "运行中" in app.log[-1] and "持仓 0 单" in app.log[-1]
    assert "运行中" in open(tmp_path / "log.txt", encoding="utf-8").read()


def test_combo_strategies_skip_stock_contracts(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(E, "TRADE_LOG", str(tmp_path / "t.jsonl"))
    monkeypatch.setattr(E, "LOG_FILE", str(tmp_path / "log.txt"))
    app = E.OrderFlowApp({"auto": True, "enabled": ["squeeze_long"]}, None, None, False)
    eng = E.SymbolEngine(app, "AAOI-USDT-SWAP", "5m", {t: 0.01 for t in E.VIEW_TFS}, 1.0)
    eng.category = "3"
    d = {}
    app.try_open(eng, C.Signal("squeeze_long", 1, 100.0, 300.0, 0), d, 110.0, 0)
    assert app.acct.positions == [] and "股票" in d["skip"]


def test_auto_guard_pauses_losing_strategy(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(E, "TRADE_LOG", str(tmp_path / "t.jsonl"))
    monkeypatch.setattr(E, "LOG_FILE", str(tmp_path / "log.txt"))
    app = E.OrderFlowApp({"auto": True, "enabled": ["squeeze_long", "flush_spot"], "guard_n": 5, "guard_pf": 0.8}, None, None, False)
    saved = []
    app.save_cfg_cb = lambda: saved.append(1)
    for k in range(5):
        pos = E.Position("SOL-USDT-SWAP", "squeeze_long", 1, 1.0, 100.0, 95.0, 300.0, 0, 1, "paper", False)
        app.acct.positions.append(pos)
        app._record_close(pos, 96.0 if k < 4 else 101.0, "止损", 1000 + k)
    assert "squeeze_long" not in app.cfg["enabled"] and "flush_spot" in app.cfg["enabled"]
    assert saved and any("自动刹车" in l for l in app.log)


def test_btc_regime_gate_blocks_long_combos_in_bear(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(E, "TRADE_LOG", str(tmp_path / "t.jsonl"))
    monkeypatch.setattr(E, "LOG_FILE", str(tmp_path / "log.txt"))
    app = E.OrderFlowApp({"auto": True, "enabled": ["flush_spot"], "btc_ma_days": 200, "flush_filters": {"oi24": False}}, None, None, False)
    eng = E.SymbolEngine(app, "SOL-USDT-SWAP", "5m", {t: 0.01 for t in E.VIEW_TFS}, 1.0)
    sig = C.Signal("flush_spot", 1, 90.0, 120.0, 0)
    for bull, opened in ((None, False), (False, False), (True, True)):
        app.btc_bull = bull
        d = {}
        app.try_open(eng, sig, d, 100.0, 0)
        assert bool(app.acct.positions) == opened, (bull, d)
        if not opened:
            assert "大盘过滤" in d["skip"]
    # 关掉过滤（0）就不管大盘
    app2 = E.OrderFlowApp({"auto": True, "enabled": ["flush_spot"], "btc_ma_days": 0, "flush_filters": {"oi24": False}}, None, None, False)
    app2.btc_bull = False
    app2.try_open(eng, sig, {}, 100.0, 0)
    assert len(app2.acct.positions) == 1


def test_btc_regime_calc_uses_only_closed_daily_candles():
    # 欧易日线：新的在前；最后一个字段 1 = 已收盘。今天没收完的那根（涨到 1000）不能算进去
    rows = [[str(86_400_000 * 10), "0", "0", "0", "1000", "0", "0", "0", "0"]]
    rows += [[str(86_400_000 * k), "0", "0", "0", str(100 + k), "0", "0", "0", "1"] for k in range(9, -1, -1)]
    bull, px, ma = E.btc_regime_calc(rows, 5)
    assert px == 109 and ma == 107 and bull is True
    rows[1][4] = "100"                                                # 昨收跌到均线下方
    assert E.btc_regime_calc(rows, 5)[0] is False
    assert E.btc_regime_calc(rows, 50)[0] is None                    # 日线不够


def test_momo_long_signal_ignores_btc_filter(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(E, "TRADE_LOG", str(tmp_path / "t.jsonl"))
    monkeypatch.setattr(E, "LOG_FILE", str(tmp_path / "log.txt"))
    app = E.OrderFlowApp({"auto": True, "enabled": ["momo_long"], "btc_ma_days": 200}, None, None, False)
    app.btc_bull = False                         # 大盘空头也照做
    eng = E.SymbolEngine(app, "AKE-USDT-SWAP", "5m", {t: 0.0001 for t in E.VIEW_TFS}, 1.0)
    app.engines = {eng.inst: eng}
    t0 = 20 * 86_400_000
    H = 3_600_000
    eng.momo.seed_hours((t0 - k * H, 1000.0) for k in range(1, 8 * 24))        # 过去 8 天每小时成交 1000U
    for k in range(25 * 12, 0, -1):
        eng.momo.add(t0 - k * 300_000, 1.0, 1000 / 12)                          # 过去 25 小时价格 1.0
    for k in range(4):                                                         # 涨到 1.25 以上，放量
        eng.on_trade(1.25 + k * 0.01, 2000, True, t0 + k * 300_000)
    sig = [s for s in eng.signals if s["kind"] == "momo_long"]
    assert len(sig) == 1 and sig[0]["side"] == 1
    eng.on_trade(1.29, 1, True, t0 + 3 * 300_000 + 500)                       # 下一笔成交市价进场
    pos = app.acct.positions
    assert len(pos) == 1 and pos[0].kind == "momo_long"
    assert pos[0].max_until - pos[0].t_open == 24 * H                           # 拿 24 小时
    assert abs(pos[0].stop - sig[0]["price"] * 0.85) < 1e-9                    # 止损 15%
    assert abs(pos[0].qty * pos[0].entry - 1000 * 0.05) < 0.5                  # 每笔 5%
    for k in range(4, 30):                                                     # 24 小时内同一个币不再出
        eng.on_trade(1.35 + k * 0.01, 2000, True, t0 + k * 300_000)
    assert len([s for s in eng.signals if s["kind"] == "momo_long"]) == 1


def test_momo_tracker_needs_full_history():
    tr = E.MomoTracker()
    for k in range(100):
        tr.add(k * 300_000, 1.0, 10.0)
    assert all(math.isnan(x) for x in tr.state(99 * 300_000)[:1])              # 不够 24 小时：算不出


def test_live_partial_close_keeps_protection_and_retries(tmp_path, monkeypatch):
    app = _live_app(tmp_path, monkeypatch)
    eng = FakeEng()
    app.engines[eng.inst] = eng
    real_close = app.live.close

    def half_close(inst, side_open, n, algo_id="", mgn="isolated"):     # 交易所只成交一半
        return real_close(inst, side_open, n / 2, algo_id, mgn)
    app.live.close = half_close

    async def go():
        app.try_open(eng, C.Signal("absorption", 1, 99.0, 102.0, 0), {}, 100.0, 0)
        await asyncio.sleep(0.05)
        pos = app.acct.positions[0]
        n0 = app.live.pos[eng.inst]
        app.check_exits(eng, 100.5, pos.max_until + 1)
        await asyncio.sleep(1.5)
        assert ("cancel", eng.inst, "A1") not in app.live.calls      # 没平干净：止盈止损单留着
        assert app.acct.positions == [pos] and app.live.pos[eng.inst] == n0 / 2   # 仓位还记着，等 30 秒后再平剩下的
    _run(go)


def test_spot_flow_needs_complete_recent_data():
    import of_xfeed as X
    h = X.CrossHub.__new__(X.CrossHub)
    h.min, h.bybit, h.ctx = {}, {}, {}
    h.cb = {"premium": math.nan, "hist": []}
    now = int(time.time() // 60)
    full = {k: [1.0, 2.0, 1.2, 2.0, 1.0, 1.0] for k in range(now - 60, now + 1)}
    h.min = {"SOL-USDT-SWAP": full}
    assert abs(X.CrossHub.stats(h, "SOL-USDT-SWAP")["sf_60"] - 0.2) < 1e-9
    gap = {k: v for k, v in full.items() if k < now - 40}                   # 最近 40 分钟断线没数据
    h.min = {"SOL-USDT-SWAP": gap}
    assert math.isnan(X.CrossHub.stats(h, "SOL-USDT-SWAP")["sf_60"])


def test_flush_orderflow_filters():
    """清洗接盘的订单流过滤：默认开"持仓量 24 小时"；没数据 / 数据太旧 / 不满足都不开，满足才开；只管清洗接盘"""
    import time
    app = E.OrderFlowApp({}, None, None, False)
    assert app.cfg["flush_filters"]["oi24"] is True

    class Eng:
        inst = "SOL-USDT-SWAP"

        def xstats(self):
            return {"pf_1440": -0.03}
    e, now = Eng(), time.time() * 1000
    assert "还没读到" in app.flush_filter_block(e)
    app.oi24["SOL-USDT-SWAP"] = (0.05, now)
    assert "不满足" in app.flush_filter_block(e)
    app.oi24["SOL-USDT-SWAP"] = (-0.02, now)
    assert app.flush_filter_block(e) == ""
    app.oi24["SOL-USDT-SWAP"] = (-0.02, now - 3 * 3600_000)            # 3 小时前的数据算太旧
    assert "还没读到" in app.flush_filter_block(e)
    app.oi24["SOL-USDT-SWAP"] = (-0.02, now)
    app.cfg["flush_filters"] = {"oi24": True, "pf24": True, "btc_oi24": True}
    app.oi24["BTC-USDT-SWAP"] = (0.01, now)
    assert "BTC" in app.flush_filter_block(e)
    app.oi24["BTC-USDT-SWAP"] = (0.0, now)
    assert app.flush_filter_block(e) == ""
    app.cfg["flush_filters"] = {}
    app.oi24.clear()
    assert app.flush_filter_block(e) == ""                               # 全关 = 不过滤


def test_trap_short_open_and_cover_on_flush(tmp_path, monkeypatch):
    """多头摊平做空：条件全满足 → 做空（每笔 10%、止损 5%、最多 48 小时）；多头被清洗（1 小时跌超 2%、持仓量 1 小时降超 3%）→ 平；不受大盘过滤影响"""
    import time
    monkeypatch.setattr(E, "STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(E, "TRADE_LOG", str(tmp_path / "t.jsonl"))
    monkeypatch.setattr(E, "LOG_FILE", str(tmp_path / "log.txt"))
    app = E.OrderFlowApp({"auto": True, "enabled": ["trap_short"], "btc_ma_days": 200, "flush_filters": {}, "trap": {"mode": "orig"}}, None, None, False)
    app.btc_bull = True                                                        # 大盘多头也照做（不用大盘过滤）
    eng = E.SymbolEngine(app, "SOL-USDT-SWAP", "5m", {t: 0.0001 for t in E.VIEW_TFS}, 1.0)
    app.engines = {eng.inst: eng}
    t0, now = 20 * 86_400_000, time.time() * 1000
    for k in range(25 * 12, 0, -1):
        eng.momo.add(t0 - k * 300_000, 1.0, 100.0)                            # 过去 25 小时价格 1.0
    app.oi24[eng.inst] = (0.08, now)
    app.lsz[eng.inst] = (0.5, now)
    eng.ext["funding"] = 0.0001
    eng.ext["oi_hist"] = [(t0 - 3_600_000, 1e6)]
    for k in range(3):
        eng.on_trade(0.93 - k * 0.001, 10, False, t0 + k * 300_000)           # 跌到 0.93（-7%）
    eng.on_trade(0.928, 1, False, t0 + 2 * 300_000 + 500)
    sig = [s for s in eng.signals if s["kind"] == "trap_short"]
    pos = app.acct.positions
    assert len(sig) == 1 and len(pos) == 1 and pos[0].side == -1 and pos[0].kind == "trap_short"
    assert pos[0].max_until - pos[0].t_open == 48 * 3_600_000
    assert abs(pos[0].stop - sig[0]["price"] * 1.05) < 1e-9
    assert abs(pos[0].qty * pos[0].entry - 1000 * 0.10) < 1.0
    for k in range(3, 6):                                                      # 还没清洗：继续拿着、不加仓
        eng.on_trade(0.925, 10, False, t0 + k * 300_000)
    assert len(app.acct.positions) == 1 and len([s for s in eng.signals if s["kind"] == "trap_short"]) == 1
    eng.ext["oi_hist"] = [(t0 + 5 * 300_000 - 3_600_000, 1e6), (t0 + 6 * 300_000, 0.95e6)]   # 持仓量 1 小时 -5%
    app.oi24[eng.inst] = (0.01, time.time() * 1000)                           # 清洗后持仓量 24 小时不再大涨：平完不再开（条件还满足时回测和程序都会再开）
    for k in range(6, 19):                                                     # 1 小时内跌到 0.89（比 12 根前低 2% 以上）
        eng.on_trade(0.925 - (k - 5) * 0.003, 10, False, t0 + k * 300_000)
    assert app.acct.positions == []
    assert app.history and "多头被清洗" in app.history[-1]["why"] and app.history[-1]["pnl"] > 0


def test_trap_short_live_opens_short_and_auto_closes(tmp_path, monkeypatch):
    """实盘（假交易所）：多头摊平做空 → 开空并挂 5% 止损；多头被清洗 → 程序自己平空、撤掉止损单；另一单拿满 48 小时 → 到时间自己平"""
    import time
    monkeypatch.setattr(E, "LOG_FILE", str(tmp_path / "log.txt"))
    app = _live_app(tmp_path, monkeypatch, enabled=["trap_short"], btc_ma_days=0, flush_filters={}, max_positions=10, trap={"mode": "orig"})
    eng = E.SymbolEngine(app, "SOL-USDT-SWAP", "5m", {t: 0.0001 for t in E.VIEW_TFS}, 1.0)
    app.engines = {eng.inst: eng}
    t0, now = 20 * 86_400_000, time.time() * 1000

    async def go():
        for k in range(25 * 12, 0, -1):
            eng.momo.add(t0 - k * 300_000, 1.0, 100.0)
        app.oi24[eng.inst] = (0.08, now)
        app.lsz[eng.inst] = (0.5, now)
        eng.ext["funding"] = 0.0001
        eng.ext["oi_hist"] = [(t0 - 3_600_000, 1e6)]
        for k in range(3):
            eng.on_trade(0.93 - k * 0.001, 10, False, t0 + k * 300_000)
        await asyncio.sleep(0.05)
        opens = [c for c in app.live.calls if c[0] == "open"]
        assert len(opens) == 1 and opens[0][2] == -1                          # 开的是空单
        pos = app.acct.positions[0]
        assert pos.live and pos.kind == "trap_short" and pos.algo_id == "A1"
        assert opens[0][4] > 0.93                                              # 止损挂在上方
        eng.ext["oi_hist"] = [(t0 + 5 * 300_000 - 3_600_000, 1e6), (t0 + 6 * 300_000, 0.95e6)]
        app.oi24[eng.inst] = (0.01, time.time() * 1000)
        app.live.pnl[eng.inst] = {"pnl": 3.1, "exit": 0.9, "fee": -0.05, "funding": 0}
        for k in range(3, 19):
            eng.on_trade(0.925 - max(k - 5, 0) * 0.003, 10, False, t0 + k * 300_000)
        await asyncio.sleep(2.8)
        assert any(c[0] == "close" for c in app.live.calls)
        assert ("cancel", eng.inst, "A1") in app.live.calls
        assert app.acct.positions == [] and "多头被清洗" in app.history[-1]["why"]
        # 第二单：一直没清洗，拿满 48 小时自己平
        app.oi24[eng.inst] = (0.08, time.time() * 1000)
        app.live.calls.clear()
        d = {}
        app.try_open(eng, C.Signal("trap_short", -1, 1.05, 0.1, 0), d, 1.0, 0)
        await asyncio.sleep(0.05)
        pos = app.acct.positions[0]
        assert pos.max_until - pos.t_open == 48 * 3_600_000
        app.check_exits(eng, 0.99, pos.max_until + 1)
        await asyncio.sleep(2.8)
        assert any(c[0] == "close" for c in app.live.calls) and app.acct.positions == []
        assert app.history[-1]["why"] == "到时间"
    _run(go)


def test_live_skip_inside_order_task_is_printed(tmp_path, monkeypatch):
    """实盘下单前最后一步发现保证金不够 / 仓位太小，黑窗口要打出"没开：…原因"，不能只有信号没下文"""
    monkeypatch.setattr(E, "LOG_FILE", str(tmp_path / "log.txt"))
    app = _live_app(tmp_path, monkeypatch, enabled=["big_follow"])
    eng = FakeEng()
    app.engines[eng.inst] = eng
    app.live_avail = 5.0

    async def go():
        d = {}
        app.try_open(eng, C.Signal("big_follow", 1, 99.0, 102.0, 0), d, 100.0, 0)
        await asyncio.sleep(0.05)
        assert "可用保证金不够" in d["skip"]
        assert any("没开：BTC" in l and "可用保证金不够" in l for l in app.log)
        app.live_avail = 1000.0
        app.live.contracts_for = lambda inst, q: (0.0, 1.0, 1.0)
        d2 = {}
        app.try_open(eng, C.Signal("big_follow", 1, 99.0, 102.0, 0), d2, 100.0, 0)
        await asyncio.sleep(0.05)
        assert any("没开：BTC" in l and "仓位太小" in l for l in app.log)
    _run(go)


def test_pick_symbols_skips_stock_contracts(monkeypatch):
    """自动选币不要股票合约（欧易 instCategory=3）、黄金等（4）"""
    import of_app as A
    async def fake_top(proxy, n):
        return ["BTC-USDT-SWAP", "SNDK-USDT-SWAP", "ETH-USDT-SWAP", "AAPL-USDT-SWAP", "XAU-USDT-SWAP", "SOL-USDT-SWAP"][:n]
    monkeypatch.setattr(A, "top_by_volume", fake_top)
    monkeypatch.setattr(A, "INSTS", {"BTC-USDT-SWAP": {"instCategory": "1"}, "SNDK-USDT-SWAP": {"instCategory": "3"},
                                     "ETH-USDT-SWAP": {"instCategory": "1"}, "AAPL-USDT-SWAP": {"instCategory": "3"},
                                     "XAU-USDT-SWAP": {"instCategory": "4"}, "SOL-USDT-SWAP": {"instCategory": "1"}})
    monkeypatch.setitem(A.core.cfg, "symbols", "auto")
    monkeypatch.setitem(A.core.cfg, "top_n", 2)
    out = _run(A._pick_symbols)
    assert out == ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]


def test_old_strategy_uses_fixed_10pct_size(tmp_path, monkeypatch):
    """旧打法止损贴得很近：以前按止损倒推，仓位推到杠杆上限、保证金不够开不出来；现在和新打法一样每笔用权益 10%"""
    monkeypatch.setattr(E, "LOG_FILE", str(tmp_path / "log.txt"))
    app = _live_app(tmp_path, monkeypatch, enabled=["big_follow"], risk_pct=10, max_leverage=10)
    eng = FakeEng()
    app.engines[eng.inst] = eng

    async def go():
        d = {}
        app.try_open(eng, C.Signal("big_follow", 1, 99.95, 100.1, 0), d, 100.0, 0)   # 止损只差 0.05%
        await asyncio.sleep(0.05)
        assert d.get("traded"), d
        pos = app.acct.positions[0]
        assert abs(pos.qty * 100.0 - 1000 * 0.10) < 1.0          # 仓位价值 = 权益 10%
    _run(go)


def test_old_strategy_fixed_exit_option(tmp_path, monkeypatch):
    """旧打法选"像新打法"：止损按现价 5%、不设止盈、拿 48 小时；关掉时还是原版结构止损"""
    monkeypatch.setattr(E, "LOG_FILE", str(tmp_path / "log.txt"))
    app = _live_app(tmp_path, monkeypatch, enabled=["big_follow"],
                    old_exit={"fixed": 1, "stop_pct": 5, "tp_pct": 0, "hold_h": 48})
    eng = FakeEng()
    app.engines[eng.inst] = eng

    async def go():
        d = {}
        app.try_open(eng, C.Signal("big_follow", -1, 100.05, 99.9, 0), d, 100.0, 0)     # 原版止损只差 0.05%
        await asyncio.sleep(0.05)
        pos = app.acct.positions[0]
        assert abs(pos.stop - 105.0) < 1e-9 and pos.target < 20                       # 空单：止损 +5%，止盈放到够不到
        assert pos.max_until - pos.t_open == 48 * 3_600_000
        opens = [c for c in app.live.calls if c[0] == "open"]
        assert abs(opens[0][4] - 105.0) < 1e-9                                         # 交易所上挂的也是 5% 止损
        app.cfg["old_exit"] = {"fixed": 0}
        eng2 = FakeEng("ETH-USDT-SWAP"); app.engines[eng2.inst] = eng2
        app.try_open(eng2, C.Signal("big_follow", 1, 99.0, 102.0, 0), {}, 100.0, 0)
        await asyncio.sleep(0.05)
        p2 = [p for p in app.acct.positions if p.sym == eng2.inst][0]
        assert p2.stop == 99.0 and p2.target == 102.0
    _run(go)


def test_pick_symbols_puts_held_coins_first(monkeypatch):
    """重启后有持仓的币排最前面先接入（不然 150 个币里排到最后，好几分钟看不到浮盈）"""
    import of_app as A
    async def fake_pick():
        return ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]
    monkeypatch.setattr(A, "_pick_symbols", fake_pick)
    monkeypatch.setattr(A, "INSTS", {k: {} for k in ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "ENS-USDT-SWAP"]})
    pos = [type("P", (), {"sym": "SOL-USDT-SWAP"})(), type("P", (), {"sym": "ENS-USDT-SWAP"})()]
    monkeypatch.setattr(A.core.acct, "positions", pos)
    assert _run(A.pick_symbols) == ["SOL-USDT-SWAP", "ENS-USDT-SWAP", "BTC-USDT-SWAP", "ETH-USDT-SWAP"]



DCA = {"enabled": 1, "levels": "2,4", "weights": "1,1,2", "tp_pct": 2, "stop_pct": 8, "hold_h": 72}


def test_old_dca_paper_adds_and_takes_profit_on_average(tmp_path, monkeypatch):
    """模拟盘：旧打法开补仓 → 第一笔 1/4 仓位；跌 2%、4% 各补一笔（1:1:2）；止盈按均价 +2% 重算，止损离第一笔 8% 不动；碰到均价止盈就平"""
    monkeypatch.setattr(E, "STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(E, "TRADE_LOG", str(tmp_path / "t.jsonl"))
    monkeypatch.setattr(E, "LOG_FILE", str(tmp_path / "log.txt"))
    app = E.OrderFlowApp({"auto": True, "enabled": ["absorption"], "old_dca": DCA}, None, None, False)
    eng = FakeEng(); app.engines[eng.inst] = eng
    d = {}
    app.try_open(eng, C.Signal("absorption", 1, 99.9, 100.2, 0), d, 100.0, 0)
    pos = app.acct.positions[0]
    e0 = pos.entry
    assert abs(pos.qty * 100 - 1000 * 0.10 / 4) < 0.5                    # 整单 10%，第一笔 1/4
    assert abs(pos.stop - 100.0 * 0.92) < 1e-9 and abs(pos.target - 100.0 * 1.02) < 1e-9
    app.check_exits(eng, e0 * 0.979, 1)                                    # 跌过 -2%：补第 2 笔
    assert pos.dca_next == 2 and abs(pos.qty * 100 - 50) < 1.5
    app.check_exits(eng, e0 * 0.959, 2)                                    # 跌过 -4%：补第 3 笔（2 份）
    assert pos.dca_next == 3 and abs(pos.qty * 100 - 100) < 3
    assert pos.entry < e0 * 0.975 and abs(pos.target - pos.entry * 1.02) < 1e-9 and abs(pos.stop - 92.0) < 1e-9
    app.check_exits(eng, e0 * 0.94, 3)                                     # 补满了，不再补
    assert pos.dca_next == 3
    app.check_exits(eng, pos.target + 0.01, 4)                             # 回到均价 +2%：止盈
    assert app.acct.positions == [] and app.history[-1]["why"] == "止盈" and app.history[-1]["pnl"] > 0


def test_old_dca_live_adds_then_replaces_oco(tmp_path, monkeypatch):
    """实盘（假交易所）：补仓市价加 → 撤旧的止盈止损 → 按总张数、新均价止盈、原止损重挂"""
    monkeypatch.setattr(E, "LOG_FILE", str(tmp_path / "log.txt"))
    app = _live_app(tmp_path, monkeypatch, enabled=["absorption"], old_dca=DCA)
    eng = FakeEng(); app.engines[eng.inst] = eng

    async def go():
        app.try_open(eng, C.Signal("absorption", 1, 99.9, 100.2, 0), {}, 100.0, 0)
        await asyncio.sleep(0.05)
        pos = app.acct.positions[0]
        n0 = pos.contracts
        app.check_exits(eng, 97.9, 1)
        await asyncio.sleep(0.05)
        kinds = [c[0] for c in app.live.calls]
        assert kinds[-3:] == ["add", "cancel", "oco"]
        oco = app.live.calls[-1]
        assert oco[2] == pos.contracts == n0 * 2 and abs(oco[3] - 92.0) < 1e-9 and abs(oco[4] - pos.entry * 1.02) < 1e-9
        assert abs(pos.entry - 99.0) < 1e-9 and pos.algo_id == "A2" and not pos.busy
    _run(go)


def test_old_dca_bad_settings_or_new_strategies_not_affected(tmp_path, monkeypatch):
    """补仓设置填错就不补；清洗接盘 / 多头摊平做空不受补仓影响"""
    assert E.dca_plan({"old_dca": {**DCA, "weights": "1,1"}}) is None           # 比例少一个
    assert E.dca_plan({"old_dca": {**DCA, "stop_pct": 3}}) is None              # 止损比补仓位置近
    assert E.dca_plan({"old_dca": {**DCA, "enabled": 0}}) is None
    assert E.dca_plan({"old_dca": {**DCA, "levels": "2,2", "weights": "1,1,1"}}) is None   # 同一个位置连补不行
    assert E.dca_plan({"old_dca": DCA})["wt"] == [1.0, 1.0, 2.0]
    monkeypatch.setattr(E, "LOG_FILE", str(tmp_path / "log.txt"))
    app = _live_app(tmp_path, monkeypatch, enabled=["trap_short"], old_dca=DCA)
    eng = FakeEng(); app.engines[eng.inst] = eng

    async def go():
        app.try_open(eng, C.Signal("trap_short", -1, 105.0, 10.0, 0), {}, 100.0, 0)
        await asyncio.sleep(0.05)
        pos = app.acct.positions[0]
        assert pos.dca_lv == [] and abs(pos.qty * 100 - 100) < 1
    _run(go)


def test_pb_tf_switch_feeds_playbook_5m_bars(tmp_path, monkeypatch):
    """实战打法周期改成 5 分钟：识别器只收 5 分钟K线；信号的限价单挂 3 根K线；换回 1 分钟照旧；信号周期的打法不受影响"""
    monkeypatch.setattr(E, "STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(E, "TRADE_LOG", str(tmp_path / "t.jsonl"))
    monkeypatch.setattr(E, "LOG_FILE", str(tmp_path / "log.txt"))
    app = E.OrderFlowApp({"auto": False, "enabled": [], "pb_tf": "5m"}, None, None, False)
    eng = E.SymbolEngine(app, "SOL-USDT-SWAP", "15m", {t: 0.01 for t in E.VIEW_TFS}, 1.0)
    assert eng.pb_tf == "5m" and eng.pb.p["max_trades_day"] > 100
    seen, det_seen = [], []
    eng.pb.on_bar = lambda b: (seen.append(b.t), [type("S", (), {"kind": "pb_sweep", "side": 1, "stop": 89.0, "target": 103.0, "entry": 90.0, "level": "测试"})()])[1]
    orig_det = eng.det.on_bar
    eng.det.on_bar = lambda b: (det_seen.append(b.t), orig_det(b))[1]
    t0 = 20 * 86_400_000
    for k in range(32):                                    # 32 分钟，每分钟一笔
        eng.on_trade(100.0, 1, True, t0 + k * 60_000)
    assert seen and all(t % 300_000 == 0 for t in seen) and len(seen) == 6          # 5 分钟收一根
    assert det_seen and all(t % 900_000 == 0 for t in det_seen)                      # 信号周期 15 分钟不受影响
    _, d, until = eng.limit_orders[-1]
    assert until - d["t"] == 300_000 + 3 * 300_000 and d["tf"] == "5m"
    eng.set_pb_tf("1m")
    assert eng.pb_tf == "1m" and eng.limit_orders == [] and eng.pb.p["max_trades_day"] < 100


def test_old_size_pct_from_page_sizes_dca_first_leg(tmp_path, monkeypatch):
    """页面把旧打法每笔改成 30%：补仓 1:1:2:2:4 时第一笔 = 30% × 1/10 = 3%"""
    import of_app as A
    monkeypatch.setattr(A, "save_cfg", lambda: None)
    monkeypatch.setitem(A.core.cfg, "old_size_pct", 10)
    _run(lambda: A.set_cfg({"old_size_pct": 30}))
    assert A.core.cfg["old_size_pct"] == 30
    _run(lambda: A.set_cfg({"old_size_pct": 500}))               # 超出范围不收
    assert A.core.cfg["old_size_pct"] == 30
    monkeypatch.setattr(E, "STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(E, "TRADE_LOG", str(tmp_path / "t.jsonl"))
    monkeypatch.setattr(E, "LOG_FILE", str(tmp_path / "log.txt"))
    app = E.OrderFlowApp({"auto": True, "enabled": ["absorption"], "old_size_pct": 30,
                          "old_dca": {**DCA, "levels": "2,4,6,8", "weights": "1,1,2,2,4", "stop_pct": 11}}, None, None, False)
    eng = FakeEng(); app.engines[eng.inst] = eng
    app.try_open(eng, C.Signal("absorption", 1, 99.9, 100.2, 0), {}, 100.0, 0)
    assert abs(app.acct.positions[0].qty * 100 - 1000 * 0.03) < 0.5


def test_pb_warms_up_from_seeded_history(tmp_path, monkeypatch):
    """重启后：垫底的历史K线也喂给实战打法识别器，不用等实时跑够 20 根（5 分钟就是 100 分钟）"""
    monkeypatch.setattr(E, "STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(E, "TRADE_LOG", str(tmp_path / "t.jsonl"))
    app = E.OrderFlowApp({"auto": False, "enabled": [], "pb_tf": "5m"}, None, None, False)
    eng = E.SymbolEngine(app, "SOL-USDT-SWAP", "5m", {t: 0.01 for t in E.VIEW_TFS}, 1.0)
    t0 = 20 * 86_400_000
    eng.builders["5m"].seed([(t0 + k * 300_000, 100.0, 101.0, 99.0, 100.0, 50.0) for k in range(30)])
    assert len(eng.pb.swing) == 30 and len(eng.pb.vols) == 30 and eng.signals == []


def test_dip_signal_opens_long_with_shifted_stops_and_hold(tmp_path, monkeypatch):
    """急跌抄底：后台送来的信号 → 下一笔成交开多；止盈止损离进场价的距离不变（挪到实际成交价）、每笔 10%、最多 48 小时；
    同一根K线不重复出信号；没勾的打法只记信号不下单"""
    monkeypatch.setattr(E, "STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(E, "TRADE_LOG", str(tmp_path / "t.jsonl"))
    monkeypatch.setattr(E, "LOG_FILE", str(tmp_path / "log.txt"))
    app = E.OrderFlowApp({"auto": True, "enabled": ["vn_dip"], "btc_ma_days": 200, "max_positions": 5}, None, None, False)
    app.btc_bull = False                                   # 清洗接盘的大盘过滤不管这三个（大跌抄底自己判断牛熊）
    eng = E.SymbolEngine(app, "SOL-USDT-SWAP", "5m", {t: 0.0001 for t in E.VIEW_TFS}, 1.0)
    app.engines = {eng.inst: eng}
    t0 = 20 * 86_400_000
    eng.dip_signal("vn_dip", 100.0, 94.0, 102.0, t0, "1小时 -5.5 个标准差")
    eng.dip_signal("vn_dip", 100.0, 94.0, 102.0, t0, "重复")            # 同一根K线不再出
    assert len([s for s in eng.signals if s["kind"] == "vn_dip"]) == 1
    eng.on_trade(101.0, 1, True, t0 + 300_000 + 1000)
    pos = app.acct.positions
    assert len(pos) == 1 and pos[0].kind == "vn_dip" and pos[0].side == 1
    fill = pos[0].entry
    assert abs(pos[0].stop - (fill - 6.0)) < 0.2 and abs(pos[0].target - (fill + 2.0)) < 0.2
    assert pos[0].max_until - pos[0].t_open == 48 * 3_600_000
    assert abs(pos[0].qty * pos[0].entry - 1000 * 0.10) < 1.0
    eng.on_trade(103.5, 1, True, t0 + 600_000)                          # 碰到止盈
    assert app.acct.positions == [] and app.history[-1]["pnl"] > 0
    eng.dip_signal("nfi_5m", 100.0, 92.0, 103.0, t0 + 900_000, "NFI 条件 142 触发")   # 没勾：只记信号
    eng.on_trade(100.0, 1, True, t0 + 1_200_000)
    assert app.acct.positions == [] and any(s["kind"] == "nfi_5m" for s in eng.signals)


def test_dip_cfg_from_page(monkeypatch):
    """页面改急跌抄底参数：只收认识的数字；默认值就是回测过关的那组"""
    import of_app as A
    monkeypatch.setattr(A, "save_cfg", lambda: None)
    monkeypatch.setitem(A.core.cfg, "vn_dip", dict(E.VN_DIP))
    monkeypatch.setitem(A.core.cfg, "nfi_dip", dict(E.NFI_DIP))
    _run(lambda: A.set_cfg({"vn_dip": {"th1": 6, "bull_only": 0, "bad": 1}, "nfi_dip": {"tp_pct": 4}}))
    vc, nc = E.dip_cfg(A.core.cfg, "vn_dip"), E.dip_cfg(A.core.cfg, "nfi_15m")
    assert vc["th1"] == 6 and vc["th4"] == 4 and vc["bull_only"] == 0 and "bad" not in vc
    assert nc["tp_pct"] == 4 and nc["stop_pct"] == 8 and nc["hold_h"] == 48
    assert E.VN_DIP["th1"] == 5 and E.VN_DIP["tp_atr"] == 1 and E.VN_DIP["sl_atr"] == 3 and E.VN_DIP["bull_only"] == 1


def test_dip_features_only_on_bar_close():
    """大跌抄底只在整点（xx:55 那根 5 分钟收盘）算、15 分钟急跌只在 15 分钟收盘算；急跌 → 标准差为负；牛熊按昨收和 200 天均线"""
    import pytest
    pd = pytest.importorskip("pandas"); pytest.importorskip("talib")
    import numpy as np
    import of_nfi as N
    n = 2600
    rng = np.random.default_rng(0)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, n)))
    c[-12:] = c[-13] * np.linspace(0.99, 0.90, 12)                  # 最后 1 小时急跌 10%
    dates = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    d5 = pd.DataFrame({"date": dates, "open": c, "high": c * 1.001, "low": c * 0.999, "close": c, "volume": 1.0})
    shift = (55 - dates[-1].minute) % 60 // 5
    d5 = d5.iloc[: n - (12 - shift) % 12] if shift else d5
    if d5.date.iloc[-1].minute != 55:
        d5 = d5[d5.date.dt.minute.cumsum() >= 0]
        d5 = d5.iloc[: max(i for i, m in enumerate(d5.date.dt.minute) if m == 55) + 1]
    f = N.vn_features(d5)
    assert f is not None and f["vn1"] < -5 and f["atr"] > 0
    assert N.vn_features(d5.iloc[:-1]) is None                      # 不在整点
    assert N.tf15_triggers(d5.iloc[:-1]) == [] or d5.date.iloc[-2].minute % 15 == 10
    daily = pd.DataFrame({"close": np.r_[np.full(199, 100.0), 120.0]})
    assert N.btc_bull_200(daily) is True and N.btc_bull_200(daily.iloc[:50]) is None
    assert N.btc_bull_200(pd.DataFrame({"close": np.r_[np.full(199, 100.0), 90.0]})) is False


def test_neutralize_keeps_guard_drops_trigger():
    """只留安全检查的那份：141~145 的触发部分换成永远成立，其他条件不动"""
    import of_nfi as N
    src = open(N.NFI_FILE, encoding="utf-8").read()
    out = N.neutralize(src)
    assert out.count("long_entry_logic.append(rsi_3 > -1.0)") == 5
    assert "(close < sma_16 * 0.960)" in src and out.count("(close < sma_16 * 0.960)") < src.count("(close < sma_16 * 0.960)")
    assert len(out.split("\n")) < len(src.split("\n"))



def _trap_setup(tmp_path, monkeypatch, mode, start_px=1.0):
    import time
    monkeypatch.setattr(E, "STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(E, "TRADE_LOG", str(tmp_path / "t.jsonl"))
    monkeypatch.setattr(E, "LOG_FILE", str(tmp_path / "log.txt"))
    app = E.OrderFlowApp({"auto": True, "enabled": ["trap_short"], "btc_ma_days": 0, "flush_filters": {}, "trap": {"mode": mode}},
                         None, None, False)
    eng = E.SymbolEngine(app, "SOL-USDT-SWAP", "5m", {t: 0.0001 for t in E.VIEW_TFS}, 1.0)
    app.engines = {eng.inst: eng}
    t0, now = 20 * 86_400_000, time.time() * 1000
    for k in range(25 * 12, 0, -1):
        eng.momo.add(t0 - k * 300_000, start_px, 100.0)
    app.oi24[eng.inst] = (0.08, now)
    app.lsz[eng.inst] = (0.5, now)
    eng.ext["funding"] = 0.0001
    eng.ext["oi_hist"] = [(t0 - 3_600_000, 1e6)]
    return app, eng, t0


def test_trap_mode_a_waits_for_bounce_and_skips_near_low(tmp_path, monkeypatch):
    """A：信号出来不马上空，在收盘价上方 2% 挂空，1 小时内涨到才成交；止损按成交价上方 5%。
    已经贴着 24 小时低点（2% 以内）的信号不做"""
    app, eng, t0 = _trap_setup(tmp_path, monkeypatch, "A")
    eng.on_trade(0.95, 10, False, t0 - 300_000)                                # 24 小时最低 0.90（更早跌过）
    eng.on_trade(0.90, 10, False, t0 - 290_000)
    for k in range(3):
        eng.on_trade(0.93 - k * 0.001, 10, False, t0 + k * 300_000)            # 收在 0.929 左右：离低点 >2%
    eng.on_trade(0.929, 1, False, t0 + 2 * 300_000 + 500)
    sig = [s for s in eng.signals if s["kind"] == "trap_short"]
    assert len(sig) == 1 and app.acct.positions == [] and len(eng.limit_orders) == 1
    s, d, until = eng.limit_orders[0]
    assert abs(s.entry - sig[0]["price"] * 1.02) < 1e-9 and until == sig[0]["t"] + 300_000 + 60 * 60_000
    eng.on_trade(s.entry + 0.0005, 1, True, t0 + 3 * 300_000 + 1000)            # 反弹到挂单价
    pos = app.acct.positions
    assert len(pos) == 1 and pos[0].side == -1 and pos[0].mode == "A"
    assert abs(pos[0].stop - s.entry * 1.05) < 1e-6 and pos[0].max_until - pos[0].t_open == 48 * 3_600_000


def test_trap_mode_a_skips_when_at_low(tmp_path, monkeypatch):
    app, eng, t0 = _trap_setup(tmp_path, monkeypatch, "A")
    for k in range(3):
        eng.on_trade(0.93 - k * 0.001, 10, False, t0 + k * 300_000)            # 收盘就在 24 小时最低附近
    eng.on_trade(0.928, 1, False, t0 + 2 * 300_000 + 500)
    sig = [s for s in eng.signals if s["kind"] == "trap_short"]
    assert len(sig) == 1 and "太低" in sig[0]["skip"] and eng.limit_orders == [] and app.acct.positions == []


def test_trap_mode_b_uses_sar_and_atr(tmp_path, monkeypatch):
    """B：后台读到 4 小时 SAR 在上方 → 挂空，止盈 2 倍 ATR、止损 3 倍 ATR；SAR 在下方 → 不做；B 的单不看"多头被清洗"平仓"""
    import asyncio
    app, eng, t0 = _trap_setup(tmp_path, monkeypatch, "B")
    seen = []

    async def aux(inst):
        seen.append(inst); return {"sar_above": True, "atr": 0.01}
    app.trap_aux_cb = aux

    async def go():
        for k in range(3):
            eng.on_trade(0.93 - k * 0.001, 10, False, t0 + k * 300_000)
        eng.on_trade(0.928, 1, False, t0 + 2 * 300_000 + 500)
        await asyncio.sleep(0.01)
        assert seen == [eng.inst] and len(eng.limit_orders) == 1
        s, d, _ = eng.limit_orders[0]
        assert abs(s.stop - (s.entry + 0.03)) < 1e-9 and abs(s.target - (s.entry - 0.02)) < 1e-9
        eng.on_trade(s.entry + 0.0005, 1, True, t0 + 3 * 300_000 + 1000)
        pos = app.acct.positions
        assert len(pos) == 1 and pos[0].mode == "B"
        eng.ext["oi_hist"] = [(t0 + 3 * 300_000 - 3_600_000, 1e6), (t0 + 4 * 300_000, 0.9e6)]
        for k in range(4, 17):                                                   # 多头被清洗：B 不平（只按 ATR 止盈止损）
            eng.on_trade(s.entry - 0.005 - k * 0.0002, 10, False, t0 + k * 300_000)
        assert len(app.acct.positions) == 1
    asyncio.run(go())


def test_trap_mode_b_skips_when_sar_below(tmp_path, monkeypatch):
    import asyncio
    app, eng, t0 = _trap_setup(tmp_path, monkeypatch, "B")

    async def aux(inst):
        return {"sar_above": False, "atr": 0.01}
    app.trap_aux_cb = aux

    async def go():
        for k in range(3):
            eng.on_trade(0.93 - k * 0.001, 10, False, t0 + k * 300_000)
        eng.on_trade(0.928, 1, False, t0 + 2 * 300_000 + 500)
        await asyncio.sleep(0.01)
        assert eng.limit_orders == [] and any("SAR" in (s.get("skip") or "") for s in eng.signals)
    asyncio.run(go())


def test_trap_mode_c_needs_4pct_above_low_and_uses_atr(tmp_path, monkeypatch):
    """C：离 24 小时低点超过 4% 才做（3% 的信号 A 会做、C 不做）；做的话挂空在上方 2%，止盈 2 倍 / 止损 3 倍 ATR，不看清洗"""
    import asyncio
    app, eng, t0 = _trap_setup(tmp_path, monkeypatch, "C")
    seen = []

    async def aux(inst):
        seen.append(inst); return {"sar_above": False, "atr": 0.01}          # C 不看 SAR
    app.trap_aux_cb = aux

    async def go():
        eng.on_trade(0.95, 10, False, t0 - 300_000)
        eng.on_trade(0.88, 10, False, t0 - 290_000)                            # 24 小时最低 0.88 → 收 0.929 离低点 5.6%
        for k in range(3):
            eng.on_trade(0.93 - k * 0.001, 10, False, t0 + k * 300_000)
        eng.on_trade(0.929, 1, False, t0 + 2 * 300_000 + 500)
        await asyncio.sleep(0.01)
        assert seen == [eng.inst] and len(eng.limit_orders) == 1
        s, d, until = eng.limit_orders[0]
        assert abs(s.entry - d["price"] * 1.02) < 1e-9 and until == d["t"] + 300_000 + 60 * 60_000
        assert abs(s.stop - (s.entry + 0.03)) < 1e-9 and abs(s.target - (s.entry - 0.02)) < 1e-9
        eng.on_trade(s.entry + 0.0005, 1, True, t0 + 3 * 300_000 + 1000)
        pos = app.acct.positions
        assert len(pos) == 1 and pos[0].mode == "C"
        eng.ext["oi_hist"] = [(t0 + 3 * 300_000 - 3_600_000, 1e6), (t0 + 4 * 300_000, 0.9e6)]
        for k in range(4, 17):                                                   # 多头被清洗：C 不平
            eng.on_trade(s.entry - 0.005 - k * 0.0002, 10, False, t0 + k * 300_000)
        assert len(app.acct.positions) == 1
    asyncio.run(go())


def test_trap_mode_c_skips_3pct_above_low(tmp_path, monkeypatch):
    app, eng, t0 = _trap_setup(tmp_path, monkeypatch, "C")
    eng.on_trade(0.95, 10, False, t0 - 300_000)
    eng.on_trade(0.90, 10, False, t0 - 290_000)                                # 离低点 3.2%：A 会做，C 不做
    for k in range(3):
        eng.on_trade(0.93 - k * 0.001, 10, False, t0 + k * 300_000)
    eng.on_trade(0.929, 1, False, t0 + 2 * 300_000 + 500)
    sig = [s for s in eng.signals if s["kind"] == "trap_short"]
    assert len(sig) == 1 and "要超过 4%" in sig[0]["skip"] and eng.limit_orders == [] and app.acct.positions == []
    assert E.trap_cfg({})["mode"] == "C"                                          # 默认 C


def test_trap_mode_from_page(monkeypatch):
    import of_app as A
    monkeypatch.setattr(A, "save_cfg", lambda: None)
    monkeypatch.setitem(A.core.cfg, "trap", dict(E.TRAP))
    _run(lambda: A.set_cfg({"trap": {"mode": "B"}}))
    assert E.trap_cfg(A.core.cfg)["mode"] == "B" and E.trap_cfg(A.core.cfg)["stop"] == 0.05
    _run(lambda: A.set_cfg({"trap": {"mode": "乱写"}}))
    assert E.trap_cfg(A.core.cfg)["mode"] == "B"
