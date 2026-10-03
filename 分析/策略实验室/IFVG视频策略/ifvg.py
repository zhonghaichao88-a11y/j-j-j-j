"""ICT IFVG 模型（视频里讲的）机械化回测。规则先写死再跑，不看结果改：
时间：纽约时间 9:30-11:00 才找入场；一天只做一笔（one bullet）；16:00 没到止盈止损就平掉。
关键位（做多看下方流动性，做空看上方，镜像）：前一天高低点、亚盘(前日20:00-24:00)高低点、伦敦盘(02:00-05:00)高低点。
  关键位要在 9:30 时还没被碰过。
1 扫位：窗口内价格刺破一个关键位（做多：最低价 < 关键位）。
2 操控腿：从扫位前 60 分钟内的最高点，到扫位后的最低点。
3 选周期：1/2/3/4/5 分钟里，找操控腿里刚好只有 1 个反向 FVG 的最小周期（视频原话）。都不是 1 个就不做。
4 确认（IFVG）：该周期K线收盘站上这个看跌 FVG 的上沿 → 收盘价市价进场。进场前创新低就重新算操控腿。
5 止损：操控腿最低点；止盈：1R（另测 2R）。同一根1分钟K线里止盈止损都碰到 → 算止损（保守）。
费用：每边 0.05%（欧易吃单）+ 0.02% 滑点。
"""
import numpy as np, pandas as pd, sys
from zoneinfo import ZoneInfo
NY = ZoneInfo("America/New_York")
FEE = 0.0007          # 单边手续费+滑点
HTF_FVG = False


def load_npz(path):
    d = np.load(path)
    df = pd.DataFrame({k: d[k] for k in ("open", "high", "low", "close")})
    df.index = pd.to_datetime(d["ts"], unit="ms", utc=True).tz_convert(NY)
    return df[~df.index.duplicated()].sort_index()


def fvgs(bars, bearish):
    """返回 [(形成时间, 上沿, 下沿)]。看跌FVG：前前根最低 > 当前根最高"""
    h, l = bars["high"].values, bars["low"].values
    out = []
    for i in range(2, len(bars)):
        if bearish and l[i - 2] > h[i]:
            out.append((bars.index[i], l[i - 2], h[i]))
        if not bearish and h[i - 2] < l[i]:
            out.append((bars.index[i], l[i], h[i - 2]))
    return out


def resample(m1, tf):
    if tf == 1:
        return m1
    return m1.resample(f"{tf}min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


def day_levels(df, day):
    prev = df[(df.index >= day - pd.Timedelta(days=1)) & (df.index < day)]
    asia = df[(df.index >= day - pd.Timedelta(hours=4)) & (df.index < day)]
    lon = df[(df.index >= day + pd.Timedelta(hours=2)) & (df.index < day + pd.Timedelta(hours=5))]
    if len(prev) < 1000 or len(lon) < 100:
        return None
    return {"PDH": prev.high.max(), "PDL": prev.low.min(), "AsiaH": asia.high.max(), "AsiaL": asia.low.min(),
            "LonH": lon.high.max(), "LonL": lon.low.min(), "pd_open": prev.open.iloc[0], "pd_close": prev.close.iloc[-1]}


def find_setup(df, day, side, rr):
    """side=+1 做多（扫下方），-1 做空（扫上方）。返回交易或 None"""
    lv = day_levels(df, day)
    if lv is None:
        return None
    t0, t1, tend = day + pd.Timedelta(hours=9, minutes=30), day + pd.Timedelta(hours=11), day + pd.Timedelta(hours=16)
    pre = df[(df.index >= day + pd.Timedelta(hours=5)) & (df.index < t0)]
    names = ("PDL", "AsiaL", "LonL") if side > 0 else ("PDH", "AsiaH", "LonH")
    # 9:30 前还没被碰过的位（伦敦结束到开盘之间被扫掉的不算；前日/亚盘位在伦敦盘被扫的也不算）
    after = df[(df.index >= day + pd.Timedelta(hours=2)) & (df.index < t0)] if True else pre
    levels = []
    for n in names:
        v = lv[n]
        seg = df[(df.index >= day + pd.Timedelta(hours=5)) & (df.index < t0)] if n.startswith("Lon") else after
        if side > 0 and seg.low.min() > v or side < 0 and seg.high.max() < v:
            levels.append((n, v))
    if HTF_FVG:                                 # 变体B：15分钟/1小时未回补的FVG也算关键位（视频："5分钟以上的FVG或重要流动性"）
        hist = df[(df.index >= day - pd.Timedelta(hours=24)) & (df.index < t0)]
        for tf in (15, 60):
            b = resample(hist, tf)
            for gt, gtop, gbot in fvgs(b, bearish=(side < 0)):
                after_g = df[(df.index >= gt + pd.Timedelta(minutes=tf)) & (df.index < t0)]
                edge = gtop if side > 0 else gbot           # 做多：价格回到看涨FVG上沿以下；做空：回到看跌FVG下沿以上
                if after_g.empty:
                    continue
                if (side > 0 and after_g.low.min() > edge) or (side < 0 and after_g.high.max() < edge):
                    levels.append((f"FVG{tf}", edge))
    if not levels:
        return None
    win = df[(df.index >= t0) & (df.index < t1)]
    swept = None
    for ts, r in win.iterrows():
        if swept is None:
            for n, v in levels:
                if (side > 0 and r.low < v) or (side < 0 and r.high > v):
                    swept = (ts, n)
                    break
            if swept is None:
                continue
        # 已扫位：建操控腿，找 IFVG
        start = swept[0] - pd.Timedelta(minutes=60)
        leg_all = df[(df.index >= start) & (df.index <= ts)]
        if side > 0:
            ext_t = leg_all.low.idxmin(); top_t = leg_all.loc[:ext_t].high.idxmax()
        else:
            ext_t = leg_all.high.idxmax(); top_t = leg_all.loc[:ext_t].low.idxmin()
        leg = df[(df.index >= top_t) & (df.index <= ext_t)]
        if len(leg) < 3 or ts == ext_t:
            continue
        cand = None
        for tf in (1, 2, 3, 4, 5):
            b = resample(leg, tf)
            g = fvgs(b, bearish=(side > 0))
            if len(g) == 1:
                cand = (tf, g[0]); break
        if cand is None:
            continue
        tf, (_, gtop, gbot) = cand
        # 只在这个周期的K线收盘时检查（收盘时刻 = 这根1分钟K线结束且对齐周期）
        minute = ts.hour * 60 + ts.minute
        if (minute + 1) % tf:
            continue
        bar = resample(df[(df.index > ext_t) & (df.index <= ts)], tf)
        if bar.empty:
            continue
        close = r.close
        if (side > 0 and close > gtop) or (side < 0 and close < gbot):
            entry = close
            stop = df.loc[ext_t].low if side > 0 else df.loc[ext_t].high
            risk = (entry - stop) * side
            if risk <= 0:
                return None
            tp = entry + side * rr * risk
            fut = df[(df.index > ts) & (df.index < tend)]
            res, exit_px, exit_t = None, None, None
            for ft, fr in fut.iterrows():
                hit_sl = (fr.low <= stop) if side > 0 else (fr.high >= stop)
                hit_tp = (fr.high >= tp) if side > 0 else (fr.low <= tp)
                if hit_sl:
                    res, exit_px, exit_t = "SL", stop, ft; break
                if hit_tp:
                    res, exit_px, exit_t = "TP", tp, ft; break
            if res is None:
                res, exit_px, exit_t = "EOD", fut.close.iloc[-1] if len(fut) else entry, fut.index[-1] if len(fut) else ts
            gross_r = (exit_px - entry) * side / risk
            cost_r = FEE * 2 * entry / risk
            return dict(day=day.date(), side=side, level=swept[1], tf=tf, entry_t=ts, entry=entry, stop=stop, tp=tp,
                        risk_pct=risk / entry * 100, res=res, exit_t=exit_t, R=gross_r, R_net=gross_r - cost_r,
                        bias_pd=np.sign(lv["pd_close"] - lv["pd_open"]))
    return None


def run(df, rr=1.0):
    days = pd.date_range(df.index[0].normalize() + pd.Timedelta(days=2), df.index[-1].normalize() - pd.Timedelta(days=1), freq="D", tz=NY)
    out = []
    for d in days:
        d = d.normalize()
        if d.weekday() >= 5:          # 视频只做工作日纽约盘
            continue
        best = []
        for side in (1, -1):
            t = find_setup(df, d, side, rr)
            if t:
                best.append(t)
        if best:
            out.append(min(best, key=lambda t: t["entry_t"]))   # 一天一发子弹：先出现的那笔
    return pd.DataFrame(out)


if __name__ == "__main__":
    sym, rr = sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
    HTF_FVG = len(sys.argv) > 3 and sys.argv[3] == "B"
    tag = "B" if HTF_FVG else ""
    df = load_npz(f"/home/user/okx_data/{sym}-USDT-SWAP_1m2y.npz")
    t = run(df, rr)
    t.to_csv(f"trades{tag}_{sym}_{rr:g}R.csv", index=False)
    if len(t):
        print(tag, sym, rr, "笔数", len(t), "胜率(只算TP/SL)", round((t.res == "TP").mean() * 100, 1),
              "平均R毛", round(t.R.mean(), 3), "平均R扣费", round(t.R_net.mean(), 3), "止损中位%", round(t.risk_pct.median(), 3))
