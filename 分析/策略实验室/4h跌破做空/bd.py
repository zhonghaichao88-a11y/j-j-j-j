"""4 小时跌破结构顺势做空 + 行情过滤（规则先定死）。
信号：4h 收盘跌破最近已确认（L=5）的波段低点（BOS 或 CHoCH）→ 下一根开盘市价空；止损这段下跌起点的高点；止盈 2R；48 根超时；
同币同时一单；费用 0.07%/边。过滤：F0 无；F1 币自己日线收盘 < 50 日均线；F2 全市场 >60% 的币在 50 日均线下；F3 BTC 日线 < 100 日均线。
过滤都只用前一天已经收盘的日线（不偷看）。"""
import sys, glob, os, numpy as np, pandas as pd
sys.path.insert(0, "../SMC开源库"); sys.path.insert(0, "../裸K形态")
import smc_full as S, nk
from concurrent.futures import ProcessPoolExecutor
D = "/home/user/okx_data"; RR = 2.0


def load(sym, new):
    if not new:
        return nk.load(sym, "4h")
    d = np.load(f"{D}/{sym}_bn1h.npz")
    df = pd.DataFrame({k: d[k] for k in ("open", "high", "low", "close")}, index=pd.to_datetime(d["ts"], unit="ms"))
    return S.resample(df[~df.index.duplicated()].sort_index(), "4h")


def daily_below(df, n):
    dc = df.close.resample("1D").last().dropna()
    m = dc.rolling(n).mean()
    return (dc < m).astype(float).where(m.notna()).shift(1)   # 前一天收盘的状态（1=在均线下，0=上方，空=数据不够）


def signals(args):
    sym, new = args
    df = load(sym, new)
    if len(df) < 600:
        return sym, None, None
    ev, _ = S.structure(df, 5)
    rows, busy = [], -1
    for (t, side, k, leg, sw) in ev:
        if side != -1 or t <= busy:
            continue
        r = S.sim(df, t, -1, leg, "MKT", RR)
        if r:
            busy = r[1]
            rows.append((sym, df.index[t], df.index[r[0]], df.index[r[1]], r[2]))
    below50 = daily_below(df, 50)
    return sym, pd.DataFrame(rows, columns=["sym", "sig", "t_in", "t_out", "R"]), below50


def run(universe, new):
    with ProcessPoolExecutor(4) as ex:
        res = list(ex.map(signals, [(s, new) for s in universe], chunksize=4))
    trades = pd.concat([r[1] for r in res if r[1] is not None and len(r[1])], ignore_index=True)
    b50 = pd.DataFrame({r[0]: r[2] for r in res if r[2] is not None})
    b50 = b50.astype(float)
    breadth = (b50.sum(axis=1) / b50.notna().sum(axis=1).replace(0, np.nan)).rename("breadth")     # 已经是前一天的
    btc = nk.load("BTCUSDT", "1h").close.resample("1D").last()
    bm = btc.rolling(100).mean()
    btc_below = (btc < bm).astype(float).where(bm.notna()).shift(1)
    day = trades.sig.dt.floor("D")
    own = [b50[s].get(d, np.nan) if s in b50 else np.nan for s, d in zip(trades.sym, day)]
    trades["F0"] = True
    trades["F1"] = (pd.Series(own, dtype=float).fillna(0) > 0.5).values
    trades["F2"] = (breadth.reindex(day).fillna(0).values > 0.6)
    trades["F3"] = (btc_below.reindex(day).fillna(0).values > 0.5)
    return trades


def pf(x):
    return round(x[x > 0].sum() / -x[x < 0].sum(), 2) if (x < 0).any() else np.nan


def portfolio(t, risk=0.01, max_open=6):
    """每单亏 1R = 账户 1%；最多同时 max_open 单；按开仓时间顺序，满了就跳过"""
    t = t.sort_values("t_in"); open_ = []; eq = 1.0; curve = []
    for _, r in t.iterrows():
        open_ = [x for x in open_ if x > r.t_in]
        if len(open_) >= max_open:
            continue
        open_.append(r.t_out); eq *= (1 + risk * r.R); curve.append((r.t_out, eq))
    c = pd.Series([v for _, v in curve], index=[k for k, _ in curve]).sort_index()
    return c


if __name__ == "__main__":
    old = nk.coins()
    allnew = sorted(os.path.basename(f).split("_bn1h")[0] for f in glob.glob(f"{D}/*_bn1h.npz"))
    new = [s for s in allnew if s not in set(old)]
    T_old = run(old, False); T_new = run(new, True)
    T_old.to_csv("trades_old.csv", index=False); T_new.to_csv("trades_new.csv", index=False)
    rows = []
    for f in ("F0", "F1", "F2", "F3"):
        a = T_old[T_old[f]]
        tr, te = a[a.t_in < nk.SPLIT].R, a[a.t_in >= nk.SPLIT].R
        b = T_new[T_new[f]].R
        rows.append(dict(过滤=f, 训练笔数=len(tr), 训练PF=pf(tr), 训练平均R=round(tr.mean(), 3), 检验笔数=len(te), 检验PF=pf(te),
                         新币笔数=len(b), 新币PF=pf(b)))
    print("老币 137 个（训练 2021-10~2024-06 / 检验 2024-07~2026-08），新币 296 个（2024-09~2026-08）")
    print(pd.DataFrame(rows).to_string(index=False))
    for f in ("F0", "F1", "F2", "F3"):
        a = T_old[T_old[f]]
        y = a.groupby(a.t_in.dt.year).R.apply(pf)
        print(f, "老币逐年PF:", y.to_dict())
        b = T_new[T_new[f]]
        h = b.groupby(b.t_in.dt.year.astype(str) + np.where(b.t_in.dt.month <= 6, "上", "下")).R.apply(pf)
        print(f, "新币按半年PF:", h.to_dict())
