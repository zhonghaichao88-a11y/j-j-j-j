"""币安 U本位永续 15 分钟K线（data.binance.vision 公开存档）2020-01 ~ 2026-09。资金费率用 ../趋势跟踪/data 里已下的逐笔记录。"""
import io, os, zipfile, urllib.request, concurrent.futures as cf
import numpy as np
COINS = "BTC ETH SOL BNB XRP DOGE ADA LINK".split()
MONTHS = [f"{y}-{m:02d}" for y in range(2020, 2027) for m in range(1, 13) if f"{y}-{m:02d}" <= "2026-09"]
os.makedirs("data15", exist_ok=True)


def fetch(c, m):
    s = c + "USDT"
    try:
        with urllib.request.urlopen(f"https://data.binance.vision/data/futures/um/monthly/klines/{s}/15m/{s}-15m-{m}.zip", timeout=60) as r:
            z = zipfile.ZipFile(io.BytesIO(r.read()))
            return [l.split(",")[:6] for l in z.read(z.namelist()[0]).decode().splitlines() if l and l[0].isdigit()]
    except Exception:
        return []


for c in COINS:
    with cf.ThreadPoolExecutor(8) as ex:
        rows = sum(ex.map(lambda m: fetch(c, m), MONTHS), [])
    a = np.array(rows, dtype=float)
    a = a[np.argsort(a[:, 0])]
    np.savez(f"data15/{c}.npz", ts=a[:, 0], open=a[:, 1], high=a[:, 2], low=a[:, 3], close=a[:, 4], vol=a[:, 5])
    print(c, len(a), flush=True)
