"""裸K形态机械化检验（规则先定死）。
数据：本地币安永续 1 小时K线（旧 2021-10~2024-09 + 新 2024-09~2026-08 拼接，含后来下架的币），也合成 4 小时。
进场：形态K线收盘确认，下一根开盘市价进场。
止损：形态K线的低点（做多）/高点（做空）；止盈 1.5R；24 根K线没碰到就收盘平。同一根K线止盈止损都碰到算止损。
费用：每边 0.07%（手续费+滑点）。止损距离 <0.2% 或 >8% 的不做。
训练段 2021-10~2024-06 选规则，检验段 2024-07~2026-08 只看结果。"""
import glob, os, numpy as np, pandas as pd
from numpy.lib.stride_tricks import sliding_window_view as swv
D = "/home/user/okx_data"; FEE = 0.0007; RR = 1.5; H = 24; SPLIT = pd.Timestamp("2024-07-01")


def coins():
    old = {os.path.basename(f).split("_bnold1h")[0] for f in glob.glob(f"{D}/*_bnold1h.npz")}
    return sorted(old)


def load(sym, tf="1h"):
    parts = []
    for f in (f"{D}/{sym}_bnold1h.npz", f"{D}/{sym}_bn1h.npz"):
        if os.path.exists(f):
            d = np.load(f)
            parts.append(pd.DataFrame({k: d[k] for k in ("open", "high", "low", "close")}, index=pd.to_datetime(d["ts"], unit="ms")))
    df = pd.concat(parts).sort_index(); df = df[~df.index.duplicated()]
    if tf == "4h":
        df = df.resample("4h").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    return df


def simulate(df, sig_idx, side):
    """sig_idx：形态收盘那根的位置；side +1 多 -1 空。返回每笔的 R（扣费）和时间"""
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    n = len(c)
    sig_idx = sig_idx[(sig_idx + 1 + H) < n]
    if len(sig_idx) == 0:
        return np.array([]), np.array([], dtype="datetime64[ns]")
    entry = o[sig_idx + 1]
    stop = l[sig_idx] if side > 0 else h[sig_idx]
    risk = (entry - stop) * side
    ok = (risk / entry > 0.002) & (risk / entry < 0.08)
    sig_idx, entry, stop, risk = sig_idx[ok], entry[ok], stop[ok], risk[ok]
    tp = entry + side * RR * risk
    HW, LW = swv(h, H)[sig_idx + 1], swv(l, H)[sig_idx + 1]       # 进场那根起往后 H 根
    if side > 0:
        hit_sl, hit_tp = LW <= stop[:, None], HW >= tp[:, None]
    else:
        hit_sl, hit_tp = HW >= stop[:, None], LW <= tp[:, None]
    big = H + 1
    fsl = np.where(hit_sl.any(1), hit_sl.argmax(1), big); ftp = np.where(hit_tp.any(1), hit_tp.argmax(1), big)
    exitp = np.where(fsl <= ftp, np.where(fsl < big, stop, np.nan), tp)            # 同一根先算止损
    tout = (fsl == big) & (ftp == big)
    exitp = np.where(tout, c[sig_idx + H], exitp)
    gross = (exitp - entry) * side / entry
    R = (gross - 2 * FEE) / (risk / entry)
    return R, df.index.values[sig_idx]


def context(df):
    o, h, l, c = (df[k] for k in ("open", "high", "low", "close"))
    rng = (h - l).replace(0, np.nan); body = (c - o).abs()
    up_w = h - np.maximum(o, c); dn_w = np.minimum(o, c) - l
    low20 = l.rolling(20).min(); high20 = h.rolling(20).max()
    ema = c.ewm(span=200, adjust=False).mean()
    return dict(o=o, h=h, l=l, c=c, rng=rng, body=body, up_w=up_w, dn_w=dn_w, low20=low20, high20=high20, ema=ema)


def patterns(df):
    """返回 {名字: (做多信号, 做空信号)}，都是布尔 Series（形态那根收盘时成立）"""
    x = context(df); o, h, l, c = x["o"], x["h"], x["l"], x["c"]
    at_low = l <= x["low20"]; at_high = h >= x["high20"]
    pin_l = (x["dn_w"] >= 2 * x["body"]) & (x["dn_w"] >= 0.6 * x["rng"]) & (c >= l + 0.66 * x["rng"])
    pin_s = (x["up_w"] >= 2 * x["body"]) & (x["up_w"] >= 0.6 * x["rng"]) & (c <= h - 0.66 * x["rng"])
    po, pc = o.shift(1), c.shift(1)
    eng_l = (pc < po) & (c > o) & (c >= po) & (o <= pc)
    eng_s = (pc > po) & (c < o) & (c <= po) & (o >= pc)
    mh, ml = h.shift(2), l.shift(2)                                 # 母线
    inside = (h.shift(1) <= mh) & (l.shift(1) >= ml)
    ib_l = inside & (c > mh); ib_s = inside & (c < ml)
    prev_low20, prev_high20 = l.shift(1).rolling(20).min(), h.shift(1).rolling(20).max()
    b2_l = (l < prev_low20) & (c > prev_low20); b2_s = (h > prev_high20) & (c < prev_high20)
    up, dn = c > x["ema"], c < x["ema"]
    P = {
        "长下影线(任意位置)": (pin_l, pin_s), "长影线+20根低/高点": (pin_l & at_low, pin_s & at_high),
        "长影线+低点+顺大趋势": (pin_l & at_low & up, pin_s & at_high & dn),
        "吞没(任意位置)": (eng_l, eng_s), "吞没+20根低/高点": (eng_l & (at_low | at_low.shift(1)), eng_s & (at_high | at_high.shift(1))),
        "吞没+低点+顺大趋势": (eng_l & (at_low | at_low.shift(1)) & up, eng_s & (at_high | at_high.shift(1)) & dn),
        "内包线突破": (ib_l, ib_s), "内包线突破+顺大趋势": (ib_l & up, ib_s & dn),
        "2B假突破": (b2_l, b2_s), "2B假突破+顺大趋势": (b2_l & up, b2_s & dn),
    }
    return {k: (a.fillna(False).values, b.fillna(False).values) for k, (a, b) in P.items()}


def stats(R):
    if len(R) == 0:
        return dict(笔数=0)
    g, b = R[R > 0].sum(), -R[R < 0].sum()
    return dict(笔数=len(R), 胜率=f"{(R > 0).mean() * 100:.0f}%", 平均R=round(R.mean(), 3), PF=round(g / b, 2) if b else np.inf)
