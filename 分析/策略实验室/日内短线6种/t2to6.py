import numpy as np, pandas as pd, intraday as I
from concurrent.futures import ProcessPoolExecutor


def per_coin(s):
    df = I.load(s); c = df.close; o = df.open; h = df.high; l = df.low
    out = {k: [] for k in ("2 波动突破", "3 开盘区间突破(UTC0点)", "3 开盘区间突破(美股开盘)", "5 急跌反弹", "6 资金费结算前后(顺30分动量)", "6 资金费结算前后(反30分动量)")}
    hours = []
    # ---- 2 波动突破（UTC 日）
    day = df.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    rng = (day.high - day.low).shift(1)
    for d, r in day.iterrows():
        if not np.isfinite(rng.get(d, np.nan)): continue
        seg = df.loc[d:d + pd.Timedelta(minutes=1439)]
        if len(seg) < 1000: continue
        up, dn = r.open + 0.5 * rng[d], r.open - 0.5 * rng[d]
        hi = np.flatnonzero(seg.high.values >= up); lo = np.flatnonzero(seg.low.values <= dn)
        first = min([x for x in (hi[0] if len(hi) else None, lo[0] if len(lo) else None) if x is not None], default=None)
        if first is None or first >= len(seg) - 2: continue
        side = 1 if (len(hi) and hi[0] == first) else -1
        entry = max(seg.open.values[first], up) if side > 0 else min(seg.open.values[first], dn)
        out["2 波动突破"].append((seg.index[first], side * (seg.close.values[-1] / entry - 1)))
    # ---- 3 开盘区间突破
    for name, start in (("3 开盘区间突破(UTC0点)", "00:00"), ("3 开盘区间突破(美股开盘)", "13:30")):
        for d in day.index:
            t0 = d + pd.Timedelta(start + ":00") - pd.Timestamp("1970-01-01") + pd.Timestamp("1970-01-01") if False else d + pd.to_timedelta(start + ":00")
            rngseg = df.loc[t0:t0 + pd.Timedelta(minutes=29)]
            if len(rngseg) < 25: continue
            top, bot = rngseg.high.max(), rngseg.low.min()
            after = df.loc[t0 + pd.Timedelta(minutes=30):t0 + pd.Timedelta(minutes=30 + 240)]
            if len(after) < 200: continue
            ha, la, ca = after.high.values, after.low.values, after.close.values
            br = np.flatnonzero(ca > top); bd = np.flatnonzero(ca < bot)
            first = min([x for x in (br[0] if len(br) else None, bd[0] if len(bd) else None) if x is not None], default=None)
            if first is None or first >= len(after) - 2: continue
            side = 1 if (len(br) and br[0] == first) else -1
            entry = after.open.values[first + 1]; stop = bot if side > 0 else top
            risk = (entry - stop) * side
            if risk <= 0: continue
            tp = entry + side * 2 * risk; res = ca[-1]
            for k in range(first + 1, len(after)):
                if (side > 0 and la[k] <= stop) or (side < 0 and ha[k] >= stop): res = stop; break
                if (side > 0 and ha[k] >= tp) or (side < 0 and la[k] <= tp): res = tp; break
            out[name].append((after.index[first + 1], side * (res / entry - 1)))
    # ---- 4 时间段：记录每小时收益（后面汇总）
    hr = c.resample("1h").last().pct_change().dropna()
    hours = list(zip(hr.index, hr.values))
    # ---- 5 急跌反弹
    c15 = c.resample("15min").last().dropna(); r15 = c15.pct_change(); vol = r15.rolling(96).std()
    sig = r15 < -3 * vol; last = None
    for t in c15.index[sig.fillna(False).values]:
        if last is not None and t - last < pd.Timedelta(hours=1): continue
        seg = df.loc[t + pd.Timedelta(minutes=15):t + pd.Timedelta(minutes=15 + 60)]
        if len(seg) < 50: continue
        entry = seg.open.values[0]; tp = entry * 1.01
        hit = np.flatnonzero(seg.high.values >= tp)
        res = tp if len(hit) else seg.close.values[-1]
        out["5 急跌反弹"].append((seg.index[0], res / entry - 1)); last = t
    # ---- 6 资金费结算前后
    for t in pd.date_range(df.index[0].ceil("8h"), df.index[-1], freq="8h"):
        a, b, e = t - pd.Timedelta(minutes=60), t - pd.Timedelta(minutes=30), t + pd.Timedelta(minutes=30)
        if a not in c.index or b not in c.index or e not in c.index: continue
        mom = np.sign(c[b] / c[a] - 1)
        if mom == 0: continue
        r = c[e] / c[b] - 1
        out["6 资金费结算前后(顺30分动量)"].append((b, mom * r)); out["6 资金费结算前后(反30分动量)"].append((b, -mom * r))
    return out, hours


if __name__ == "__main__":
    with ProcessPoolExecutor(4) as ex:
        res = list(ex.map(per_coin, I.SYMS))
    allrows = {}
    H = []
    for out, hours in res:
        for k, v in out.items(): allrows.setdefault(k, []).extend(v)
        H += hours
    rows = []
    for k in sorted(allrows):
        rows += I.summ(allrows[k], k)
    # 4 时间段：训练段挑小时，检验段用
    h = pd.DataFrame(H, columns=["t", "r"]); h["hour"] = (h.t - pd.Timedelta(hours=1)).dt.hour   # 这一小时开始的钟点
    tr = h[(h.t >= I.TR0) & (h.t < I.SPLIT)].groupby("hour").r.mean()
    best, worst = tr.nlargest(2).index.tolist(), tr.nsmallest(2).index.tolist()
    sel = []
    for _, r in h.iterrows() if False else []: pass
    hh = h[h.hour.isin(best + worst)].copy(); hh["side"] = np.where(hh.hour.isin(best), 1, -1)
    rows += I.summ(list(zip(hh.t, hh.side * hh.r)), f"4 时间段(多{best}点/空{worst}点 UTC)")
    pd.set_option("display.width", 250); print(pd.DataFrame(rows).to_string(index=False))
