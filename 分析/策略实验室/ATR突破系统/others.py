"""固定参数（只用 BTC+ETH 选出：4h, 通道55, 止损1.5ATR, 跟踪3ATR, ADX25）跑其他币；另外 16 组参数全跑看稳不稳。"""
import glob, os, itertools, pickle, numpy as np, pandas as pd, atrsys as A
from concurrent.futures import ProcessPoolExecutor
K = (240 / 15) ** 0.5


def load4h(c):
    d = np.load(f"data4h/{c}.npz")
    df = pd.DataFrame({k: d[k] for k in ("open", "high", "low", "close")}, index=pd.to_datetime(d["ts"], unit="ms"))
    return df[~df.index.duplicated()]


def job(args):
    c, dc, sl, tr_, adx = args
    p = dict(A.P0, tf=240, htf=1440, dc=dc, sl=sl, trail=tr_, adx_min=adx, atr_lo=0.0015 * K, atr_hi=0.03 * K)
    df = load4h(c)
    if len(df) < 6 * 120:                      # 不到 120 天数据的跳过
        return (c, dc, sl, tr_, adx), pd.DataFrame()
    t = A.trades(df, A.indicators(df, p), A.funding(c), p, 0.0002)
    if len(t): t["sym"] = c
    return (c, dc, sl, tr_, adx), t


if __name__ == "__main__":
    coins = sorted(os.path.basename(f)[:-4] for f in glob.glob("data4h/*.npz"))
    combos = [(c,) + cfg for c in coins for cfg in itertools.product((20, 55), (1.5, 2.5), (2, 3), (20, 25))]
    res = {}
    with ProcessPoolExecutor(4) as ex:
        for k, t in ex.map(job, combos, chunksize=4):
            res[k] = t
    pickle.dump(res, open("grid_others.pkl", "wb"))
    BEST = (55, 1.5, 3, 25)
    fx = pd.concat([res[(c,) + BEST] for c in coins if len(res[(c,) + BEST])])
    print(f"固定参数，{fx.sym.nunique()} 个其他币:", A.summarize(fx))
    fx["yr"] = pd.to_datetime(fx.t_in).dt.year
    for y, g in fx.groupby("yr"): print("  ", y, A.summarize(g))
    print("  最近12个月", A.summarize(fx[pd.to_datetime(fx.t_in) >= "2025-10-01"]))
    pc = fx.groupby("sym").apply(lambda g: A.summarize(g)["PF"])
    print("  赚钱的币", (pc > 1).sum(), "/", len(pc), "  PF中位", round(pc.median(), 2))
    print("  最好5个", pc.sort_values().tail(5).round(2).to_dict()); print("  最差5个", pc.sort_values().head(5).round(2).to_dict())
    rows = []
    for cfg in itertools.product((20, 55), (1.5, 2.5), (2, 3), (20, 25)):
        t = pd.concat([res[(c,) + cfg] for c in coins if len(res[(c,) + cfg])])
        rows.append(dict(参数=cfg, **A.summarize(t)))
    print(pd.DataFrame(rows).sort_values("PF").to_string())
    fx.to_csv("trades_others_fixed.csv", index=False)
