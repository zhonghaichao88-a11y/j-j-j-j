import pickle, pandas as pd, numpy as np, atrsys as A
g1 = pickle.load(open("grid_BTC_ETH.pkl", "rb")); g2 = pickle.load(open("grid_SOL_BNB_XRP_DOGE_ADA_LINK.pkl", "rb"))
allg = {**g1, **g2}
cfgs = sorted({k[1:] for k in g2})
# 1) 只用 BTC+ETH 选参数（合并 PF 最高的 4h 组合）
tr_pf = {cfg: A.summarize(pd.concat([g1[("BTC",) + cfg], g1[("ETH",) + cfg]]))["PF"] for cfg in cfgs}
best = max(tr_pf, key=tr_pf.get)
print("BTC+ETH 选出的参数", best, "训练PF", tr_pf[best])
oos = pd.concat([g2[(c,) + best] for c in "SOL BNB XRP DOGE ADA LINK".split()])
print("6 个没碰过的币", A.summarize(oos))
all8 = pd.concat([allg[(c,) + best] for c in "BTC ETH SOL BNB XRP DOGE ADA LINK".split()])
all8["yr"] = pd.to_datetime(all8.t_in).dt.year
for y, g in all8.groupby("yr"): print("  ", y, A.summarize(g))
hold = all8[pd.to_datetime(all8.t_in) >= "2025-10-01"]
print("最近 12 个月（2025-10~2026-09）", A.summarize(hold))
print("按币", {c: A.summarize(g)["PF"] for c, g in all8.groupby("sym")})
# 2) 时间滚动：24 个月选参（8 币合并 PF 最高），用在后面 6 个月
starts = pd.date_range("2020-01-01", "2026-04-01", freq="6MS")
wf = []
for s in starts:
    tr0, tr1, te1 = s, s + pd.DateOffset(months=24), s + pd.DateOffset(months=30)
    if te1 > pd.Timestamp("2026-10-01"): break
    def seg(cfg, a, b):
        t = pd.concat([allg[(c,) + cfg] for c in "BTC ETH SOL BNB XRP DOGE ADA LINK".split()])
        ti = pd.to_datetime(t.t_in); return t[(ti >= a) & (ti < b)]
    pick = max(cfgs, key=lambda cfg: A.summarize(seg(cfg, tr0, tr1)).get("PF", 0))
    t = seg(pick, tr1, te1); wf.append(t)
    print("滚动", tr1.date(), "~", te1.date(), "选", pick, A.summarize(t))
wf = pd.concat(wf); print("滚动样本外合计", A.summarize(wf))
# 3) 组合层（文档风控），8 币，选出的参数
for slip in (0.0001, 0.0002, 0.0005):
    if slip == 0.0002:
        t = all8.copy()
    else:
        ts = []
        for c in "BTC ETH SOL BNB XRP DOGE ADA LINK".split():
            p = dict(A.P0, tf=240, htf=1440, dc=best[1], sl=best[2], trail=best[3], adx_min=best[4], atr_lo=0.0015 * (240 / 15) ** 0.5, atr_hi=0.03 * (240 / 15) ** 0.5)
            df = A.load15(c, 240); x = A.trades(df, A.indicators(df, p), A.funding(c), p, slip); x["sym"] = c; ts.append(x)
        t = pd.concat(ts)
    t["t_in"] = pd.to_datetime(t.t_in); t["t_out"] = pd.to_datetime(t.t_out)
    curve, tt, halted = A.portfolio(t, slip)
    print(f"滑点 {slip*100:.2f}%:", A.summarize(t), "组合", A.curve_stats(curve), "12%回撤停机日", halted)
    if slip == 0.0002:
        curve.to_csv("curve_4h.csv"); tt.to_csv("trades_4h_portfolio.csv", index=False)
        cy = curve.groupby(curve.index.year).last(); prev = 1.0
        for y, v in cy.items(): print("   ", y, f"{(v/prev-1)*100:+.1f}%"); prev = v
