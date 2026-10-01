"""读取 V7 录下来的实盘数据（V7 文件夹/recorder_data/YYYY-MM-DD.csv 或 .csv.gz）。
每分钟每个币一行：开高低收、主动买卖金额、盘口失衡、前 5 档、资金费率、持仓量、爆仓金额。
订单流程序启动时用它：
  1 选币：按最近 24 小时成交额，自动挑最活跃的几个币
  2 垫底：把最近的爆仓、持仓、盘口失衡、主动买卖填进历史K线，打法一启动就有足够的历史可比
订单流程序自己不再录数据。"""
from __future__ import annotations

import csv
import gzip
import math
import os
from collections import defaultdict
from pathlib import Path

NUM = ("open", "high", "low", "close", "buy_usdt", "sell_usdt", "imbalance", "funding_rate",
       "next_funding_ms", "oi_ccy", "oi_usdt", "liq_buy_usdt", "liq_sell_usdt", "bid5_usdt", "ask5_usdt")


def find_dir(hint: str | None = None) -> Path | None:
    """找 V7 的 recorder_data 文件夹：.env 里写的 > 旁边的 V7 文件夹"""
    cands = []
    if hint:
        h = Path(hint.strip().strip('"'))
        cands += [h, h / "recorder_data"]
    here = Path(__file__).resolve().parent
    for up in (here.parent, here.parent.parent):
        cands += list(up.glob("*V7*/recorder_data")) + list(up.glob("*v7*/recorder_data")) + [up / "recorder_data"]
    for c in cands:
        if c.is_dir() and (list(c.glob("*.csv")) or list(c.glob("*.csv.gz"))):
            return c
    return None


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else math.nan
    except (TypeError, ValueError):
        return math.nan


def load(dirpath: Path, insts=None, days: int = 3) -> dict:
    """读最近几天的数据。返回 {inst: [每分钟一条 dict，按时间排好]}"""
    files = sorted(list(dirpath.glob("*.csv")) + list(dirpath.glob("*.csv.gz")),
                   key=lambda p: p.name.split(".")[0])[-days:]
    out = defaultdict(list)
    want = set(insts) if insts else None
    for f in files:
        op = gzip.open if f.suffix == ".gz" else open
        try:
            with op(f, "rt", encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    inst = r.get("inst")
                    if want and inst not in want:
                        continue
                    d = {k: _f(r.get(k)) for k in NUM}
                    d["ts"] = int(float(r["ts"]))
                    out[inst].append(d)
        except (OSError, EOFError, csv.Error):
            continue
    for k in out:
        out[k].sort(key=lambda d: d["ts"])
    return dict(out)


def top_symbols(data: dict, n: int = 5, hours: int = 24, must=("BTC-USDT-SWAP",)) -> list[str]:
    """按最近 hours 小时的成交额挑最活跃的 n 个币（BTC 一定带上）"""
    if not data:
        return []
    end = max(rows[-1]["ts"] for rows in data.values() if rows)
    start = end - hours * 3_600_000
    score = {}
    for inst, rows in data.items():
        if not inst.endswith("-USDT-SWAP"):
            continue
        score[inst] = sum((r["buy_usdt"] or 0) + (r["sell_usdt"] or 0) for r in rows
                          if r["ts"] >= start and not math.isnan(r["buy_usdt"]))
    picked = [m for m in must if m in score]
    picked += [k for k, _ in sorted(score.items(), key=lambda kv: -kv[1]) if k not in picked]
    return picked[:n]


def bars(rows: list, tf_ms: int) -> dict:
    """每分钟数据合成 tf 的K线字段：{开始时间: {...}}，金额换成币数量（除以收盘价）"""
    out = {}
    for r in rows:
        t0 = r["ts"] - r["ts"] % tf_ms
        c = r["close"] if not math.isnan(r["close"]) else r.get("mid", math.nan)
        if math.isnan(c) or c <= 0:
            continue
        b = out.get(t0)
        if b is None:
            b = out[t0] = {"t": t0, "o": r["open"], "h": r["high"], "l": r["low"], "c": c, "buy": 0.0, "sell": 0.0,
                           "liq_long": 0.0, "liq_short": 0.0, "oi": math.nan, "funding": math.nan,
                           "obi_sum": 0.0, "obi_n": 0}
        b["h"] = max(b["h"], r["high"]); b["l"] = min(b["l"], r["low"]); b["c"] = c
        for k_src, k_dst in (("buy_usdt", "buy"), ("sell_usdt", "sell")):
            if not math.isnan(r[k_src]):
                b[k_dst] += r[k_src] / c
        # 强平卖单 = 多单被爆；强平买单 = 空单被爆
        if not math.isnan(r["liq_sell_usdt"]):
            b["liq_long"] += r["liq_sell_usdt"] / c
        if not math.isnan(r["liq_buy_usdt"]):
            b["liq_short"] += r["liq_buy_usdt"] / c
        if not math.isnan(r["oi_ccy"]):
            b["oi"] = r["oi_ccy"]
        if not math.isnan(r["funding_rate"]):
            b["funding"] = r["funding_rate"]
        if not math.isnan(r["imbalance"]):
            b["obi_sum"] += r["imbalance"]; b["obi_n"] += 1
    for b in out.values():
        b["obi"] = b["obi_sum"] / b["obi_n"] if b["obi_n"] else math.nan
    return out


def env_dir() -> str | None:
    return os.environ.get("OF_V7_DATA")
