"""TA-Lib 61 个K线形态（行业里最常用的现成代码）逐个检验。形态出现的那根收盘确认，下一根开盘进场，出场规则同 nk.py。
两种：任意位置 / 必须在 20 根低点（看涨）或高点（看跌）。只用训练段挑：PF>1.15 且 ≥300 笔，再看检验段。"""
import sys, numpy as np, pandas as pd, talib, nk
from concurrent.futures import ProcessPoolExecutor
tf = sys.argv[1]
CDL = [f for f in talib.get_functions() if f.startswith("CDL")]


def one(sym):
    df = nk.load(sym, tf)
    if len(df) < (2000 if tf == "1h" else 500):
        return {}
    o, h, l, c = (df[k].values.astype(float) for k in ("open", "high", "low", "close"))
    at_low = (df.low <= df.low.rolling(20).min()).values; at_high = (df.high >= df.high.rolling(20).max()).values
    out = {}
    for f in CDL:
        v = getattr(talib, f)(o, h, l, c)
        for side, base in ((1, v > 0), (-1, v < 0)):
            for ctx, m in (("任意位置", base), ("在高低点", base & (at_low if side > 0 else at_high))):
                idx = np.flatnonzero(m)
                if len(idx):
                    out[(f, side, ctx)] = nk.simulate(df, idx, side)
    return out


if __name__ == "__main__":
    agg = {}
    with ProcessPoolExecutor(4) as ex:
        for res in ex.map(one, nk.coins(), chunksize=4):
            for k, (R, t) in res.items():
                a = agg.setdefault(k, [[], []]); a[0].append(R); a[1].append(t)
    rows = []
    for (f, side, ctx), (Rs, ts) in agg.items():
        R = np.concatenate(Rs); t = pd.to_datetime(np.concatenate(ts))
        tr, te = R[t < nk.SPLIT], R[t >= nk.SPLIT]
        if len(tr) < 50:
            continue
        a, b = nk.stats(tr), nk.stats(te)
        rows.append(dict(形态=f, 方向="多" if side > 0 else "空", 位置=ctx, 训练笔数=len(tr), 训练PF=a["PF"], 训练平均R=a["平均R"],
                         检验笔数=len(te), 检验PF=b.get("PF"), 检验平均R=b.get("平均R")))
    d = pd.DataFrame(rows); d.to_csv(f"talib_{tf}.csv", index=False)
    pd.set_option("display.width", 250)
    print(f"周期 {tf}：测了 {len(d)} 种（形态×方向×位置）；训练段 PF>1 的 {int((d.训练PF > 1).sum())} 种，PF>1.15 的 {int((d.训练PF > 1.15).sum())} 种")
    sel = d[(d.训练PF > 1.15) & (d.训练笔数 >= 300)].sort_values("训练PF", ascending=False)
    print("训练段过关（PF>1.15 且≥300 笔）的，在检验段的表现："); print(sel.to_string(index=False) if len(sel) else "  一个都没有")
    print("\n训练段最好的 10 种（不管笔数）："); print(d.sort_values("训练PF", ascending=False).head(10).to_string(index=False))
