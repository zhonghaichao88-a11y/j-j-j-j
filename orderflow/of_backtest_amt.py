"""按 Fabio Valentini 公开的"拍卖理论 + 订单流"方法回测（1 分钟真实足迹，不偷看未来）。

他的三步：市场状态（平衡 / 失衡）→ 位置（这一段走势里成交量最少的价位 LVN）→ 主动性（到了位置出现顺方向的大单或失衡）。
三样缺一样就不做。参数全部照公开资料定，不在数据上调。

两种打法（多空对称，下面以做多为例）：
  回归（平衡市）：价格跌出昨天价值区下沿（VAL）后又收回区间里 → 不在第一下进场，等回踩
      收回这一段的 LVN，出现主动买（大单买多于卖、或买方堆叠失衡、且这根 delta 为正）→ 下一根开盘进场。
      止损：这根主动K线最低点下面 2 格；目标：昨天的 POC。
  顺势（失衡市）：价格在昨天价值区上方，最近 2 小时有一段明显推动（≥ 4 倍平均 1 分钟振幅，并创出新高突破 VAH）
      → 回踩这段推动的 LVN，出现主动买 → 下一根开盘进场。止损同上；目标：推动段高点。
  共同：盈亏比不到 1.5 不做；到 1R 而且进场后 delta 为正，止损移到保本；当天每个币亏 3 次就停；最多拿 4 小时。
  成本：进场吃单 0.05% + 滑点 0.02%；止盈挂单 0.02%；止损/到时间吃单 0.05% + 滑点 0.02%。

用法: python of_backtest_amt.py <npz目录> BTC,ETH,SOL [all]   （加 all = 不限时段；默认顺势只做纽约、回归只做伦敦）
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from of_core import volume_profile, imbalances, stacked  # noqa: E402
import of_backtest as B  # noqa: E402
from of_backtest_pb import minute_bars  # noqa: E402

MAKER, TAKER, SLIP = 0.0002, 0.0005, 0.0002
RR_MIN, MAX_HOLD, MAX_LOSS_DAY = 1.5, 240, 3
LVN_FRAC = 0.35          # 这一段的成交量分布里，低于最高量 35% 的价位算 LVN
IMPULSE_ATR = 4.0        # 推动段至少是平均 1 分钟振幅的 4 倍
SESS = {"trend": [(13.5, 20.0)], "mr": [(7.0, 12.0)]}   # UTC


def in_sess(t_ms, kind, all_hours):
    if all_hours:
        return True
    h = (t_ms // 60000 % 1440) / 60
    return any(a <= h < b for a, b in SESS[kind])


def leg_lvn(bars, i0, i1, tick, side):
    """i0..i1 这一段的成交量分布，返回中间那半段里的 LVN 价位行号集合"""
    prof = defaultdict(float)
    for b in bars[i0:i1 + 1]:
        for r, (x, y) in b.rows.items():
            prof[r] += x + y
    if len(prof) < 6:
        return set()
    lo, hi = min(prof), max(prof)
    span = hi - lo
    mx = max(prof.values())
    out = set()
    for r in range(lo + int(span * 0.25), hi - int(span * 0.25) + 1):
        v = (prof.get(r - 1, 0) + prof.get(r, 0) + prof.get(r + 1, 0)) / 3
        if v < LVN_FRAC * mx:
            out.add(r)
    return out


def aggressive(b, side):
    """这根K线顺方向有主动性：delta 同向，并且（大单同向占优，或同向 3 格以上堆叠失衡）"""
    if side * b.delta() <= 0:
        return False
    if (b.big_buy - b.big_sell) * side > 0:
        return True
    buy, sell = imbalances(b)
    return bool(stacked(buy if side == 1 else sell, 3))


def run(d, all_hours=False):
    bars, tick = minute_bars(d)
    big = {int(m): (bb, bs) for m, bb, bs in zip(d["big_m"], d["big_buy"], d["big_sell"])}
    for b in bars:
        b.big_buy, b.big_sell = big.get(b.t // 60000, (0.0, 0.0))
    trades = []
    day, prev = None, None
    day_bars, losses = [], 0
    rng = []
    pos = None
    st = {1: None, -1: None}     # 回归打法的状态：{'ext': 极值, 'i_ext': 序号, 'reclaimed': bool}
    for i, b in enumerate(bars):
        dd = b.t // 86_400_000
        if dd != day:
            if day_bars:
                prev = volume_profile(day_bars, tick)
            day, day_bars, losses = dd, [], 0
            st = {1: None, -1: None}
        day_bars.append(b)
        rng.append(b.h - b.l)
        atr = np.mean(rng[-60:]) if len(rng) >= 60 else None

        # ---- 持仓
        if pos is not None:
            s = pos["side"]
            if i > pos["i"]:
                pos["cvd"] += b.delta()
                hit = b.l <= pos["stop"] if s == 1 else b.h >= pos["stop"]
                tgt = b.h > pos["tgt"] if s == 1 else b.l < pos["tgt"]
                ex = None
                if hit:
                    px = (min(b.o, pos["stop"]) if s == 1 else max(b.o, pos["stop"])) * (1 - s * SLIP)
                    ex, cost, why = px, TAKER, "止损" if pos["stop"] != pos["entry"] else "保本"
                elif tgt:
                    ex, cost, why = pos["tgt"], MAKER, "目标"
                elif i - pos["i"] >= MAX_HOLD:
                    ex, cost, why = b.c * (1 - s * SLIP), TAKER, "到时间"
                if ex is not None:
                    r = s * (ex / pos["entry"] - 1) - cost - TAKER - SLIP
                    trades.append({"t": pos["t"], "kind": pos["kind"], "side": s, "ret": r,
                                   "R": r / pos["risk"], "why": why})
                    if r < 0:
                        losses += 1
                    pos = None
                else:
                    if pos["stop"] != pos["entry"] and s * (b.c - pos["entry"]) >= pos["risk"] * pos["entry"] and pos["cvd"] * s > 0:
                        pos["stop"] = pos["entry"]
            if pos is not None:
                continue
        if prev is None or atr is None or losses >= MAX_LOSS_DAY or i + 1 >= len(bars):
            continue
        vah, val, poc = prev["vah"], prev["val"], prev["poc"]

        cands = []
        # ---- 回归：跌出价值区再收回
        for side in (1, -1):
            edge = val if side == 1 else vah
            outside = b.l < edge if side == 1 else b.h > edge
            S = st[side]
            if outside and (S is None or (side == 1 and b.l < S["ext"]) or (side == -1 and b.h > S["ext"])):
                st[side] = S = {"ext": b.l if side == 1 else b.h, "i_ext": i, "reclaimed": False}
            if S is None:
                continue
            if not S["reclaimed"] and (b.c > edge if side == 1 else b.c < edge):
                S["reclaimed"] = True
                S["i_rec"] = i
                continue                              # 第一下收回不进场
            if S["reclaimed"] and i - S["i_rec"] >= 2 and in_sess(b.t, "mr", all_hours):
                lv = leg_lvn(bars, S["i_ext"], i - 1, tick, side)
                touch = [r for r in lv if (b.l <= (r + 1) * tick and r * tick <= b.h)]
                if touch and aggressive(b, side) and (b.c > edge if side == 1 else b.c < edge):
                    cands.append(("回归", side, poc))
        # ---- 顺势：价值区外的推动段回踩
        for side in (1, -1):
            if not in_sess(b.t, "trend", all_hours):
                continue
            lo_i = max(0, i - 120)
            seg = bars[lo_i:i]
            if not seg:
                continue
            if side == 1:
                j0 = lo_i + int(np.argmin([x.l for x in seg])); j1 = lo_i + int(np.argmax([x.h for x in seg]))
                ok = j1 > j0 and bars[j1].h - bars[j0].l >= IMPULSE_ATR * atr and bars[j1].h > vah and b.c > vah
                ext = bars[j1].h if ok else None
            else:
                j0 = lo_i + int(np.argmax([x.h for x in seg])); j1 = lo_i + int(np.argmin([x.l for x in seg]))
                ok = j1 > j0 and bars[j0].h - bars[j1].l >= IMPULSE_ATR * atr and bars[j1].l < val and b.c < val
                ext = bars[j1].l if ok else None
            if not ok or i - j1 < 2:
                continue
            lv = leg_lvn(bars, j0, j1, tick, side)
            touch = [r for r in lv if (b.l <= (r + 1) * tick and r * tick <= b.h)]
            if touch and aggressive(b, side):
                cands.append(("顺势", side, ext))

        for kind, side, tgt in cands[:1]:
            nb = bars[i + 1]
            entry = nb.o * (1 + side * SLIP)
            stop = (b.l - 2 * tick) if side == 1 else (b.h + 2 * tick)
            risk = abs(entry - stop) / entry
            if risk <= 0 or side * (tgt - entry) <= 0:
                continue
            if side * (tgt - entry) / abs(entry - stop) < RR_MIN:
                continue
            pos = {"t": nb.t, "i": i + 1, "kind": kind, "side": side, "entry": entry, "stop": stop, "tgt": tgt,
                   "risk": risk, "cvd": 0.0}
            if kind == "回归":
                st[side] = None
            # 进场那根K线自己也要检查止损（往坏处算）
            hit = nb.l <= stop if side == 1 else nb.h >= stop
            if hit:
                r = side * (stop * (1 - side * SLIP) / entry - 1) - 2 * TAKER - SLIP
                trades.append({"t": nb.t, "kind": kind, "side": side, "ret": r, "R": r / risk, "why": "止损"})
                losses += 1
                pos = None
    return trades


def report(all_tr):
    import pandas as pd
    T = pd.DataFrame(all_tr)
    if T.empty:
        print("没有交易")
        return
    T["m"] = pd.to_datetime(T.t, unit="ms").dt.strftime("%Y-%m")
    T["half"] = np.where(T.m <= "2026-07", "前4个月", "后2个月")
    pf = lambda x: x[x > 0].sum() / -x[x < 0].sum() if (x < 0).any() else float("inf")
    for (sym, kind), g in T.groupby(["sym", "kind"]):
        print(f"{sym} {kind}: {len(g)}笔 胜率{(g.ret > 0).mean() * 100:.0f}% 每笔{g.ret.mean() * 1e4:+.1f}基点 "
              f"平均{g.R.mean():+.2f}R PF{pf(g.ret):.2f}")
    print()
    for (kind, half), g in T.groupby(["kind", "half"]):
        print(f"合计 {kind} {half}: {len(g)}笔 胜率{(g.ret > 0).mean() * 100:.0f}% 每笔{g.ret.mean() * 1e4:+.1f}基点 "
              f"平均{g.R.mean():+.2f}R PF{pf(g.ret):.2f}  出场:{g.why.value_counts().to_dict()}")
    for kind, g in T.groupby("kind"):
        print(f"  {kind} 各月平均R:", (g.groupby("m").R.mean()).round(2).to_dict())


if __name__ == "__main__":
    path, syms = sys.argv[1], sys.argv[2].split(",")
    allh = len(sys.argv) > 3 and sys.argv[3] == "all"
    out = []
    for s in syms:
        d = B.load(path, s)
        tr = run(d, allh)
        for t in tr:
            t["sym"] = s
        out += tr
        print(s, len(tr), "笔", flush=True)
    report(out)
