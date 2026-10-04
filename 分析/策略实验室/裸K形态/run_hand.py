import sys, numpy as np, pandas as pd, nk
from concurrent.futures import ProcessPoolExecutor
tf = sys.argv[1]


def one(sym):
    df = nk.load(sym, tf)
    if len(df) < 2000 // (4 if tf == "4h" else 1):
        return {}
    out = {}
    for name, (L, S) in nk.patterns(df).items():
        for side, sig in ((1, L), (-1, S)):
            R, t = nk.simulate(df, np.flatnonzero(sig), side)
            out[(name, side)] = (R, t)
    return out


if __name__ == "__main__":
    cs = nk.coins()
    agg = {}
    with ProcessPoolExecutor(4) as ex:
        for res in ex.map(one, cs, chunksize=4):
            for k, (R, t) in res.items():
                a = agg.setdefault(k, [[], []]); a[0].append(R); a[1].append(t)
    rows = []
    for (name, side), (Rs, ts) in agg.items():
        R = np.concatenate(Rs); t = pd.to_datetime(np.concatenate(ts))
        tr, te = R[t < nk.SPLIT], R[t >= nk.SPLIT]
        a, b = nk.stats(tr), nk.stats(te)
        rows.append(dict(形态=name, 方向="多" if side > 0 else "空", 训练笔数=a.get("笔数"), 训练胜率=a.get("胜率"), 训练平均R=a.get("平均R"), 训练PF=a.get("PF"),
                         检验笔数=b.get("笔数"), 检验胜率=b.get("胜率"), 检验平均R=b.get("平均R"), 检验PF=b.get("PF")))
    d = pd.DataFrame(rows).sort_values(["形态", "方向"])
    pd.set_option("display.width", 250); print(f"周期 {tf}，{len(cs)} 个币"); print(d.to_string(index=False))
    d.to_csv(f"hand_{tf}.csv", index=False)
