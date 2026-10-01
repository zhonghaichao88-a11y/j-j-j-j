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
    assert not ok and app.broker is app.paper
    app2 = E.OrderFlowApp({}, None, {"key": "k", "secret": "s", "passphrase": "p"}, allow_live=True)
    ok, msg = app2.enable_live("随便")
    assert not ok and app2.broker is app2.paper


def test_auto_trading_off_never_opens(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(E, "TRADE_LOG", str(tmp_path / "t.jsonl"))
    app = E.OrderFlowApp({"auto": False, "enabled": list(C.SIGNAL_NAMES)}, None, None, False)

    class Eng:
        inst, tf = "BTC-USDT-SWAP", "5m"
    s = C.Signal("absorption", 1, 99.0, 102.0, 0)
    app.try_open(Eng(), s, {}, 100.0, 0)
    assert app.acct.positions == []
