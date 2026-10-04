"""SMC（结构突破 BOS + FVG 回踩）机械化检验，对比两种波段点：
  causal=True  ：自己写的不偷看版本——波段高低点要等后面 L 根K线走完才确认，确认之前不能用（和实盘一样）
  causal=False ：直接用开源库 smartmoneyconcepts 的 swing_highs_lows（它用到了之后的K线，回测会偷看未来）
规则：收盘突破最近一个"已知"波段高点 = 看涨 BOS；之后 24 根内价格回到 BOS 那段上涨里最后一个看涨 FVG 的上沿 → 限价成交做多；
止损这段上涨的起点低点；止盈 2R；成交后 48 根没碰到就收盘平。做空镜像。每边费用 0.07%。"""
import sys, numpy as np, pandas as pd
sys.path.insert(0, "/home/user/joshyattridge/smart-money-concepts")
sys.path.insert(0, "../裸K形态")
import nk
from smartmoneyconcepts import smc
FEE, RR, W, HOLD = 0.0007, 2.0, 24, 48


def swings(df, L, causal):
    """返回 (高点列表, 低点列表)，每项 (可以开始使用的位置, 波段所在位置, 价格)"""
    h, l = df.high.values, df.low.values
    if causal:
        hi, lo = [], []
        for i in range(L, len(h) - L):
            if h[i] == h[i - L:i + L + 1].max():
                hi.append((i + L, i, h[i]))
            if l[i] == l[i - L:i + L + 1].min():
                lo.append((i + L, i, l[i]))
        return hi, lo
    s = smc.swing_highs_lows(df[["open", "high", "low", "close"]], swing_length=L)
    v = s["HighLow"].values; lv = s["Level"].values
    hi = [(i, i, lv[i]) for i in np.flatnonzero(v == 1)]           # 偷看：波段一出现就当作已知
    lo = [(i, i, lv[i]) for i in np.flatnonzero(v == -1)]
    return hi, lo


def run(df, L, causal):
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    n = len(c); hi, lo = swings(df, L, causal)
    out = []
    for side in (1, -1):
        piv = hi if side > 0 else lo
        k = 0; last = None; used = set(); t = 0; busy_until = 0
        known = []
        piv_sorted = sorted(piv)
        for t in range(n - HOLD - W - 2):
            while k < len(piv_sorted) and piv_sorted[k][0] <= t:
                known.append(piv_sorted[k]); k += 1
            if not known or t < busy_until:
                continue
            _, idx, lvl = known[-1]
            if idx in used:
                continue
            brk = c[t] > lvl if side > 0 else c[t] < lvl
            if not brk:
                continue
            used.add(idx)
            seg = slice(idx, t + 1)
            leg_ext = l[seg].min() if side > 0 else h[seg].max()          # 这段的起点（止损）
            zone = None
            for j in range(t, idx + 1, -1):                               # 这段里最后一个 FVG
                if side > 0 and l[j] > h[j - 2]:
                    zone = (h[j - 2], l[j]); break
                if side < 0 and h[j] < l[j - 2]:
                    zone = (h[j], l[j - 2]); break
            if zone is None:
                continue
            edge = zone[1] if side > 0 else zone[0]                       # 做多挂在 FVG 上沿，做空挂下沿
            stop = leg_ext
            risk = (edge - stop) * side
            if risk <= 0 or risk / edge < 0.003 or risk / edge > 0.1:
                continue
            fill = None
            for f in range(t + 1, t + 1 + W):
                if (side > 0 and l[f] <= edge) or (side < 0 and h[f] >= edge):
                    fill = (f, min(o[f], edge) if side > 0 else max(o[f], edge)); break
            if fill is None:
                continue
            f, entry = fill
            risk = (entry - stop) * side
            if risk <= 0:
                continue
            tp = entry + side * RR * risk
            res = None
            for g in range(f, f + HOLD):
                if (side > 0 and l[g] <= stop) or (side < 0 and h[g] >= stop):
                    res = stop; break
                if g > f and ((side > 0 and h[g] >= tp) or (side < 0 and l[g] <= tp)):
                    res = tp; break
            if res is None:
                res = c[f + HOLD - 1]; g = f + HOLD - 1
            R = ((res - entry) * side / entry - 2 * FEE) / (risk / entry)
            out.append((df.index[f], side, R)); busy_until = g + 1
    return out


def one(args):
    sym, tf, L, causal = args
    df = nk.load(sym, tf)
    if len(df) < 1000:
        return []
    return [(sym,) + x for x in run(df, L, causal)]


if __name__ == "__main__":
    from concurrent.futures import ProcessPoolExecutor
    tf = sys.argv[1]
    rows = []
    for L in (5, 10):
        for causal in (False, True):
            with ProcessPoolExecutor(4) as ex:
                res = sum(ex.map(one, [(s, tf, L, causal) for s in nk.coins()], chunksize=4), [])
            d = pd.DataFrame(res, columns=["sym", "t", "side", "R"])
            for per, m in (("训练2021-10~2024-06", d.t < nk.SPLIT), ("检验2024-07~2026-08", d.t >= nk.SPLIT)):
                for side in (1, -1):
                    x = d[m & (d.side == side)].R.values
                    if len(x):
                        g, b = x[x > 0].sum(), -x[x < 0].sum()
                        rows.append(dict(版本="不偷看(实盘一样)" if causal else "原样用开源库(偷看未来)", 波段长度=L, 时段=per, 方向="多" if side > 0 else "空",
                                         笔数=len(x), 胜率=f"{(x > 0).mean() * 100:.0f}%", 平均R=round(x.mean(), 3), PF=round(g / b, 2)))
            print(f"L={L} causal={causal} 完成", flush=True)
    out = pd.DataFrame(rows); out.to_csv(f"smc_{tf}.csv", index=False)
    pd.set_option("display.width", 250); print(out.to_string(index=False))
