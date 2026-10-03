"""其他 71 个币的 4 小时K线（币池 = 趋势跟踪里 2020 年底前上合约的 79 个币，去掉已测的 8 个；含已下架币）"""
import io, os, sys, zipfile, urllib.request, concurrent.futures as cf
import numpy as np
import re, ast
src = open("../趋势跟踪/dl.py", encoding="utf-8").read()
COINS = " ".join(ast.literal_eval(m) for m in re.findall(r'"[A-Z0-9 ]+ "|"[A-Z0-9 ]+"', src[src.index("COINS"):src.index(".split()")])).split()
DONE = set("BTC ETH SOL BNB XRP DOGE ADA LINK".split())
MONTHS = [f"{y}-{m:02d}" for y in range(2020, 2027) for m in range(1, 13) if f"{y}-{m:02d}" <= "2026-09"]
os.makedirs("data4h", exist_ok=True)


def fetch(c, m):
    s = c + "USDT"
    try:
        with urllib.request.urlopen(f"https://data.binance.vision/data/futures/um/monthly/klines/{s}/4h/{s}-4h-{m}.zip", timeout=60) as r:
            z = zipfile.ZipFile(io.BytesIO(r.read()))
            return [l.split(",")[:6] for l in z.read(z.namelist()[0]).decode().splitlines() if l and l[0].isdigit()]
    except Exception:
        return []


def coin(c):
    rows = sum((fetch(c, m) for m in MONTHS), [])
    if not rows:
        return c, 0
    a = np.array(rows, dtype=float); a = a[np.argsort(a[:, 0])]
    np.savez(f"data4h/{c}.npz", ts=a[:, 0], open=a[:, 1], high=a[:, 2], low=a[:, 3], close=a[:, 4], vol=a[:, 5])
    return c, len(a)


with cf.ThreadPoolExecutor(10) as ex:
    for c, n in ex.map(coin, [c for c in COINS if c not in DONE]):
        print(c, n, flush=True)
