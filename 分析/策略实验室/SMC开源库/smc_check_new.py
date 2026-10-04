"""在没用过的新币上复测（2021 年币池以外、只有 2024-09 以后数据的币）：A 扫流动性+CHoCH 做空、C 任何看跌突破做空、D 随机做空，4 小时。"""
import glob, os, numpy as np, pandas as pd, smc_full as S, nk, smc_check as K
from concurrent.futures import ProcessPoolExecutor
D = "/home/user/okx_data"


def load_new(sym):
    d = np.load(f"{D}/{sym}_bn1h.npz")
    df = pd.DataFrame({k: d[k] for k in ("open", "high", "low", "close")}, index=pd.to_datetime(d["ts"], unit="ms"))
    df = df[~df.index.duplicated()].sort_index()
    return S.resample(df, "4h")


def one(sym):
    nk_load = nk.load
    nk.load = lambda s, tf: load_new(s)
    try:
        return K.one(sym)
    finally:
        nk.load = nk_load


if __name__ == "__main__":
    old = set(nk.coins())
    new = sorted(os.path.basename(f).split("_bn1h")[0] for f in glob.glob(f"{D}/*_bn1h.npz"))
    new = [s for s in new if s not in old]
    with ProcessPoolExecutor(4) as ex:
        res = sum(ex.map(one, new, chunksize=4), [])
    d = pd.DataFrame(res, columns=["组", "币", "t", "R"]); d["半年"] = d.t.dt.year.astype(str) + np.where(d.t.dt.month <= 6, "上", "下")
    pf = lambda x: round(x[x > 0].sum() / -x[x < 0].sum(), 2) if (x < 0).any() else np.nan
    print(f"新币 {d.币.nunique()} 个（2024-09~2026-08）")
    print(d.groupby("组").R.agg(笔数="size", 胜率=lambda x: f"{(x>0).mean()*100:.0f}%", 平均R=lambda x: round(x.mean(), 3), PF=pf).to_string())
    print(d.groupby(["组", "半年"]).R.apply(pf).unstack().to_string())
