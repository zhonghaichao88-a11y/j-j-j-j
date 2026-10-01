"""把 V7 录的数据压小，方便发给 Claude 分析。
只留回测要用的列，可以只留指定的币；按天压成 .csv.gz，每个文件超过 25MB 自动拆开。
输出到本文件夹的 “发给Claude” 文件夹。只有行情数据，没有密钥和账户信息。
用法：双击 “导出V7数据.bat”；或者 python export_v7.py [V7文件夹] [币,币 或 all] [最近几天]"""
from __future__ import annotations

import csv
import gzip
import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import of_v7data  # noqa: E402

KEEP = ["ts", "inst", "open", "high", "low", "close", "buy_usdt", "sell_usdt", "trades", "imbalance",
        "spread_bps", "bid5_usdt", "ask5_usdt", "funding_rate", "oi_ccy", "liq_buy_usdt", "liq_sell_usdt"]
LIMIT = 25 * 1024 * 1024
OUT = Path(__file__).resolve().parent / "发给Claude"


def main():
    hint = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("OF_V7_DATA") or input("V7 文件夹（拖进来回车，放在旁边可直接回车）: ").strip().strip('"')
    d = of_v7data.find_dir(hint or None)
    if d is None:
        sys.exit("没找到 V7 的 recorder_data 文件夹")
    want = sys.argv[2] if len(sys.argv) > 2 else (input("只要哪些币？例如 BTC,ETH,SOL（直接回车 = 成交额最大的 10 个）: ").strip() or "top10")
    days = int(sys.argv[3]) if len(sys.argv) > 3 else int(input("最近几天？（直接回车 = 全部）: ").strip() or 9999)
    files = sorted(list(d.glob("*.csv")) + list(d.glob("*.csv.gz")), key=lambda p: p.name.split(".")[0])[-days:]
    if not files:
        sys.exit("recorder_data 里没有数据文件")
    if want == "top10":
        insts = set(of_v7data.top_symbols(of_v7data.load(d, days=1), 10))
    elif want.lower() == "all":
        insts = None
    else:
        insts = {(w.strip().upper() + "-USDT-SWAP") if "-" not in w else w.strip().upper() for w in want.split(",") if w.strip()}
    print("币：", "全部" if insts is None else ", ".join(sorted(insts)))
    OUT.mkdir(exist_ok=True)
    total = 0
    for f in files:
        day = f.name.split(".")[0]
        op = gzip.open if f.suffix == ".gz" else open
        part, buf, n = 1, None, 0

        def new_part(k):
            path = OUT / (f"{day}.csv.gz" if k == 1 else f"{day}_{k}.csv.gz")
            fh = gzip.open(path, "wt", encoding="utf-8", newline="")
            w = csv.DictWriter(fh, fieldnames=KEEP, extrasaction="ignore")
            w.writeheader()
            return path, fh, w

        path, fh, w = new_part(part)
        with op(f, "rt", encoding="utf-8", newline="") as src:
            for r in csv.DictReader(src):
                if insts is not None and r.get("inst") not in insts:
                    continue
                w.writerow(r)
                n += 1
                if n % 20000 == 0:
                    fh.flush()
                    if path.stat().st_size > LIMIT:
                        fh.close(); part += 1
                        path, fh, w = new_part(part)
        fh.close()
        size = sum(p.stat().st_size for p in OUT.glob(f"{day}*.csv.gz"))
        total += size
        print(f"{day}: {n} 行 → {size/1024/1024:.1f} MB")
    print(f"完成，共 {total/1024/1024:.1f} MB，在：{OUT}")
    print("把 “发给Claude” 文件夹里的文件发给我就行（一次发不完可以分几次）。")


if __name__ == "__main__":
    main()
