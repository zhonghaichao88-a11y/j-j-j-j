"""只用本地数据测其他币（不下载）：币安 1 小时K线（旧 2021-10~2024-09 + 新 2024-09~2026-08）拼起来合成 4 小时，
资金费用本地 bn_funding.json。排除已经用过的 8 个币。参数固定（只用 BTC+ETH 选出的那组），另跑 16 组看稳不稳。"""
import glob, os, json, itertools, pickle, numpy as np, pandas as pd, atrsys as A
from concurrent.futures import ProcessPoolExecutor
D = "/home/user/okx_data"; K = (240 / 15) ** 0.5
USED = {s + "USDT" for s in "BTC ETH SOL BNB XRP DOGE ADA LINK".split()}
CFGS = list(itertools.product((20, 55), (1.5, 2.5), (2, 3), (20, 25)))
BEST = (55, 1.5, 3, 25)


def load(sym):
    parts = []
    for f in (f"{D}/{sym}_bnold1h.npz", f"{D}/{sym}_bn1h.npz"):
        if os.path.exists(f):
            d = np.load(f)
            parts.append(pd.DataFrame({k: d[k] for k in ("open", "high", "low", "close")}, index=pd.to_datetime(d["ts"], unit="ms")))
    df = pd.concat(parts).sort_index(); df = df[~df.index.duplicated()]
    return df.resample("240min", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


FUND = None
def fund(sym):
    global FUND
    if FUND is None:
        FUND = json.load(open(f"{D}/bn_funding.json"))
    x = FUND.get(sym) or []
    if not x:
        return pd.Series([0.0], index=[pd.Timestamp("2000-01-01")])
    a = np.array(x, dtype=float)
    return pd.Series(a[:, 1], index=pd.to_datetime(a[:, 0], unit="ms").round("min")).sort_index()


def job(sym):
    df = load(sym)
    if len(df) < 6 * 120:
        return sym, None, {}
    f = fund(sym); out = {}
    for cfg in CFGS:
        dc, sl, tr_, adx = cfg
        p = dict(A.P0, tf=240, htf=1440, dc=dc, sl=sl, trail=tr_, adx_min=adx, atr_lo=0.0015 * K, atr_hi=0.03 * K)
        t = A.trades(df, A.indicators(df, p), f, p, 0.0002)
        if len(t): t["sym"] = sym
        out[cfg] = t
    return sym, (df.index[0], df.index[-1], len(df)), out


if __name__ == "__main__":
    syms = sorted({os.path.basename(f).split("_bn")[0] for f in glob.glob(f"{D}/*_bn1h.npz") + glob.glob(f"{D}/*_bnold1h.npz")} - USED)
    res, info = {}, {}
    with ProcessPoolExecutor(4) as ex:
        for sym, inf, out in ex.map(job, syms, chunksize=4):
            if inf: res[sym], info[sym] = out, inf
    pickle.dump((res, info), open("grid_local.pkl", "wb"))
    print("测了", len(res), "个币（跳过数据不足 120 天的", len(syms) - len(res), "个）")
