"""下载币安公开归档的合约数据（历史回测用）：持仓量、大户多空比、全体多空比、主动买卖比（15 分钟一条），资金费率。
欧易这些数据只给最近几天，所以历史用币安的（两个交易所走势基本一致）。"""
import datetime as dt
import io
import os
import sys
import zipfile

import httpx
import pandas as pd

OUT = os.environ.get("OF_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "fp_data"))
BASE = "https://data.binance.vision/data/futures/um"
proxy = os.environ.get("PROXY_URL") or os.environ.get("HTTPS_PROXY")


def get_zip_csv(url):
    for _ in range(3):
        try:
            r = httpx.get(url, timeout=60, proxy=proxy)
            if r.status_code == 200:
                z = zipfile.ZipFile(io.BytesIO(r.content))
                return pd.read_csv(z.open(z.namelist()[0]))
            if r.status_code == 404:
                return None
        except Exception:  # noqa: BLE001
            pass
    return None


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    syms = sys.argv[1].split(",")
    d0, d1 = dt.date.fromisoformat(sys.argv[2]), dt.date.fromisoformat(sys.argv[3])
    for s in syms:
        parts = []
        d = d0
        while d <= d1:
            df = get_zip_csv(f"{BASE}/daily/metrics/{s}USDT/{s}USDT-metrics-{d:%Y-%m-%d}.zip")
            if df is not None:
                parts.append(df)
            d += dt.timedelta(days=1)
        m = pd.concat(parts)
        m["t"] = pd.to_datetime(m["create_time"]).astype("datetime64[ms]").astype("int64")
        m = m.rename(columns={"sum_open_interest": "oi", "count_toptrader_long_short_ratio": "top_ls_acc",
                              "sum_toptrader_long_short_ratio": "top_ls_pos", "count_long_short_ratio": "ls",
                              "sum_taker_long_short_vol_ratio": "taker_ratio"})
        m[["t", "oi", "top_ls_acc", "top_ls_pos", "ls", "taker_ratio"]].to_csv(f"{OUT}/{s}_metrics.csv", index=False)
        fr = []
        mo = dt.date(d0.year, d0.month, 1)
        while mo <= d1:
            df = get_zip_csv(f"{BASE}/monthly/fundingRate/{s}USDT/{s}USDT-fundingRate-{mo:%Y-%m}.zip")
            if df is not None:
                fr.append(df)
            mo = (mo.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        f = pd.concat(fr)
        f = f.rename(columns={"calc_time": "t", "last_funding_rate": "funding"})
        f[["t", "funding"]].to_csv(f"{OUT}/{s}_funding.csv", index=False)
        print(s, len(m), "条持仓数据", len(f), "条资金费率", flush=True)
