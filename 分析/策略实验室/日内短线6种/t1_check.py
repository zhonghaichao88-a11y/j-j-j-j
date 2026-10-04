import numpy as np, pandas as pd, intraday as I
btc = I.load("BTC").close; b5 = btc.pct_change(5); H = 15; rows = []
for s in I.SYMS:
    if s == "BTC": continue
    raw = I.load(s).close
    c = raw.reindex(btc.index); stale = c.isna().rolling(10).sum()        # 近 10 分钟缺了几根
    c = c.ffill(); a5 = c.pct_change(5); fwd = c.shift(-H) / c.shift(-1) - 1
    for side in (1, -1):
        m = (side * b5 >= 0.01) & (side * a5 < 0.3 * side * b5)
        for i in np.flatnonzero(m.values):
            r = side * fwd.values[i]
            if np.isfinite(r): rows.append((c.index[i], s, side, r - 2 * I.FEE, stale.values[i]))
d = pd.DataFrame(rows, columns=["t", "sym", "side", "net", "stale"]).drop_duplicates(["t", "sym"])
te = d[d.t >= I.SPLIT]
print("检验段中位净收益 %.3f%%，有缺数据的信号 %d 个" % (te.net.median() * 100, (te.stale > 0).sum()))
by = te.groupby(te.t.dt.date).net.agg(["size", "sum"]).sort_values("sum")
print("检验段按天：最赚的 5 天", by.tail(5).round(3).to_dict("index"))
print("去掉最赚的 3 天后 平均净收益 %.3f%%" % (te[~te.t.dt.date.isin(by.tail(3).index)].net.mean() * 100))
