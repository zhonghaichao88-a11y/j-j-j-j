import pandas as pd, trend
px, fund = trend.load()
P = [("2020-03-01", "2021-12-31", "20-21牛"), ("2022-01-01", "2022-12-31", "22熊"), ("2023-01-01", "2024-12-31", "23-24"),
     ("2025-01-01", "2026-09-30", "25-26"), (None, None, "全部")]
cfgs = [("donch", 20, 10), ("donch", 50, 25), ("donch", 100, 50), ("sma", 50, None), ("sma", 100, None), ("sma", 200, None),
        ("tsmom", 30, None), ("tsmom", 90, None), ("tsmom", 180, None)]
rows = []
def add(name, pnl):
    for a, b, lab in P:
        s = trend.stats(pnl, a, b); rows.append(dict(策略=name, 时段=lab, **s))
for univ, coins in (("BTC", ["BTC"]), ("ETH", ["ETH"]), ("79币", None)):
    add(f"{univ} 拿着不动", trend.backtest(px, fund, "hold", coins=coins, vol_target=False, max_lev=1)[0])
    for k, n, m in cfgs:
        for ls in (False, True):
            nm = f"{univ} {k}{n}{'/'+str(m) if m else ''} {'多空' if ls else '只多'}"
            add(nm, trend.backtest(px, fund, k, n, m, ls, coins=coins)[0])
    print(univ, "done", flush=True)
df = pd.DataFrame(rows)
df.to_csv("results.csv", index=False)
piv = df.pivot_table(index="策略", columns="时段", values="年化", aggfunc="first", sort=False)[["20-21牛", "22熊", "23-24", "25-26", "全部"]]
dd = df[df.时段 == "全部"].set_index("策略")[["最大回撤", "夏普"]]
out = piv.join(dd)
pd.set_option("display.width", 250); pd.set_option("display.max_rows", 200)
print(out.to_string())
out.to_csv("summary.csv")
