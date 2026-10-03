"""参数网格：周期 15m/1h/4h × 通道 20/55 × 止损 1.5/2.5ATR × 跟踪 2/3ATR × ADX 20/25。每个组合存交易，给后面滚动检验用。"""
import itertools, sys, os, pickle, numpy as np, pandas as pd, atrsys as A
from concurrent.futures import ProcessPoolExecutor
coins = sys.argv[1].split(","); SLIP = 0.0002
TFS = {15: 60, 60: 240, 240: 1440}
if os.environ.get("ONLY4H"): TFS = {240: 1440}


def job(args):
    c, tf, dc, sl, tr_, adx = args
    p = dict(A.P0, tf=tf, htf=TFS[tf], dc=dc, sl=sl, trail=tr_, adx_min=adx)
    k = (tf / 15) ** 0.5
    p["atr_lo"], p["atr_hi"] = 0.0015 * k, 0.03 * k          # ATR% 上下限按周期放大
    df = A.load15(c, tf); ind = A.indicators(df, p)
    t = A.trades(df, ind, A.funding(c), p, SLIP); t["sym"] = c
    return (c, tf, dc, sl, tr_, adx), t


if __name__ == "__main__":
    combos = [(c, tf, dc, sl, tr_, adx) for c in coins for tf in TFS for dc in (20, 55) for sl in (1.5, 2.5) for tr_ in (2, 3) for adx in (20, 25)]
    res = {}
    with ProcessPoolExecutor(4) as ex:
        for key, t in ex.map(job, combos):
            res[key] = t
            s = A.summarize(t); print(key, s, flush=True)
    pickle.dump(res, open(f"grid_{'_'.join(coins)}.pkl", "wb"))
