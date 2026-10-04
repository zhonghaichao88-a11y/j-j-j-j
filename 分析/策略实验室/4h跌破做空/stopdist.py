import numpy as np, pandas as pd, bd, nk, smc_full as S
from concurrent.futures import ProcessPoolExecutor
def one(sym):
    df = nk.load(sym, "4h")
    if len(df) < 600: return []
    ev, _ = S.structure(df, 5); o, h = df.open.values, df.high.values; out = []; busy = -1
    for (t, side, k, leg, sw) in ev:
        if side != -1 or t <= busy: continue
        r = S.sim(df, t, -1, leg, "MKT", 2.0)
        if r:
            busy = r[1]; e = o[t + 1]; out.append(((h[leg] - e) / e * 100, (r[1] - r[0] + 1) * 4))
    return out
if __name__ == "__main__":
    with ProcessPoolExecutor(4) as ex: res = sum(ex.map(one, nk.coins(), chunksize=4), [])
    d = pd.DataFrame(res, columns=["stop", "hours"])
    q = d.stop.quantile([.1, .25, .5, .75, .9]).round(1).to_dict(); print("止损距离%（币价）分位:", q)
    print("止盈距离% = 2倍:", {k: round(v * 2, 1) for k, v in q.items()})
    print("持仓小时 中位/75%:", d.hours.median(), d.hours.quantile(.75))
