import numpy as np, pandas as pd, intraday as I
btc = I.load("BTC").close; b5 = btc.pct_change(5)
res = []
for H in (5, 15, 30):
    rows = []
    for s in I.SYMS:
        if s == "BTC": continue
        c = I.load(s).close.reindex(btc.index).ffill()
        a5 = c.pct_change(5); fwd = c.shift(-H) / c.shift(-1) - 1          # 下一分钟进（不偷看）
        for side in (1, -1):
            m = (side * b5 >= 0.01) & (side * a5 < 0.3 * side * b5)
            idx = np.flatnonzero(m.values); last = -10**9; keep = []
            for i in idx:
                if i - last >= H: keep.append(i); last = i
            r = side * fwd.values[keep]; t = c.index[keep]
            rows += [(tt, rr) for tt, rr in zip(t, r) if np.isfinite(rr)]
    res += I.summ(rows, f"1 BTC带动 拿{H}分")
print(pd.DataFrame(res).to_string(index=False))
