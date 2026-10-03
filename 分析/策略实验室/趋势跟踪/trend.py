"""趋势跟踪回测（规则先定死）。日线收盘出信号，第二天开盘成交。
S1 唐奇安突破 N/M：收盘 > 前 N 天最高 → 多；收盘 < 前 M 天最低 → 平（多空版对称做空）
S2 均线：收盘 > N 日均线 → 多，否则平（多空版：下方做空）
S3 时间序列动量：过去 N 天涨 → 多，否则平（多空版：跌就空）
仓位：每个币 = 目标波动 / 该币 30 天波动，再除以当时可交易币数；总杠杆最多 2 倍；单币最多 1 倍。
费用：成交额 0.1%/边（手续费+滑点）；资金费：多头付、空头收（真实费率）。
"""
import glob, os, sys
import numpy as np, pandas as pd

COST = 0.001
TARGET_VOL = 0.5          # 单币年化目标波动（再按币数平分）


def load():
    px, fund = {}, {}
    for f in sorted(glob.glob("data/*.npz")):
        c = os.path.basename(f)[:-4]
        d = np.load(f)
        idx = pd.to_datetime(d["ts"], unit="ms").normalize()
        df = pd.DataFrame({k: d[k] for k in ("open", "high", "low", "close")}, index=idx)
        df = df[~df.index.duplicated()].sort_index()
        px[c] = df
        if len(d["f_ts"]):
            fr = pd.Series(d["f_rate"], index=pd.to_datetime(d["f_ts"], unit="ms").floor("D"))
            fund[c] = fr.groupby(level=0).sum()
    return px, fund


def signal(df, kind, n, m=None, ls=False):
    c = df.close
    if kind == "donch":
        hi = df.high.rolling(n).max().shift(1); lo = df.low.rolling(m).min().shift(1)
        hi2 = df.high.rolling(m).max().shift(1); lo2 = df.low.rolling(n).min().shift(1)
        pos = np.zeros(len(c)); p = 0
        for i in range(len(c)):
            if np.isnan(hi.iloc[i]):
                pos[i] = 0; continue
            if p == 1 and c.iloc[i] < lo.iloc[i]: p = 0
            if p == -1 and c.iloc[i] > hi2.iloc[i]: p = 0
            if p <= 0 and c.iloc[i] > hi.iloc[i]: p = 1
            if ls and p >= 0 and c.iloc[i] < lo2.iloc[i]: p = -1
            pos[i] = p
        return pd.Series(pos, index=c.index)
    if kind == "sma":
        s = c.rolling(n).mean()
        p = np.where(c > s, 1, -1 if ls else 0)
        return pd.Series(np.where(s.isna(), 0, p), index=c.index)
    if kind == "tsmom":
        r = c / c.shift(n) - 1
        p = np.where(r > 0, 1, -1 if ls else 0)
        return pd.Series(np.where(r.isna(), 0, p), index=c.index)
    if kind == "hold":
        return pd.Series(1.0, index=c.index)


def backtest(px, fund, kind, n=None, m=None, ls=False, coins=None, vol_target=True, max_lev=2.0):
    coins = coins or list(px)
    days = pd.date_range("2020-03-01", max(px[c].index[-1] for c in coins), freq="D")
    rets, wts = {}, {}
    for c in coins:
        df = px[c]
        sig = signal(df, kind, n, m, ls)
        vol = df.close.pct_change().rolling(30).std() * np.sqrt(365)
        w = sig * ((TARGET_VOL / vol).clip(upper=1.0) if vol_target else 1.0)
        w = w.where(vol.notna(), 0.0)
        # 信号在 t 日收盘，t+1 开盘成交：t+1 当天收益 = 开→收 用新仓位，之前的收→开 用旧仓位
        o, cl = df.open, df.close
        r_on = o / cl.shift(1) - 1            # 前收 → 今开（旧仓位）
        r_day = cl / o - 1                    # 今开 → 今收（新仓位）
        wts[c] = w.reindex(days)
        f = fund.get(c, pd.Series(dtype=float)).reindex(df.index).fillna(0)
        rets[c] = pd.DataFrame({"on": r_on, "day": r_day, "f": f}).reindex(days)
    W = pd.DataFrame(wts)                                 # 当天收盘决定的目标权重（单币口径）
    alive = W.notna()
    nalive = alive.sum(axis=1).clip(lower=1)
    W = W.fillna(0).div(nalive, axis=0) if len(coins) > 1 else W.fillna(0)
    lev = W.abs().sum(axis=1)
    W = W.div((lev / max_lev).clip(lower=1), axis=0)
    Wold, Wnew = W.shift(2).fillna(0), W.shift(1).fillna(0)   # 今天开盘前持有的 / 今天开盘换成的
    on = pd.DataFrame({c: rets[c]["on"] for c in coins}).fillna(0)
    dy = pd.DataFrame({c: rets[c]["day"] for c in coins}).fillna(0)
    fr = pd.DataFrame({c: rets[c]["f"] for c in coins}).fillna(0)
    # 下架：数据没了当天按 0 收益处理，并在之前一天已平（权重随 alive 消失 → 换仓成本计入）
    pnl = (Wold * on).sum(axis=1) + (Wnew * dy).sum(axis=1) - (Wnew * fr).sum(axis=1) - (Wnew - Wold).abs().sum(axis=1) * COST
    eq = (1 + pnl).cumprod()
    return pnl, eq, Wnew


def stats(pnl, a=None, b=None):
    p = pnl[(pnl.index >= (a or pnl.index[0])) & (pnl.index <= (b or pnl.index[-1]))]
    eq = (1 + p).cumprod()
    yrs = len(p) / 365
    cagr = eq.iloc[-1] ** (1 / yrs) - 1 if yrs > 0 else 0
    dd = (eq / eq.cummax() - 1).min()
    sh = p.mean() / p.std() * np.sqrt(365) if p.std() > 0 else 0
    return dict(总收益=f"{(eq.iloc[-1]-1)*100:+.0f}%", 年化=f"{cagr*100:+.0f}%", 最大回撤=f"{dd*100:.0f}%", 夏普=round(sh, 2))
