import numpy as np, nk
from concurrent.futures import ProcessPoolExecutor
nk.FEE = 0.0


def one(sym):
    nk.FEE = 0.0
    df = nk.load(sym, "4h")
    if len(df) < 500:
        return {}
    out = {}
    for name, (L, S) in nk.patterns(df).items():
        for side, sig in ((1, L), (-1, S)):
            out[(name, side)] = nk.simulate(df, np.flatnonzero(sig), side)[0]
    return out


if __name__ == "__main__":
    agg = {}
    with ProcessPoolExecutor(4) as ex:
        for res in ex.map(one, nk.coins(), chunksize=4):
            for k, R in res.items():
                agg.setdefault(k, []).append(R)
    print("4 小时、不扣任何费用的平均 R（2021-10~2026-08 全部）：")
    for (n, s), v in sorted(agg.items()):
        print(f"  {n} {'多' if s > 0 else '空'}: {np.concatenate(v).mean():+.3f}")
