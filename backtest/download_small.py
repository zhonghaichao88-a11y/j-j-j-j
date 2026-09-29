#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载 Binance 公共历史 5m K线（小市值/高波动 alt），合并为每个币种一个 CSV。
与 download_data.py 同源（data.binance.vision，现货裸K代理），输出到 data_small/。
口径与现有 data/*.csv 完全一致：open_ms,open,high,low,close,vol。
"""
import io, os, sys, zipfile, urllib.request, time as _t
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

OUT = os.path.join(os.path.dirname(__file__), "data_small")
os.makedirs(OUT, exist_ok=True)

# 候选小币/中高波动 alt（USDT 现货，板块分散）。最终只保留全窗口数据完整的。
CANDIDATES = [
    "1000PEPEUSDT", "1000SHIBUSDT", "1000FLOKIUSDT", "WIFUSDT",
    "SUIUSDT", "SEIUSDT", "APTUSDT", "INJUSDT", "TIAUSDT",
    "OPUSDT", "ARBUSDT", "WLDUSDT", "FETUSDT", "RUNEUSDT",
    "AAVEUSDT", "CRVUSDT", "ENSUSDT", "GALAUSDT", "SANDUSDT",
    "AXSUSDT", "CHZUSDT", "LDOUSDT", "ALTUSDT", "JTOUSDT",
    # 现货无 1000 前缀的高波动 meme / 其它小币
    "PEPEUSDT", "SHIBUSDT", "FLOKIUSDT", "MEMEUSDT", "BONKUSDT",
    "RENDERUSDT", "STXUSDT", "IMXUSDT", "ARKMUSDT", "ORDIUSDT",
]
MONTHS = [("2026", m) for m in range(3, 9)]          # 2026-03 .. 2026-08
DAYS = [f"2026-09-{d:02d}" for d in range(1, 16)]    # 2026-09-01..15，与主窗口对齐
BASE = "https://data.binance.vision/data/spot"
COLS = ["open_ms", "open", "high", "low", "close", "vol", "close_ms",
        "qv", "trades", "tbv", "tqv", "x"]


def _get(url, tries=3):
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=45) as r:
                return r.read()
        except Exception as e:
            last = e
            _t.sleep(1.0 * (k + 1))
    raise last


def fetch_one(sym, kind, token):
    if kind == "month":
        y, m = token
        url = f"{BASE}/monthly/klines/{sym}/5m/{sym}-5m-{y}-{m:02d}.zip"
    else:
        url = f"{BASE}/daily/klines/{sym}/5m/{sym}-5m-{token}.zip"
    raw = _get(url)
    text = None
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
        text = z.read(z.namelist()[0]).decode("utf-8", "ignore")
    except Exception:
        text = raw.decode("utf-8", "ignore")
    if not text.strip() or "404" in text[:80] or "No such file" in text[:120]:
        return sym, None
    df = pd.read_csv(io.StringIO(text), header=None, names=COLS, usecols=range(12))
    return sym, df


def probe(sym):
    """只探 2026-03 与 2026-08 两个月文件是否存在，判断全窗口是否可用。"""
    ok = 0
    for tok in [("2026", 3), ("2026", 8)]:
        try:
            _, df = fetch_one(sym, "month", tok)
            if df is not None and len(df) > 1000:
                ok += 1
        except Exception:
            pass
    return sym, ok == 2


def main():
    probe_only = "--probe" in sys.argv
    print("探测候选小币全窗口数据可用性 ...", flush=True)
    avail = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(probe, s): s for s in CANDIDATES}
        for f in as_completed(futs):
            sym, ok = f.result()
            print(f"  {'OK ' if ok else 'NO '} {sym}", flush=True)
            if ok:
                avail.append(sym)
    avail = [s for s in CANDIDATES if s in avail]
    print("全窗口可用：", avail, flush=True)
    if probe_only:
        return
    for sym in avail:
        jobs = [(sym, "month", tok) for tok in MONTHS] + [(sym, "day", d) for d in DAYS]
        parts = []
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = [ex.submit(fetch_one, *j) for j in jobs]
            for f in as_completed(futs):
                try:
                    _, df = f.result()
                    if df is not None:
                        parts.append(df)
                except Exception:
                    pass
        if not parts:
            print(sym, "无数据", flush=True); continue
        df = pd.concat(parts, ignore_index=True)
        df = df[["open_ms", "open", "high", "low", "close", "vol"]]
        om = df["open_ms"].astype("int64")
        if om.iloc[0] > 10**14:        # 时间戳可能是微秒或毫秒，统一为毫秒
            om = om // 1000
        df["open_ms"] = om
        df = df.drop_duplicates("open_ms").sort_values("open_ms").reset_index(drop=True)
        for c in ["open", "high", "low", "close", "vol"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna()
        p = os.path.join(OUT, f"{sym}_5m.csv")
        df.to_csv(p, index=False)
        print(f"  saved {sym} rows={len(df)} {pd.to_datetime(df.open_ms.iloc[0],unit='ms')} -> {pd.to_datetime(df.open_ms.iloc[-1],unit='ms')}", flush=True)


if __name__ == "__main__":
    main()
