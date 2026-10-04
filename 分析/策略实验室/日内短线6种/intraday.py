"""日内短线 6 种机械化检验（规则先定死）。数据：欧易永续 40 币 1 分钟K线 2024-09~2026-09。
训练 2024-10-01~2025-09-30，检验 2025-10-01~2026-09-29。费用：市价每边 0.07%（来回 0.14%）。
1 BTC 带动：BTC 5 分钟涨/跌 ≥1%，山寨同 5 分钟涨跌不到 BTC 的 30% → 同方向进，拿 5/15/30 分钟
2 波动突破：UTC 日开盘价 ± 0.5×昨日振幅 被突破 → 追，UTC 收盘平
3 开盘区间突破：UTC 00:00~00:30、13:30~14:00 区间，之后突破 → 进，止损区间另一边，止盈 2R，4 小时内收盘平
4 时间段：训练段每个 UTC 小时平均收益最好/最差的 2 个小时 → 检验段在这些小时开头做多/空，拿 1 小时
5 急跌反弹：15 分钟跌 ≥ 3×（过去 1 天 15 分钟波动）→ 买，拿 60 分钟或 +1% 止盈
6 资金费结算：00/08/16 UTC 前 30 分钟进、结算后 30 分钟出，方向 = 上一期资金费为正做空、为负做多（本地无逐币资金费 → 用 30 分钟前价格动量代替方向，见代码）"""
import glob, os, numpy as np, pandas as pd
D = "/home/user/okx_data"; FEE = 0.0007
TR0, SPLIT, TE1 = pd.Timestamp("2024-10-01"), pd.Timestamp("2025-10-01"), pd.Timestamp("2026-09-29")


def load(sym):
    d = np.load(f"{D}/{sym}-USDT-SWAP_1m2y.npz")
    df = pd.DataFrame({k: d[k] for k in ("open", "high", "low", "close")}, index=pd.to_datetime(d["ts"], unit="ms"))
    return df[~df.index.duplicated()].sort_index()


SYMS = sorted(os.path.basename(f).split("-USDT")[0] for f in glob.glob(f"{D}/*-USDT-SWAP_1m2y.npz"))


def summ(rows, name):
    d = pd.DataFrame(rows, columns=["t", "ret"])
    if d.empty:
        return [dict(玩法=name, 段=s, 笔数=0) for s in ("训练", "检验")]
    d["net"] = d.ret - 2 * FEE
    out = []
    for s, m in (("训练", (d.t >= TR0) & (d.t < SPLIT)), ("检验", (d.t >= SPLIT) & (d.t < TE1))):
        x = d.net[m]
        if len(x) == 0:
            out.append(dict(玩法=name, 段=s, 笔数=0)); continue
        g, b = x[x > 0].sum(), -x[x < 0].sum()
        out.append(dict(玩法=name, 段=s, 笔数=len(x), 每天笔数=round(len(x) / 365, 1), 胜率=f"{(x > 0).mean() * 100:.0f}%",
                        每笔毛收益=f"{d.ret[m].mean() * 100:+.3f}%", 每笔净收益=f"{x.mean() * 100:+.3f}%", PF=round(g / b, 2) if b else np.nan))
    return out
