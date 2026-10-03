"""币安 U本位永续 日K + 资金费率（data.binance.vision 公开存档，含已下架币，避免只测活下来的币）。
币池事先定死：2020 年底前在币安上合约的币（不按后来涨没涨挑）。"""
import io, os, zipfile, urllib.request, concurrent.futures as cf
import numpy as np, pandas as pd
COINS = ("BTC ETH BCH XRP EOS LTC TRX ETC LINK XLM ADA XMR DASH ZEC XTZ BNB ATOM ONT IOTA BAT VET NEO QTUM IOST THETA ALGO ZIL "
         "KNC ZRX COMP OMG DOGE SXP KAVA BAND RLC WAVES MKR SNX DOT YFI BAL CRV TRB RUNE SUSHI SRM EGLD SOL ICX STORJ BLZ UNI "
         "AVAX FTM ENJ FLM REN KSM NEAR AAVE FIL RSR LRC MATIC OCEAN CVC BEL CTK AXS ALPHA ZEN SKL GRT 1INCH LUNA").split()
MONTHS = [f"{y}-{m:02d}" for y in range(2020, 2027) for m in range(1, 13) if f"{y}-{m:02d}" <= "2026-09"]
OUT = "data"; os.makedirs(OUT, exist_ok=True)
B = "https://data.binance.vision/data/futures/um/monthly"


def fetch(url):
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            z = zipfile.ZipFile(io.BytesIO(r.read()))
            return z.read(z.namelist()[0]).decode()
    except Exception:
        return None


def coin(c):
    s = c + "USDT"
    k, f = [], []
    for m in MONTHS:
        t = fetch(f"{B}/klines/{s}/1d/{s}-1d-{m}.zip")
        if t:
            k += [l.split(",")[:6] for l in t.splitlines() if l and l[0].isdigit()]
        t = fetch(f"{B}/fundingRate/{s}/{s}-fundingRate-{m}.zip")
        if t:
            f += [l.split(",")[:3] for l in t.splitlines() if l and l[0].isdigit()]
    if not k:
        return c, 0
    a = np.array(k, dtype=float)
    fr = np.array([[float(x[0]), float(x[2])] for x in f]) if f else np.zeros((0, 2))
    np.savez(f"{OUT}/{c}.npz", ts=a[:, 0], open=a[:, 1], high=a[:, 2], low=a[:, 3], close=a[:, 4], vol=a[:, 5],
             f_ts=fr[:, 0] if len(fr) else fr, f_rate=fr[:, 1] if len(fr) else fr)
    return c, len(a)


with cf.ThreadPoolExecutor(8) as ex:
    for c, n in ex.map(coin, COINS):
        print(c, n, flush=True)
