"""4 小时"扫流动性+CHoCH 市价做空"的对照检验：
A 原组合（扫流动性 + CHoCH）  B 不要扫流动性的 CHoCH 做空  C 任何看跌结构突破（BOS 或 CHoCH）做空
D 随机做空：随机挑同样多的时间点，止损放在前 20 根最高点，其余规则一样（看是不是只是山寨币整体在跌）
都用 L=5、RR=2、市价进场、48 根超时。另看 A 的逐年、按币分布。"""
import numpy as np, pandas as pd, smc_full as S, nk
from concurrent.futures import ProcessPoolExecutor
RR = 2.0


def one(sym):
    df = nk.load(sym, "4h")
    if len(df) < 1500:
        return []
    ev, _ = S.structure(df, 5)
    out = []
    h = df.high.values
    rng = np.random.default_rng(abs(hash(sym)) % 2**32)
    for name, cond in (("A 扫流动性+CHoCH", lambda k, sw: k == "CHOCH" and sw), ("B CHoCH(不要扫)", lambda k, sw: k == "CHOCH"),
                       ("C 任何看跌突破", lambda k, sw: True)):
        busy = -1
        for (t, side, k, leg, sw) in ev:
            if side != -1 or t <= busy or not cond(k, sw):
                continue
            r = S.sim(df, t, -1, leg, "MKT", RR)
            if r:
                busy = r[1]; out.append((name, sym, df.index[r[0]], r[2]))
    nA = sum(1 for x in out if x[0].startswith("A"))
    for t in sorted(rng.choice(np.arange(30, len(df) - 80), size=min(max(nA, 1) * 3, len(df) - 120), replace=False)):
        leg = t - 20 + int(np.argmax(h[t - 20:t + 1]))
        r = S.sim(df, t, -1, leg, "MKT", RR)
        if r:
            out.append(("D 随机做空", sym, df.index[r[0]], r[2]))
    return out


if __name__ == "__main__":
    with ProcessPoolExecutor(4) as ex:
        res = sum(ex.map(one, nk.coins(), chunksize=2), [])
    d = pd.DataFrame(res, columns=["组", "币", "t", "R"]); d["段"] = np.where(d.t < nk.SPLIT, "训练", "检验"); d["年"] = d.t.dt.year
    pf = lambda x: round(x[x > 0].sum() / -x[x < 0].sum(), 2)
    print(d.groupby(["组", "段"]).R.agg(笔数="size", 胜率=lambda x: f"{(x>0).mean()*100:.0f}%", 平均R=lambda x: round(x.mean(), 3), PF=pf).to_string())
    print("\n各组逐年 PF："); print(d.groupby(["组", "年"]).R.apply(pf).unstack().to_string())
    a = d[d.组.str.startswith("A")]
    by = a.groupby("币").R.sum().sort_values()
    print(f"\nA 组：{a.币.nunique()} 个币，赚钱的币 {int((by>0).sum())} 个；去掉最赚的 5 个币后 PF {pf(a[~a.币.isin(by.tail(5).index)].R)}；合计 R {round(a.R.sum())}，最赚 5 币贡献 {round(by.tail(5).sum())}")
    d.to_csv("smc_check_4h.csv", index=False)
