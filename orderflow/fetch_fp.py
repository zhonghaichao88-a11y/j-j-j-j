"""下载欧易逐笔成交（官方历史数据），压成 1 分钟足迹数据：每分钟每个价位的主动卖量(bid)、主动买量(ask)。
原始 csv 用完就删，只留压缩结果。"""
import os, sys, io, zipfile, datetime as dt, numpy as np, pandas as pd, httpx
from concurrent.futures import ThreadPoolExecutor
OUT = os.environ.get("OF_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "fp_data"))
CFG = {"BTC": (0.01, 5.0), "ETH": (0.1, 0.5), "SOL": (1.0, 0.05)}   # 合约面值, 价位格子
proxy = os.environ.get("PROXY_URL") or os.environ.get("HTTPS_PROXY")

def one(sym, day):
    ct, tick = CFG[sym]
    out = f"{OUT}/{sym}_{day:%Y%m%d}.npz"
    if os.path.exists(out):
        return "skip"
    url = f"https://static.okx.com/cdn/okex/traderecords/trades/daily/{day:%Y%m%d}/{sym}-USDT-SWAP-trades-{day:%Y-%m-%d}.zip"
    for k in range(4):
        try:
            r = httpx.get(url, timeout=120, proxy=proxy)
            if r.status_code == 200:
                break
        except Exception as e:
            r = None
    if r is None or r.status_code != 200:
        return f"fail {r.status_code if r is not None else 'net'}"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    df = pd.read_csv(z.open(z.namelist()[0]), usecols=["side", "price", "size", "created_time"])
    df = df.sort_values("created_time", kind="stable")
    q = df["size"].to_numpy() * ct
    px = df["price"].to_numpy()
    m = (df["created_time"].to_numpy() // 60000).astype(np.int64)
    b = np.round(px / tick).astype(np.int64)
    buy = (df["side"].to_numpy() == "buy")
    g = pd.DataFrame({"m": m, "b": b, "ask": np.where(buy, q, 0.0), "bid": np.where(buy, 0.0, q)})
    fp = g.groupby(["m", "b"], sort=True)[["bid", "ask"]].sum().reset_index()
    o = pd.DataFrame({"m": m, "p": px}).groupby("m")["p"].agg(["first", "max", "min", "last"]).reset_index()
    np.savez_compressed(out, m=fp["m"].to_numpy(np.int64), b=fp["b"].to_numpy(np.int64),
                        bid=fp["bid"].to_numpy(np.float32), ask=fp["ask"].to_numpy(np.float32),
                        om=o["m"].to_numpy(np.int64), o=o["first"].to_numpy(), h=o["max"].to_numpy(),
                        l=o["min"].to_numpy(), c=o["last"].to_numpy(), tick=tick)
    return f"ok {len(df)}"

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    syms = sys.argv[1].split(",")
    d0 = dt.date.fromisoformat(sys.argv[2]); d1 = dt.date.fromisoformat(sys.argv[3])
    jobs = [(s, d0 + dt.timedelta(days=i)) for s in syms for i in range((d1 - d0).days + 1)]
    with ThreadPoolExecutor(3) as ex:
        for (s, d), res in zip(jobs, ex.map(lambda a: one(*a), jobs)):
            print(s, d, res, flush=True)
    print("ALL DONE", flush=True)
