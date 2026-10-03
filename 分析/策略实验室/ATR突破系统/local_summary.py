import pickle, numpy as np, pandas as pd, atrsys as A
res, info = pickle.load(open("grid_local.pkl", "rb"))
BEST = (55, 1.5, 3, 25)
fx = pd.concat([v[BEST] for v in res.values() if len(v[BEST])], ignore_index=True)
fx["t_in"] = pd.to_datetime(fx.t_in)
old = {s for s, (a, b, n) in info.items() if a < pd.Timestamp("2022-01-01")}
print(f"固定参数 {fx.sym.nunique()} 个币:", A.summarize(fx))
fc = fx[fx.fund != 0]
print("  有资金费数据的交易平均资金费 R:", round((fc.fund / fc.stop_pct).mean(), 3), "（2024-09 以后的币安数据没有资金费，按 0 算，偏乐观这么多）")
print("  老币(2021 就有):", A.summarize(fx[fx.sym.isin(old)]), "  新币(2022 以后上的):", A.summarize(fx[~fx.sym.isin(old)]))
for y, g in fx.groupby(fx.t_in.dt.year): print("  ", y, A.summarize(g))
print("  最近12个月(2025-09~2026-08)", A.summarize(fx[fx.t_in >= "2025-09-01"]))
pc = fx.groupby("sym").apply(lambda g: (g.net[g.net > 0].sum() / max(-g.net[g.net < 0].sum(), 1e-9), len(g)))
pf = pd.Series({k: v[0] for k, v in pc.items()}); n = pd.Series({k: v[1] for k, v in pc.items()})
big = pf[n >= 20]
print("  至少 20 笔的币:", len(big), "个，赚钱的", (big > 1).sum(), f"({(big>1).mean()*100:.0f}%)", "PF中位", round(big.median(), 2))
# 盈利是否集中在少数交易
s = fx.sort_values("R", ascending=False)
print("  去掉最赚的 1% 交易后:", A.summarize(s.iloc[int(len(s) * 0.01):]))
rows = []
for cfg in res[next(iter(res))]:
    t = pd.concat([v[cfg] for v in res.values() if len(v[cfg])])
    rows.append(dict(参数=cfg, **A.summarize(t)))
print(pd.DataFrame(rows).sort_values("PF").to_string())
