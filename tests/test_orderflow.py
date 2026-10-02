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

    def prepare(self, inst, lev):
        self.calls.append(("prepare", inst, lev))

    def open(self, inst, side, n, stop, target):
        self.calls.append(("open", inst, side, n, stop, target))
        self.pos[inst] = side * n
        self.algos[inst] = "A1"
        return {"fill": 100.0, "contracts": n, "order_id": "O1", "algo_id": "A1"}

    def close(self, inst, side_open, n, algo_id=""):
        self.calls.append(("close", inst, n))
        self.pos[inst] = self.pos.get(inst, 0) - side_open * n
        return 101.0

    def cancel_algo(self, inst, algo_id):
        self.calls.append(("cancel", inst, algo_id))

    def place_oco(self, inst, side, n, stop, target):
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
        await asyncio.sleep(1.7)
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
    assert eng.builders["15m"].on_close == eng._on_bar
    assert eng.builders["5m"].on_close not in (eng._on_bar, eng._on_pb_bar)
    assert eng.builders["1m"].on_close == eng._on_pb_bar      # 实战打法还在 1 分钟
    eng.set_signal_tf("1m")
    assert eng.builders["1m"].on_close not in (eng._on_bar, eng._on_pb_bar)   # 两种打法挂在一起


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
    app = E.OrderFlowApp({"auto": True, "enabled": ["flush_spot"]}, None, None, False)

    class X:          # 假的全网数据：币安现货 1 小时主动买 > 卖 10%
        def stats(self, inst, okx_min=None):
            return {"sf_60": 0.10}
    app.xx = X()
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
    assert abs(pos[0].qty * sig_px - 1000 * 0.05) < 1.0                  # 每笔用权益 5%
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
            return {"sf_60": 0.02}
    app.xx = X()
    eng = E.SymbolEngine(app, "SOL-USDT-SWAP", "5m", {t: 0.01 for t in E.VIEW_TFS}, 1.0)
    t0 = 10 * 86_400_000
    eng.ext["oi_hist"] = [(t0, 1000.0), (t0 + 60 * 60_000, 960.0)]       # -4%
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
