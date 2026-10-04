"""SMC 全套打法机械化检验（全部不偷看未来：波段点要等后面 L 根K线走完才确认）。
结构：收盘突破最近已确认波段高点 → 看涨突破；原来是跌势则叫 CHoCH（反转），否则 BOS（顺势）。做空镜像。
进场方式：
  1 BOS→回踩FVG  2 BOS→回踩OB  3 CHoCH→回踩FVG  4 CHoCH→回踩OB
  5 扫流动性+CHoCH→收盘后市价进  6 扫流动性+CHoCH→回踩FVG  7 =1+大周期同向  8 =2+大周期同向
FVG：这段推动里最后一个缺口，挂缺口上沿（多）；OB：推动起点附近最后一根反向K线，挂它的高点（多）。
扫流动性：CHoCH 前 20 根内有K线刺破前一个已确认波段低点、收盘收回。
止损：推动起点（OB 方式取 OB 低点和起点里更低的）；止盈 RR×风险；成交后 48 根超时收盘平。
突破后 24 根内没回踩到就放弃；碰到挂单价就算成交，同一根K线又打到止损按亏损算（不丢弃）。
费用：限价进场 0.02%，市价进场 0.07%；止盈 0.02%；止损/超时 0.07%（含滑点）。"""
import sys, glob, os, numpy as np, pandas as pd
sys.path.insert(0, "../裸K形态")
import nk
W, HOLD, SWEEP_LB = 24, 48, 20
MAKER, TAKER = 0.0002, 0.0007
RRS = (1.5, 2.0, 3.0)
HTF = {"15m": "1h", "1h": "4h", "4h": "1D"}


def resample(df, rule):
    return df.resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


def pivots(h, l, L):
    n = len(h); hi, lo = [], []
    from numpy.lib.stride_tricks import sliding_window_view as swv
    if n < 2 * L + 1:
        return hi, lo
    mh = swv(h, 2 * L + 1).max(1); ml = swv(l, 2 * L + 1).min(1)
    for i in range(L, n - L):
        if h[i] == mh[i - L]:
            hi.append((i + L, i, h[i]))
        if l[i] == ml[i - L]:
            lo.append((i + L, i, l[i]))
    return hi, lo


def structure(df, L):
    """逐根走一遍，返回 (事件列表, 每根的趋势)。事件：(t, 方向, 类型, 推动起点位置, 最近20根是否扫过流动性)"""
    h, l, c = df.high.values, df.low.values, df.close.values
    n = len(c); hi, lo = pivots(h, l, L)
    ih = il = 0; last_h = last_l = None; broken_h = broken_l = None
    trend = 0; tr = np.zeros(n, dtype=int); ev = []
    swept_lo_t = swept_hi_t = -10 ** 9
    for t in range(n):
        while ih < len(hi) and hi[ih][0] <= t:
            last_h = hi[ih]; ih += 1
        while il < len(lo) and lo[il][0] <= t:
            last_l = lo[il]; il += 1
        if last_l is not None and l[t] < last_l[2] and c[t] > last_l[2]:
            swept_lo_t = t
        if last_h is not None and h[t] > last_h[2] and c[t] < last_h[2]:
            swept_hi_t = t
        if last_h is not None and broken_h != last_h[1] and c[t] > last_h[2]:
            broken_h = last_h[1]
            kind = "CHOCH" if trend == -1 else "BOS"
            leg = last_h[1] + int(np.argmin(l[last_h[1]:t + 1]))
            ev.append((t, 1, kind, leg, t - swept_lo_t <= SWEEP_LB)); trend = 1
        elif last_l is not None and broken_l != last_l[1] and c[t] < last_l[2]:
            broken_l = last_l[1]
            kind = "CHOCH" if trend == 1 else "BOS"
            leg = last_l[1] + int(np.argmax(h[last_l[1]:t + 1]))
            ev.append((t, -1, kind, leg, t - swept_hi_t <= SWEEP_LB)); trend = -1
        tr[t] = trend
    return ev, tr


def zone(df, t, side, leg, how):
    o, h, l, c = df.open.values, df.high.values, df.low.values, df.close.values
    if how == "FVG":
        for j in range(t, leg + 1, -1):
            if side > 0 and l[j] > h[j - 2]:
                return l[j], None
            if side < 0 and h[j] < l[j - 2]:
                return h[j], None
        return None
    if how == "OB":
        for j in range(min(leg + 2, t - 1), max(leg - 6, 0), -1):
            if side > 0 and c[j] < o[j]:
                return h[j], l[j]
            if side < 0 and c[j] > o[j]:
                return l[j], h[j]
        return None


def sim(df, t, side, leg, how, rr):
    o, h, l, c = df.open.values, df.high.values, df.low.values, df.close.values
    n = len(c)
    if t + W + HOLD + 2 >= n:
        return None
    stop = l[leg] if side > 0 else h[leg]
    if how == "MKT":
        f, entry, fee_in = t + 1, o[t + 1], TAKER
    else:
        z = zone(df, t, side, leg, how)
        if z is None:
            return None
        edge, ob_ext = z
        if ob_ext is not None:
            stop = min(stop, ob_ext) if side > 0 else max(stop, ob_ext)
        if (edge - stop) * side <= 0:
            return None
        f = None
        for k in range(t + 1, t + 1 + W):
            # 碰到挂单价就算成交（止损在挂单价外侧，打到止损之前一定先成交）；同一根又打到止损，下面出场循环会按亏损算
            if (side > 0 and l[k] <= edge) or (side < 0 and h[k] >= edge):
                f = k; entry = min(o[k], edge) if side > 0 else max(o[k], edge); break
        if f is None:
            return None
        fee_in = MAKER
    risk = (entry - stop) * side
    if risk <= 0 or not (0.002 < risk / entry < 0.1):
        return None
    tp = entry + side * rr * risk
    for g in range(f, f + HOLD):
        if (side > 0 and l[g] <= stop) or (side < 0 and h[g] >= stop):
            px, fee_out = stop, TAKER; break
        if g > f and ((side > 0 and h[g] >= tp) or (side < 0 and l[g] <= tp)):
            px, fee_out = tp, MAKER; break
    else:
        g = f + HOLD - 1; px, fee_out = c[g], TAKER
    R = ((px - entry) * side / entry - fee_in - fee_out) / (risk / entry)
    return f, g, R


VARIANTS = {1: ("BOS", "FVG", False, False), 2: ("BOS", "OB", False, False), 3: ("CHOCH", "FVG", False, False),
            4: ("CHOCH", "OB", False, False), 5: ("CHOCH", "MKT", True, False), 6: ("CHOCH", "FVG", True, False),
            7: ("BOS", "FVG", False, True), 8: ("BOS", "OB", False, True)}


def run_coin(df, tf, L):
    ev, _ = structure(df, L)
    htf = resample(df, HTF[tf]); _, htr = structure(htf, L)
    # 大周期趋势：只用已收盘的大周期K线（标签是开盘时间 → 收盘时间=下一根开盘）
    hs = pd.Series(htr, index=htf.index).shift(1).reindex(df.index, method="ffill").fillna(0).values
    out = []
    for v, (kind, how, need_sweep, need_htf) in VARIANTS.items():
        for rr in RRS:
            busy = {1: -1, -1: -1}
            for (t, side, k, leg, swept) in ev:
                if k != kind or t <= busy[side] or (need_sweep and not swept) or (need_htf and hs[t] != side):
                    continue
                r = sim(df, t, side, leg, how, rr)
                if r is None:
                    continue
                f, g, R = r; busy[side] = g
                out.append((v, L, rr, side, df.index[f], R))
    return out


def load_tf(sym, tf):
    if tf == "15m":
        d = np.load(f"/home/user/okx_data/{sym}-USDT-SWAP_1m2y.npz")
        df = pd.DataFrame({k: d[k] for k in ("open", "high", "low", "close")}, index=pd.to_datetime(d["ts"], unit="ms"))
        df = df[~df.index.duplicated()].sort_index()
        return resample(df, "15min")
    return nk.load(sym, tf)


def job(args):
    sym, tf = args
    try:
        df = load_tf(sym, tf)
    except Exception:
        return []
    if len(df) < 1500:
        return []
    res = []
    for L in (5, 10):
        res += run_coin(df, tf, L)
    return res


if __name__ == "__main__":
    from concurrent.futures import ProcessPoolExecutor
    tf = sys.argv[1]
    if tf == "15m":
        syms = sorted(os.path.basename(f).split("-USDT")[0] for f in glob.glob("/home/user/okx_data/*-USDT-SWAP_1m2y.npz"))
        split = pd.Timestamp("2025-09-30")
    else:
        syms = nk.coins(); split = nk.SPLIT
    with ProcessPoolExecutor(4) as ex:
        res = sum(ex.map(job, [(s, tf) for s in syms], chunksize=2), [])
    d = pd.DataFrame(res, columns=["v", "L", "rr", "side", "t", "R"])
    d["seg"] = np.where(d.t < split, "训练", "检验")
    def pf(x):
        g, b = x[x > 0].sum(), -x[x < 0].sum(); return round(g / b, 2) if b else np.nan
    agg = d.groupby(["v", "L", "rr", "side", "seg"]).R.agg(n="size", win=lambda x: round((x > 0).mean() * 100), avgR=lambda x: round(x.mean(), 3), PF=pf).reset_index()
    wide = agg.pivot_table(index=["v", "L", "rr", "side"], columns="seg", values=["n", "win", "avgR", "PF"]).reset_index()
    wide.columns = [a if not b else f"{b}{a}" for a, b in wide.columns]
    names = {1: "BOS→FVG", 2: "BOS→OB", 3: "CHoCH→FVG", 4: "CHoCH→OB", 5: "扫流动性+CHoCH市价", 6: "扫流动性+CHoCH→FVG", 7: "BOS→FVG+大周期", 8: "BOS→OB+大周期"}
    wide["打法"] = wide.v.map(names); wide["方向"] = np.where(wide.side > 0, "多", "空")
    wide.to_csv(f"smc_full_{tf}.csv", index=False)
    cols = ["打法", "L", "rr", "方向", "训练n", "训练win", "训练avgR", "训练PF", "检验n", "检验win", "检验avgR", "检验PF"]
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 200)
    print(f"周期 {tf}：{len(syms)} 个币，{len(wide)} 种组合")
    print("训练段 PF>1 的组合数:", int((wide["训练PF"] > 1).sum()), "；训练 PF>1.15 且≥300 笔:", int(((wide["训练PF"] > 1.15) & (wide["训练n"] >= 300)).sum()))
    sel = wide[(wide["训练PF"] > 1.15) & (wide["训练n"] >= 300)]
    print("训练段过关的在检验段：" if len(sel) else "训练段过关的：一个都没有"); print(sel[cols].to_string(index=False) if len(sel) else "")
    print("\n各打法汇总（所有 L/RR 合起来）：")
    s = d.groupby(["v", "side", "seg"]).R.agg(n="size", PF=pf).unstack("seg")
    s.index = [f"{names[v]} {'多' if sd > 0 else '空'}" for v, sd in s.index]; print(s.to_string())
