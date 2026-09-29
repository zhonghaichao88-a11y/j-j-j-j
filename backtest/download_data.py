#!/usr/bin/env python3
"""下载 Binance 公共历史5m K线(data.binance.vision)，合并为每个币种一个 CSV。
数据为现货，仅用于裸K价格行为回测；结构/形态在各交易所间高度一致。"""
import io, os, sys, zipfile, urllib.request, calendar, time as _t
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

OUT = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(OUT, exist_ok=True)
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "LINKUSDT"]
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "LINKUSDT"]
# 月度 2026-03 .. 2026-08，日度 2026-09-01..15
MONTHS = [("2026", m) for m in range(3, 9)]
DAYS = [f"2026-09-{d:02d}" for d in range(1, 16)]
BASE = "https://data.binance.vision/data/spot"
COLS = ["open_ms", "open", "high", "low", "close", "vol", "close_ms", "qv", "trades", "tbv", "tqv", "x"]

def _get(url, tries=3):
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:
            last = e
            _t.sleep(1.5 * (k + 1))
    raise last

def fetch_one(sym, kind, token):
    if kind == "month":
        y, m = token
        url = f"{BASE}/monthly/klines/{sym}/5m/{sym}-5m-{y}-{m:02d}.zip"
    else:
        url = f"{BASE}/daily/klines/{sym}/5m/{sym}-5m-{token}.zip"
    raw = _get(url)
    # 服务端可能返回真 zip，也可能直接返回 csv 文本
    text = None
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
        text = z.read(z.namelist()[0]).decode("utf-8", "ignore")
    except Exception:
        text = raw.decode("utf-8", "ignore")
    if not text.strip() or "404" in text[:80]:
        return sym, None
    df = pd.read_csv(io.StringIO(text), header=None, names=COLS, usecols=range(12))
    return sym, df

def main():
    jobs = []
    for sym in SYMBOLS:
        for tok in MONTHS:
            jobs.append((sym, "month", tok))
        for d in DAYS:
            jobs.append((sym, "day", d))
    parts = {s: [] for s in SYMBOLS}
    done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(fetch_one, *j) for j in jobs]
        for f in as_completed(futs):
            sym, df = f.result()
            if df is not None and len(df):
                parts[sym].append(df)
            done += 1
            if done % 20 == 0:
                print(f"  progress {done}/{len(jobs)}", flush=True)
    for sym in SYMBOLS:
        if not parts[sym]:
            print(f"[WARN] {sym} 无数据"); continue
        df = pd.concat(parts[sym], ignore_index=True)
        # 时间戳可能是微秒或毫秒，统一为毫秒
        om = df["open_ms"].astype("int64")
        if om.iloc[0] > 10**14:
            om = om // 1000
        df["open_ms"] = om
        df = df[["open_ms", "open", "high", "low", "close", "vol"]]
        for c in ["open", "high", "low", "close", "vol"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna().drop_duplicates("open_ms").sort_values("open_ms").reset_index(drop=True)
        path = os.path.join(OUT, f"{sym}_5m.csv")
        df.to_csv(path, index=False)
        span = (df["open_ms"].iloc[-1] - df["open_ms"].iloc[0]) / 86400000
        print(f"[OK] {sym}: {len(df)} bars, {span:.1f} days, {pd.to_datetime(df.open_ms.iloc[0],unit='ms')} -> {pd.to_datetime(df.open_ms.iloc[-1],unit='ms')}")

if __name__ == "__main__":
    main()
