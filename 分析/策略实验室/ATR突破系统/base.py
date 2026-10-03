import pandas as pd, atrsys as A, sys
coins = sys.argv[1].split(","); slip = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0002
allt = []
for c in coins:
    df = A.load15(c); ind = A.indicators(df, A.P0)
    t = A.trades(df, ind, A.funding(c), A.P0, slip); t["sym"] = c
    allt.append(t)
    print(c, A.summarize(t), flush=True)
tr = pd.concat(allt, ignore_index=True)
tr.to_csv(f"trades_base_{'_'.join(coins)}_{slip}.csv", index=False)
print("合计", A.summarize(tr))
tr["yr"] = tr.t_in.dt.year
for y, g in tr.groupby("yr"): print(" ", y, A.summarize(g))
print("出场原因", tr.groupby("why").agg(n=("R", "size"), R=("R", "mean")).round(2).to_dict())
curve, tt, halted = A.portfolio(tr, slip)
print("组合(不停机)", A.curve_stats(curve), "实际成交", len(tt), "12%回撤停机日", halted)
