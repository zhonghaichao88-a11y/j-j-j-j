"""按 strategy_research.md 第 2、3 节实现的「趋势突破 + ATR 风控」系统回测。
信号（15 分钟收盘后，下一根开盘市价进场）：
  多：收盘 > 前 20 根最高（不含当前）；15m EMA20>EMA50；1h EMA20>EMA50（只用已收盘的 1 小时K线）；
      ADX(14)>=20；0.15% <= ATR(14)/收盘 <= 3%。空：全部取反。
出场：初始止损 1.5ATR；到 +2R 后止损移到成本价，并按 最高价-2ATR 跟踪；反向信号、96 根超时 → 下一根开盘平；
      亏损止损后冷却 3 根。同一根K线里先判止损（保守），再用这根K线更新跟踪止损。
成本：吃单 0.05%/边 + 滑点（情景 0.01%/0.02%/0.05%）+ 真实资金费（按结算时刻、持仓方向）。
仓位（组合模拟里）：单笔风险 0.5% 净值；qty = 风险金额 / (入场价*(止损%+成本%))；ATR% 高于近 90 天 80 分位时风险减半；
  单品种名义 ≤ 2 倍净值；全部保证金（3 倍杠杆）≤ 25% 净值；总未平仓风险 ≤ 1.5%；
  当日（UTC）已实现亏损 ≥ 2% → 当天不再开仓；回撤 ≥ 8% 风险减半；回撤 ≥ 12% → 记为停机（另报不停机继续跑的结果）。
"""
import numpy as np, pandas as pd

FEE = 0.0005
P0 = dict(dc=20, ema_f=20, ema_s=50, adx_min=20, atr_lo=0.0015, atr_hi=0.03, sl=1.5, be_r=2.0, trail=2.0, timeout=96, cool=3, partial=False)


def load15(c, tf=15):
    d = np.load(f"data15/{c}.npz")
    df = pd.DataFrame({k: d[k] for k in ("open", "high", "low", "close")}, index=pd.to_datetime(d["ts"], unit="ms"))
    df = df[~df.index.duplicated()]
    if tf != 15:
        df = df.resample(f"{tf}min", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    return df


def funding(c):
    d = np.load(f"../趋势跟踪/data/{c}.npz")
    return pd.Series(d["f_rate"], index=pd.to_datetime(d["f_ts"], unit="ms").round("min")).sort_index()


def wilder(x, n):
    return x.ewm(alpha=1 / n, adjust=False).mean()


def indicators(df, p):
    h, l, c = df.high, df.low, df.close
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = wilder(tr, 14)
    up, dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    ndm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    pdi, ndi = 100 * wilder(pdm, 14) / atr, 100 * wilder(ndm, 14) / atr
    adx = wilder(100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan), 14)
    ef, es = c.ewm(span=p["ema_f"], adjust=False).mean(), c.ewm(span=p["ema_s"], adjust=False).mean()
    tf = p.get("tf", 15); htf = p.get("htf", 60)
    h1 = df.resample(f"{htf}min", label="right", closed="left").agg({"close": "last"}).dropna()   # 标签=收盘时刻
    h1f, h1s = h1.close.ewm(span=20, adjust=False).mean(), h1.close.ewm(span=50, adjust=False).mean()
    # K线在 t 开盘，t+tf 收盘；收盘时能用的高周期K线 = 收盘时刻 <= t+tf 的最后一根
    close_t = df.index + pd.Timedelta(minutes=tf)
    h1trend = np.sign(h1f - h1s).reindex(close_t, method="ffill").values
    dch = h.rolling(p["dc"]).max().shift(1); dcl = l.rolling(p["dc"]).min().shift(1)
    atr_pct = atr / c
    bpd = 1440 // tf
    atr_p80 = atr_pct.rolling(90 * bpd, min_periods=30 * bpd).quantile(0.8)
    ok = (adx >= p["adx_min"]) & (atr_pct >= p["atr_lo"]) & (atr_pct <= p["atr_hi"])
    long_sig = ok & (c > dch) & (ef > es) & (h1trend > 0)
    short_sig = ok & (c < dcl) & (ef < es) & (h1trend < 0)
    return dict(atr=atr.values, atr_pct=atr_pct.values, atr_hot=(atr_pct > atr_p80).values,
                L=long_sig.fillna(False).values, S=short_sig.fillna(False).values)


def trades(df, ind, fund, p, slip):
    """单品种逐根模拟，返回交易列表（不含仓位大小，收益按百分比和 R 记）"""
    o, h, l, c = df.open.values, df.high.values, df.low.values, df.close.values
    t = df.index
    L, S, atr = ind["L"], ind["S"], ind["atr"]
    fts, frt = fund.index.values, fund.values
    out = []
    pos = 0; cool_until = -1; i = 0; n = len(c)
    while i < n - 1:
        if pos == 0:
            if i >= cool_until and (L[i] or S[i]) and not np.isnan(atr[i]):
                side = 1 if L[i] else -1
                k = i + 1
                entry = o[k] * (1 + side * slip)
                R = p["sl"] * atr[i]
                stop = entry - side * R
                best = entry; be = False; half_done = False; realized_half = 0.0
                j = k
                exit_px = None; why = None
                while j < n:
                    # 1 止损（保守：先判）
                    if (side > 0 and l[j] <= stop) or (side < 0 and h[j] >= stop):
                        px = o[j] if (side > 0 and o[j] < stop) or (side < 0 and o[j] > stop) else stop   # 跳空穿过 → 按开盘价
                        exit_px = px * (1 - side * slip); why = "止损" if not be else "保本/跟踪"; break
                    # 2 分批（可选）：+3R 平一半
                    if p["partial"] and not half_done and (h[j] - entry if side > 0 else entry - l[j]) >= 3 * R:
                        half_done = True; realized_half = 3 * R / entry
                    # 3 更新最好价、保本、跟踪
                    best = max(best, h[j]) if side > 0 else min(best, l[j])
                    if not be and (best - entry) * side >= p["be_r"] * R:
                        be = True
                    if be:
                        trail = best - side * p["trail"] * atr[j]
                        stop = max(stop, entry, trail) if side > 0 else min(stop, entry, trail)
                    # 4 收盘时：反向信号 / 超时 → 下一根开盘平
                    if j + 1 < n and ((side > 0 and S[j]) or (side < 0 and L[j]) or (j - k + 1) >= p["timeout"]):
                        exit_px = o[j + 1] * (1 - side * slip); why = "反向" if (S[j] if side > 0 else L[j]) else "超时"; j = j + 1; break
                    j += 1
                if exit_px is None:
                    break
                ret = (exit_px - entry) / entry * side
                if p["partial"] and half_done:
                    ret = 0.5 * ret + 0.5 * realized_half
                lo_t, hi_t = t[k].to_datetime64(), t[j].to_datetime64()
                a, b = np.searchsorted(fts, lo_t, "right"), np.searchsorted(fts, hi_t, "right")
                fcost = side * frt[a:b].sum()
                net = ret - 2 * FEE - fcost
                stop_pct = R / entry
                out.append(dict(sym=None, side=side, t_in=t[k], t_out=t[j], entry=entry, stop_pct=stop_pct, ret=ret, fund=fcost,
                                net=net, R=net / stop_pct, why=why, bars=j - k + 1, hot=bool(ind["atr_hot"][i])))
                if net < 0 and why == "止损":
                    cool_until = j + 1 + p["cool"]
                i = j
                continue
        i += 1
    return pd.DataFrame(out)


def portfolio(tr, slip, risk=0.005, halt_dd=0.12, stop_at_halt=False):
    """组合层：按风险算仓位 + 账户风控。返回 (净值序列, 实际成交的交易, 停机日期)"""
    tr = tr.sort_values("t_in").reset_index(drop=True)
    eq = 1.0; peak = 1.0; open_ = []; day = None; day_pnl = 0.0; halted = None
    taken = []; curve = []
    # 同一时刻：先处理别的单的平仓（0），再开仓（1）；同一根K线开了又止损的单，平仓排在自己开仓之后（2）
    events = sorted([(r.t_in, 1, i) for i, r in tr.iterrows()] +
                    [(r.t_out, 2 if r.t_out <= r.t_in else 0, i) for i, r in tr.iterrows()], key=lambda x: (x[0], x[1]))
    size = {}
    for ts, kind, i in events:
        d = ts.normalize()
        if d != day:
            day, day_pnl = d, 0.0
        r = tr.loc[i]
        if kind != 1:
            if i in size:
                pnl = size[i] * r.net
                eq += pnl; day_pnl += pnl; peak = max(peak, eq)
                open_ = [x for x in open_ if x[0] != i]
                curve.append((ts, eq))
                if halted is None and eq / peak - 1 <= -halt_dd:
                    halted = ts
            continue
        if stop_at_halt and halted is not None:
            continue
        if day_pnl <= -0.02 * eq:
            continue
        if any(x[1] == r.sym for x in open_):
            continue
        rk = risk * (0.5 if r.hot else 1.0) * (0.5 if eq / peak - 1 <= -0.08 else 1.0)
        if sum(x[2] for x in open_) + rk > 0.015:
            continue
        cost_pct = 2 * FEE + 2 * slip + 0.0001
        notional = rk * eq / (r.stop_pct + cost_pct)
        notional = min(notional, 2 * eq)
        margin_used = sum(x[3] for x in open_) / 3
        room = 0.25 * eq - margin_used
        if room <= 0:
            continue
        notional = min(notional, room * 3)
        size[i] = notional
        open_.append((i, r.sym, rk, notional))
        taken.append(i)
    curve = pd.Series([v for _, v in curve], index=[k for k, _ in curve]) if curve else pd.Series([1.0])
    tt = tr.loc[taken].copy(); tt["notional"] = [size[i] for i in taken]
    return curve, tt, halted


def summarize(tr):
    if len(tr) == 0:
        return dict(笔数=0)
    g, b = tr.net[tr.net > 0].sum(), -tr.net[tr.net < 0].sum()
    return dict(笔数=len(tr), 胜率=f"{(tr.net > 0).mean()*100:.0f}%", PF=round(g / b, 2) if b else np.inf,
                平均R=round(tr.R.mean(), 3), 资金费占比R=round((tr.fund / tr.stop_pct).mean(), 3))


def curve_stats(curve):
    if len(curve) < 2:
        return {}
    yrs = (curve.index[-1] - curve.index[0]).days / 365
    dd = (curve / curve.cummax() - 1).min()
    return dict(总收益=f"{(curve.iloc[-1]-1)*100:+.1f}%", 年化=f"{((curve.iloc[-1])**(1/yrs)-1)*100:+.1f}%", 最大回撤=f"{dd*100:.1f}%")
