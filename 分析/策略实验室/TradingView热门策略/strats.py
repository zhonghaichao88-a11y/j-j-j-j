"""TradingView 热门策略的 Python 移植（按原脚本默认参数；能照搬的照搬，外汇按点数设的止损止盈换成百分比，写在注释里）
每个函数输入 df(o h l c v)，返回 tvsim.run 的参数字典。注册表 REG: 名字 -> (函数, 周期分钟, TV 编号, 点赞)"""
import numpy as np, pandas as pd
from numba import njit
import pine as P

REG = {}


def reg(name, tf, tvid, likes):
    def d(f):
        REG[name] = (f, tf, tvid, likes)
        return f
    return d


B = lambda x: np.asarray(pd.Series(x).fillna(False), bool)


@reg('布林+RSI 双重(ChartArt)', 60, 3266789, 48602)
def s_bb_rsi_chartart(df):
    c = df.c; r = P.rsi(c, 6); b, u, l = P.bb(c, 200, 2)
    le = P.cross_over(r, 50) & P.cross_over(c, l)
    se = P.cross_under(r, 50) & P.cross_under(c, u)
    return dict(le=B(le), se=B(se))


@reg('AO+随机+RSI+ATR(Serdar)', 60, 6535128, 14248)
def s_ao_stoch(df):
    hl2 = (df.h + df.l) / 2
    ao = P.sma(hl2, 5) - P.sma(hl2, 34)
    k = P.sma(P.stoch(df.c, df.h, df.l, 14), 3)
    r = P.rsi(df.c, 10); a = P.rma(P.tr(df.h, df.l, df.c), 14)
    le = (k < 20) & (r < 30) & (ao > ao.shift())
    se = (k > 80) & (r > 70) & (ao < ao.shift())
    return dict(le=B(le), se=B(se), slp=((df.l - a).values, (df.h + a).values), tpp=((df.c + a).values, (df.c - a).values))


@reg('Flawless Victory 15分钟BTC', 15, 10033393, 11028)
def s_flawless(df):
    c = df.c; r = P.rsi(c, 14); b, u, l = P.bb(c, 20, 1.0)
    le = (c < l) & (r > 42)
    lx = (c > u) & (r > 70)
    return dict(le=B(le), lx=B(lx), long_only=True)


@njit(cache=True)
def _var(src, length):
    n = len(src); out = np.zeros(n); a = 2.0 / (length + 1)
    for i in range(n):
        ud = 0.0; dd = 0.0
        for j in range(max(1, i - 8), i + 1):
            d = src[j] - src[j - 1]
            if d > 0: ud += d
            else: dd -= d
        cmo = (ud - dd) / (ud + dd) if (ud + dd) != 0 else 0.0
        prev = out[i - 1] if i > 0 else 0.0
        out[i] = a * abs(cmo) * src[i] + (1 - a * abs(cmo)) * prev
    return out


@njit(cache=True)
def _ott(m, pct):
    n = len(m); ott = np.full(n, np.nan)
    ls_p = np.nan; ss_p = np.nan; d = 1
    for i in range(n):
        f = m[i] * pct * 0.01
        ls = m[i] - f; ss = m[i] + f
        lsp = ls if np.isnan(ls_p) else ls_p
        ssp = ss if np.isnan(ss_p) else ss_p
        ls = max(ls, lsp) if m[i] > lsp else ls
        ss = min(ss, ssp) if m[i] < ssp else ss
        if d == -1 and m[i] > ssp: d = 1
        elif d == 1 and m[i] < lsp: d = -1
        mt = ls if d == 1 else ss
        ott[i] = mt * (200 + pct) / 200 if m[i] > mt else mt * (200 - pct) / 200
        ls_p, ss_p = ls, ss
    return ott


@reg('TOTT 双优化趋势跟踪', 60, 9628832, 8738)
def s_tott(df):
    m = _var(df.c.values, 40); ott = _ott(m, 1.0)
    up = pd.Series(ott * 1.001).shift(2); dn = pd.Series(ott * 0.999).shift(2); m = pd.Series(m)
    return dict(le=B(P.cross_over(m, up)), se=B(P.cross_under(m, dn)))


@reg('MACD+随机 双重(ChartArt)', 60, 3292004, 7964)
def s_macd_stoch(df):
    _, _, h = P.macd(df.c, 12, 26, 9)
    k = P.sma(P.stoch(df.c, df.h, df.l, 14), 3); d = P.sma(k, 2)
    le = P.cross_over(k, d) & (k < 29) & P.cross_over(h, 0)
    se = P.cross_under(k, d) & (k > 71) & P.cross_under(h, 0)
    return dict(le=B(le), se=B(se))


@reg('Super Scalper 5/15分钟', 5, 12859580, 7141)
def s_super_scalper(df):
    a = P.wma(P.tr(df.h, df.l, df.c), 14)
    r1, r2 = P.rsi(df.c, 25), P.rsi(df.c, 100)
    le = (df.o < df.c - a) & (r1 > r2)
    se = (df.o > df.c + a) & (r1 < r2)
    return dict(le=B(le), se=B(se))          # 原脚本算了止损止盈但没用上，只靠反手


@reg('随机+RSI 双重(ChartArt)', 60, 3217902, 7010)
def s_stoch_rsi_chartart(df):
    k = P.sma(P.stoch(df.c, df.h, df.l, 14), 3); d = P.sma(k, 3); r = P.rsi(df.c, 14)
    le = P.cross_over(k, d) & (k < 20) & P.cross_over(r, 30)
    se = P.cross_under(k, d) & (k > 80) & P.cross_under(r, 70)
    return dict(le=B(le), se=B(se))


@njit(cache=True)
def _ppst(h, l, c, ph, pl, a, factor):
    n = len(c); trend = np.ones(n); center = np.nan; tup = np.nan; tdn = np.nan
    for i in range(n):
        lp = ph[i] if not np.isnan(ph[i]) else pl[i]
        if not np.isnan(lp):
            center = lp if np.isnan(center) else (center * 2 + lp) / 3
        up = center - factor * a[i]; dn = center + factor * a[i]
        tup_p, tdn_p = tup, tdn
        if i > 0 and not np.isnan(tup_p) and c[i - 1] > tup_p:
            tup = max(up, tup_p)
        else:
            tup = up
        if i > 0 and not np.isnan(tdn_p) and c[i - 1] < tdn_p:
            tdn = min(dn, tdn_p)
        else:
            tdn = dn
        if not np.isnan(tdn_p) and c[i] > tdn_p:
            trend[i] = 1
        elif not np.isnan(tup_p) and c[i] < tup_p:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1] if i > 0 else 1
    return trend


@reg('枢轴点超级趋势 Pivot Point SuperTrend', 60, 7537862, 5403)
def s_ppst(df):
    ph = P.pivothigh(df.h, 2, 2).values; pl = P.pivotlow(df.l, 2, 2).values
    a = P.atr(df.h, df.l, df.c, 10).values
    t = pd.Series(_ppst(df.h.values, df.l.values, df.c.values, ph, pl, a, 3.0))
    return dict(le=B((t == 1) & (t.shift() == -1)), se=B((t == -1) & (t.shift() == 1)))


@reg('布林带+RSI 15分钟(只做多)', 15, 9972638, 5074)
def s_bb_rsi15(df):
    r = P.rsi(df.c, 14); b, u, l = P.bb(df.c, 20, 2.0)
    return dict(le=B((r < 30) & (df.c < l)), lx=B(r > 70), sl=0.25, tp=0.10, long_only=True)


@reg('布林带突破(55,1倍)', 60, 8477059, 4273)
def s_bb_breakout(df):
    b, u, l = P.bb(df.c, 55, 1.0)
    L1 = P.barssince(df.c > u); S1 = P.barssince(df.c < l)
    x = (L1 < S1); y = (S1 < L1)
    return dict(le=B(x & ~x.shift().fillna(False).astype(bool)), se=B(y & ~y.shift().fillna(False).astype(bool)))


@reg('MicuRobert 零滞后EMA交叉', 60, 3207828, 4214)
def s_micu(df):
    def zl(s, n):
        e1 = P.ema(s, n); e2 = P.ema(e1, n); return e1 + (e1 - e2)
    p = df.o; m0, m1 = zl(p, 5), zl(p, 34)
    le = P.cross_over(m0, m1) | (P.cross_over(p, m0) & (m0 > m1))
    se = P.cross_under(m0, m1) | (P.cross_under(p, m0) & (m0 < m1))
    # 原版欧元兑美元：止盈 55 点 / 追踪止损 22 点 ≈ 0.5% / 0.2%；不限交易时段（币 24 小时）
    return dict(le=B(le), se=B(se), tp=0.005, trail=0.002)


@reg('RSI 对比均线(不重绘)', 5, 3366568, 4190)
def s_rsi_vs_sma(df):
    s = df.o
    r = P.rsi(s, 8) - 50; m = P.sma(r, 34)
    td = (m <= 0)
    return dict(le=B(td & ~td.shift().fillna(False).astype(bool)), se=B(~td & td.shift().fillna(False).astype(bool)))


@njit(cache=True)
def _wr_state(bc, dbc, sc, dsc):
    n = len(bc); ba = np.zeros(n, np.bool_); sa = np.zeros(n, np.bool_); b = False; s = False
    for i in range(n):
        if bc[i]: b = True
        if dbc[i]: b = False
        if sc[i]: s = True
        if dsc[i]: s = False
        ba[i] = b; sa[i] = s
    return ba, sa


@reg('威廉指标+MACD+均线 剥头皮(原1分钟)', 5, 17461838, 3780)
def s_wr_macd(df):
    hh, ll = P.highest(df.h, 140), P.lowest(df.l, 140)
    wr = (hh - df.c) / (hh - ll) * -100
    _, _, h = P.macd(df.c, 24, 52, 9); sm = P.sma(df.c, 7)
    ba, sa = _wr_state(B(P.cross_over(wr, -94) & (df.c > sm)), B(P.cross_over(wr, -40)),
                       B(P.cross_under(wr, -6) & (df.c < sm)), B(P.cross_under(wr, -60)))
    le = ba & B((h.shift() < 0) & (h > 0)); se = sa & B((h.shift() > 0) & (h < 0))
    return dict(le=le, se=se, lx=B(h < h.shift()), sx=B(h > h.shift()))


@njit(cache=True)
def _grab_trend(h, l, mr1, ms1):
    n = len(h); t = np.zeros(n); x = 0.0
    for i in range(n):
        if h[i] > mr1[i]: x = 1.0
        elif l[i] < ms1[i]: x = -1.0
        t[i] = x
    return t


@reg('Grab 交易系统(原2分钟)', 5, 8354181, 3660)
def s_grab(df):
    MR, MS = P.highest(df.h, 80), P.lowest(df.l, 80); mr, ms = P.highest(df.h, 21), P.lowest(df.l, 21)
    t = pd.Series(_grab_trend(df.h.values, df.l.values, MR.shift().values, MS.shift().values))
    bpat = (df.l.shift() == ms.shift()) & (df.l > ms)
    spat = (df.h.shift() == mr.shift()) & (df.h < mr)
    tch = t.diff().fillna(0) != 0
    return dict(le=B((t == 1) & bpat), se=B((t == -1) & spat), lx=B(spat | tch), sx=B(bpat | tch),
                slp=(MS.values, MR.values), dyn=True)


@reg('一目均衡 TK交叉+EMA200(只做多)', 30, 4373472, 3586)
def s_tk(df):
    dc = lambda n: (P.lowest(df.l, n) + P.highest(df.h, n)) / 2
    cl, bl = dc(20), dc(60); e = P.ema(df.c, 200)
    return dict(le=B((cl > bl) & (df.c > e)), lx=B(cl < bl), long_only=True)


@njit(cache=True)
def _rngfilt_vol(src, teth, rng):
    n = len(src); f = np.zeros(n)
    for i in range(n):
        prev = f[i - 1] if i > 0 else 0.0
        tp = teth[i - 1] if i > 0 else 0.0
        if np.isnan(rng[i]):
            f[i] = src[i]; continue
        if teth[i] > tp:
            f[i] = prev if (src[i] - rng[i]) < prev else (src[i] - rng[i])
        else:
            f[i] = prev if (src[i] + rng[i]) > prev else (src[i] + rng[i])
    return f


@njit(cache=True)
def _trend_up(x):
    n = len(x); u = np.zeros(n); v = 0.0
    for i in range(1, n):
        if x[i] > x[i - 1]: v += 1
        elif x[i] < x[i - 1]: v = 0.0
        u[i] = v
    return u


@reg('Powertrend 成交量区间过滤(只做多)', 60, 15394604, 3572)
def s_powertrend(df):
    # wbburgin_utils 库（已下载源码核对）：smoothrng = ema(ema(|x-x[1]|, t), 2t-1) * m；trendUp = 连续抬高计数（持平不清零）
    sr = P.ema(P.ema((df.c - df.c.shift()).abs(), 200), 399) * 3
    vr = pd.Series(_rngfilt_vol(df.c.values, df.v.values, sr.values))
    hb, lb = vr + sr.values, vr - sr.values
    up = pd.Series(_trend_up(vr.values) > 0)
    return dict(le=B(up & P.cross_over(df.c, hb)), lx=B(~up & P.cross_under(df.c, lb)), long_only=True)


@reg('剥头皮交易系统 Crypto&Stocks', 5, 11865235, 3417)
def s_scalp_sys(df):
    src = df.l
    out, out2 = P.sma(src, 25), P.ema(src, 200)
    ma = P.sma(src, 10); a = P.atr(df.h, df.l, df.c, 14); up, lo = ma + 2 * a, ma - 2 * a
    k = P.stoch(df.c, df.h, df.l, 10)
    m = P.ema(src, 4) - P.ema(src, 34); h = m - P.ema(m, 5)
    c = df.c
    le = (c > out) & (c < up) & (c > lo) & (h < 0) & (k < 50) & (c > out2)
    se = (c < out) & (c < up) & (c > lo) & (h > 0) & (k > 50) & (c < out2)
    return dict(le=B(le), se=B(se))


@reg('挤压动量 Squeeze Momentum(LazyBear)', 60, 5451724, 3407)
def s_sqz(df):
    c = df.c; b, ub, lb = P.bb(c, 14, 2.0)
    ma = P.sma(c, 16); rg = P.sma(P.tr(df.h, df.l, df.c), 16); uk, lk = ma + rg * 1.5, ma - rg * 1.5
    on = (lb > lk) & (ub < uk); off = (lb < lk) & (ub > uk)
    aqua = off                                   # scolor: noSqz=蓝, sqzOn=黑, 其它(=sqzOff)=浅蓝
    val = P.linreg(c - ((P.highest(df.h, 16) + P.lowest(df.l, 16)) / 2 + P.sma(c, 16)) / 2, 16, 0)
    vp = val.shift().fillna(0)
    lime = (val > 0) & (val > vp); green = (val > 0) & ~(val > vp)
    red = (val <= 0) & (val < vp); maroon = (val <= 0) & ~(val < vp)
    first = aqua & ~aqua.shift().fillna(False).astype(bool)
    return dict(le=B(first & lime), se=B(first & red), lx=B(green), sx=B(maroon))


@reg('Forex Master v4 均值回归', 5, 3384531, 3350)
def s_fxmaster(df):
    c = df.c; b, u, l = P.bb(c, 20, 1.5)
    h, lo = df.h, df.l
    trr = P.tr(h, lo, c)
    up, dn = h.diff(), -lo.diff()
    pdm = pd.Series(np.where(up > dn, np.maximum(up, 0), 0.0)); mdm = pd.Series(np.where(dn > up, np.maximum(dn, 0), 0.0))
    # Wilder 累加平滑（和原脚本写法一样）
    def ws(x, n=50):
        x = np.nan_to_num(np.asarray(x, float)); o = np.zeros(len(x)); s = 0.0
        for i in range(len(x)):
            s = s - s / n + x[i]; o[i] = s
        return o
    st, sp, sm = ws(trr), ws(pdm), ws(mdm)
    dip, dim = sp / st * 100, sm / st * 100
    dx = pd.Series(np.abs(dip - dim) / (dip + dim) * 100)
    a1, a2 = P.ema(dx, 6), P.ema(dx, 12)
    # 原版欧元兑美元：止盈 / 止损各 500 点（0.005）≈ 0.45%
    return dict(le=B(P.cross_over(c, l) & (a1 < a2)), se=B(P.cross_under(c, u) & (a1 < a2)), sl=0.0045, tp=0.0045)


@reg('简单赚钱策略(EMA排列+RSI+随机)', 60, 4818956, 3232)
def s_simple_profit(df):
    c = df.c; e = [P.ema(c, n) for n in (8, 13, 21, 34, 55)]
    r = P.rsi(c, 14); k = P.stoch(c, df.h, df.l, 14)
    lE = (e[0] > e[1]) & (e[1] > e[2]) & (e[2] > e[3]) & (e[3] > e[4])
    sE = (e[0] < e[1]) & (e[1] < e[2]) & (e[2] < e[3]) & (e[3] < e[4])
    le = lE & (r < 70) & (r > 40) & (k < 80)
    se = sE & (r > 30) & (r < 60) & (k > 20)
    lx = (e[1] < e[4]) | (r > 70) | (k > 95)
    sx = (e[1] > e[4]) | (r < 30) | (k < 5)
    return dict(le=B(le), se=B(se), lx=B(lx), sx=B(sx))


@reg('Crypto Scalper 背离MACD+PSAR+EMA(原1分钟)', 5, 11411286, 3379)
def s_crypto_scalper(df):
    c = df.c; out = P.ema(c, 60); _, _, h = P.macd(c, 12, 26, 9)
    sar = P.sar(df.h, df.l, 0.02, 0.02, 0.2); up = c > sar
    le = ~up & (h < 0) & (c > out); se = up & (h > 0) & (c < out)
    # 原版：在下一根 SAR 价挂停损单进场（这里用当根 SAR 近似）；多单止盈 24.5% 止损 100%，空单止盈 5.5% 止损 3%
    return dict(le=B(le), se=B(se), es=(sar.values, sar.values), sl=(1.0, 0.03), tp=(0.245, 0.055))


@reg('Crypto BOT 低周期(EMA+随机)', 5, 9182728, 3139)
def s_crypto_bot(df):
    c, h, l = df.c, df.h, df.l; o50, o100 = P.ema(c, 50), P.ema(c, 100)
    k = P.sma(P.stoch(c, h, l, 5), 3)
    cu, co = P.cross_over(k, 20), P.cross_under(k, 80)
    over = sum(P.cross_over(c.shift(i), o50) for i in range(5)) > 0
    under = (P.cross(c.shift(4), o50) | P.cross_under(c.shift(3), o50) | P.cross_under(c.shift(2), o50) |
             P.cross_under(c.shift(1), o50) | P.cross_under(c, o50))
    touch = sum((P.cross(l.shift(i), o50) | P.cross(h.shift(i), o50)) for i in range(1, 5)) > 0
    _, _, hist = P.macd(c, 12, 26, 9)
    any_ = over | under | touch
    le = any_ & (c >= o50) & cu & (o50 > o100) & (hist >= 0)
    se = any_ & (c <= o50) & co & (o50 < o100) & (hist <= 0)
    return dict(le=B(le), se=B(se), sl=0.10, tp=0.10)     # 原版止盈止损各 10%（只在 touch 那根挂，这里一直挂着）


@njit(cache=True)
def _pmax(m, a, mult):
    n = len(m); out = np.full(n, np.nan); lsp = np.nan; ssp = np.nan; d = 1
    for i in range(n):
        if np.isnan(a[i]): continue
        ls = m[i] - mult * a[i]; ss = m[i] + mult * a[i]
        lp = ls if np.isnan(lsp) else lsp; sp = ss if np.isnan(ssp) else ssp
        ls = max(ls, lp) if m[i] > lp else ls
        ss = min(ss, sp) if m[i] < sp else ss
        if d == -1 and m[i] > sp: d = 1
        elif d == 1 and m[i] < lp: d = -1
        out[i] = ls if d == 1 else ss
        lsp, ssp = ls, ss
    return out


@reg('Profit Maximizer 利润最大化(PMax)', 60, 8802072, 3112)
def s_pmax(df):
    src = (df.h + df.l) / 2
    m = _var(src.values, 12); a = P.sma(P.tr(df.h, df.l, df.c), 10).values
    pm = pd.Series(_pmax(m, a, 3.0)); m = pd.Series(m)
    f, s = P.sma(df.c, 12), P.sma(df.c, 26); mc = f - s; hist = mc - P.ema(mc, 9)
    sar = P.sar(df.h, df.l, 0.02, 0.03, 0.3); up = df.c > sar
    cc = P.cci(df.c, 20)
    le = (hist > 0) & up & (cc >= 0) & P.cross_over(m, pm)
    se = (hist < 0) & ~up & (cc < 0) & P.cross_under(m, pm)
    return dict(le=B(le), se=B(se), sl=0.01, tp=0.35)


@reg('BTC bot 周枢轴+RSI(只做多,分批止盈)', 15, 9027337, 3111)
def s_btc_bot(df):
    t = pd.to_datetime(df.t, unit='ms')
    wk = t.dt.to_period('W-SUN')
    g = df.groupby(wk.values).agg(h=('h', 'max'), l=('l', 'min'), c=('c', 'last'))
    prev = g.shift()
    ph, pl, pc = (prev[x].reindex(wk.values).values for x in ('h', 'l', 'c'))
    PP = (ph + pl + pc) / 3; R3 = ph + 2 * (PP - pl)
    v = P.rsi(df.c, 21); pp = P.ema(v, 21); cc = (v + (v - pp) * 5 + pp) / 2
    le = P.cross_over(cc, 0); se = P.cross_over(df.c.shift(), pd.Series(R3))
    return dict(le=B(le), se=B(se), long_only=True, sl=0.15, ptp=[(0.03, 0.25), (0.05, 0.25), (0.07, 0.25)], tp=0.10)


@reg('EMA 交叉 10/20', 60, 9806157, 2881)
def s_ema_cross(df):
    f, s = P.ema(df.c, 10), P.ema(df.c, 20)
    return dict(le=B(P.cross_over(f, s)), se=B(P.cross_under(f, s)))


@reg('CRYPTO 3EMA+ATR止盈止损(只做多)', 15, 10487472, 2529)
def s_3ema(df):
    sl_, mid, fa = P.ema(df.c, 55), P.ema(df.c, 21), P.ema(df.c, 9); a = P.atr(df.h, df.l, df.c, 14)
    return dict(le=B(P.cross_over(mid, sl_)), lx=B(P.cross_under(fa, mid)), long_only=True,
                slp=((df.c - 2 * a).values, np.nan), tpp=((df.c + 3 * a).values, np.nan))


@njit(cache=True)
def _ut(src, a, mult):
    n = len(src); t = np.zeros(n)
    for i in range(n):
        sl = mult * a[i] if not np.isnan(a[i]) else 0.0
        p = t[i - 1] if i > 0 else 0.0
        s1 = src[i - 1] if i > 0 else src[i]
        if src[i] > p and s1 > p: t[i] = max(p, src[i] - sl)
        elif src[i] < p and s1 < p: t[i] = min(p, src[i] + sl)
        elif src[i] > p: t[i] = src[i] - sl
        else: t[i] = src[i] + sl
    return t


@reg('UT Bot v2 ATR追踪止损', 60, 22138906, 2449)
def s_utbot(df):
    t = pd.Series(_ut(df.c.values, P.atr(df.h, df.l, df.c, 10).values, 1.0))
    return dict(le=B(P.cross_over(df.c, t)), se=B(P.cross_over(t, df.c)))


@reg('MACD+ATR浮动止损(只做多,分批止盈)', 30, 9202779, 2422)
def s_macd_atr(df):
    c = df.c
    m = P.ema(c, 3) - P.ema(c, 5); sig = P.sma(m, 2)
    le = P.cross_over(m, sig) & (P.sma(c, 34) < P.ema(c, 34))
    ep = P.valuewhen(le, c)
    slf = ep - 2.2 * P.rma(P.tr(df.h, df.l, df.c), 17)
    return dict(le=B(le), lx=B(c < slf), long_only=True, ptp=[(0.01, 0.10), (0.05, 0.50)])


@reg('RSI-VWAP 超卖抄底(只做多)', 30, 9743888, 2408)
def s_rsi_vwap(df):
    r = P.rsi(P.vwap_day(df), 16)
    # 原版卖出时只平 50%，这里按全平算（单仓位）
    return dict(le=B(P.cross_over(r, 18)), lx=B(P.cross_under(r, 80)), long_only=True)


@njit(cache=True)
def _st2(src, c, a, mult):
    n = len(c); up = np.zeros(n); dn = np.zeros(n); tr = np.ones(n)
    for i in range(n):
        u = src[i] - mult * a[i]; d = src[i] + mult * a[i]
        u1 = up[i - 1] if i > 0 and not np.isnan(up[i - 1]) else u
        d1 = dn[i - 1] if i > 0 and not np.isnan(dn[i - 1]) else d
        if i > 0 and c[i - 1] > u1: u = max(u, u1)
        if i > 0 and c[i - 1] < d1: d = min(d, d1)
        up[i] = u; dn[i] = d
        t = tr[i - 1] if i > 0 else 1.0
        if t == -1 and c[i] > d1: t = 1.0
        elif t == 1 and c[i] < u1: t = -1.0
        tr[i] = t
    return up, dn, tr


def st_classic(df, n, mult, src=None, rma_atr=True):
    """经典 SuperTrend（KivancOzbilici 写法）：返回 up, dn, trend(1 多 / -1 空)"""
    src = (df.h + df.l) / 2 if src is None else src
    a = (P.atr(df.h, df.l, df.c, n) if rma_atr else P.sma(P.tr(df.h, df.l, df.c), n)).fillna(0).values
    u, d, t = _st2(src.values, df.c.values, a, float(mult))
    return pd.Series(u), pd.Series(d), pd.Series(t)


@reg('三重SuperTrend+随机RSI(原1分钟)', 5, 10540165, 2407)
def s_3st(df):
    c = df.c
    su, sd, st_ = st_classic(df, 12, 3); mu, md, mt = st_classic(df, 11, 2); fu, fd, ft = st_classic(df, 10, 1)
    r = P.rsi(c, 14); k = P.sma(P.stoch(r, r, r, 14), 3); d = P.sma(k, 3)
    cnt = st_ + mt + ft; et = P.ema(c, 200) < c
    le = et & (k < 20) & P.cross_over(k, d) & (cnt >= 1)
    se = ~et & (k > 80) & P.cross_under(k, d) & (cnt <= -1)
    ls = np.where(cnt == 3, mu, su); ss = np.where(cnt == -3, md, sd)
    lp = c + 1.5 * (c - ls).abs(); sp = c - 1.5 * (c - ss).abs()
    # 原版：在止损价挂停损单进场（止损价在现价下方 = 下一根开盘直接成交）
    return dict(le=B(le), se=B(se), slp=(ls, ss), tpp=(lp.values, sp.values))


@reg('鳄鱼线剥头皮 Alligator(原1分钟)', 5, 6996122, 2315)
def s_alligator(df):
    ho, hh, hl, hc = P.heikin(df)
    hl2 = (hh + hl) / 2
    dz = np.where((hc > ho) & (hc.shift() > ho.shift()), 1, np.where((hc < ho) & (hc.shift() < ho.shift()), -1, 0))
    jaw, teeth, lips = P.sma(hl2, 13), P.sma(hl2, 8), P.sma(hl2, 5)
    le = (dz == 1) & (jaw < teeth) & (jaw < lips) & (teeth < lips)
    se = (dz == -1) & (jaw > teeth) & (jaw > lips) & (teeth > lips)
    lx = (jaw > teeth) | (jaw > lips) | (teeth > lips)
    sx = (jaw < teeth) | (jaw < lips) | (teeth < lips)
    return dict(le=B(le), se=B(se), lx=B(lx), sx=B(sx))


@reg('Extreme Scalping 线性回归偏离(原1分钟)', 5, 6608075, 2298)
def s_extreme(df):
    out = P.linreg(df.c, 14, 1)
    # 原版 BTC 点数：偏离 100 跳、止盈 300 跳、止损 100 跳（BTC 1 跳=0.01 美元，几乎为 0）；按币价比例换成 偏离 0.3%、止盈 0.9%、止损 0.3%
    return dict(le=B(df.c < out * (1 - 0.003)), se=B(df.c > out * (1 + 0.003)), sl=0.003, tp=0.009)


@njit(cache=True)
def _surf(c, h, l, lc, sc, tra, fa):
    n = len(c); stopL = np.full(n, np.nan); stopS = np.full(n, np.nan)
    it = 0; lts = 0.0; sts = 1e18; fp = np.nan; ff = np.nan; fsl_prev = np.nan
    for i in range(n):
        prev_it = it
        if (lc[i] or sc[i]) and it == 0:
            it = 1 if lc[i] else -1
        if it == 1 and (sc[i] or (l[i] <= max(fsl_prev if fsl_prev == fsl_prev else -1e18, lts))):
            it = 0
        if it == -1 and (lc[i] or (h[i] >= min(fsl_prev if fsl_prev == fsl_prev else 1e18, sts))):
            it = 0
        lts = max(c[i] - tra[i], lts) if it == 1 else 0.0
        sts = min(c[i] + tra[i], sts) if it == -1 else 1e18
        if it != 0 and it != prev_it:
            fp = c[i]; ff = fa[i]
        fsl = np.nan
        if it == 1:
            fsl = fp - ff; stopL[i] = max(fsl, lts)
        elif it == -1:
            fsl = fp + ff; stopS[i] = min(fsl, sts)
        fsl_prev = fsl
    return stopL, stopS


@reg('Trend Surfers 168根突破', 60, 10240619, 2272)
def s_surfers(df):
    hh, ll = P.highest(df.h, 168).shift(), P.lowest(df.l, 168).shift()
    lc, sc = P.cross_over(df.c, hh), P.cross_under(df.c, ll)
    tra = (P.atr(df.h, df.l, df.c, 10) * 8).fillna(0).values; fa = (P.atr(df.h, df.l, df.c, 10) * 2).fillna(0).values
    sL, sS = _surf(df.c.values, df.h.values, df.l.values, B(lc), B(sc), tra, fa)
    return dict(le=B(lc), se=B(sc), es=(hh.values, ll.values), slp=(sL, sS), dyn=True)


@reg('RSI 趋势(35上穿买,75下穿卖)', 15, 10304792, 2184)
def s_rsi_trend(df):
    r = P.rsi(df.c, 14)
    return dict(le=B(P.cross_over(r, 35)), lx=B(P.cross_under(r, 75)), long_only=True)


@reg('MACD+布林+RSI(Alorse,只做多)', 60, 11594581, 2145)
def s_alorse(df):
    c = df.c; m, s, _ = P.macd(c, 12, 26, 9); b, u, l = P.bb(c, 20, 2.0); r = P.rsi(c, 14)
    return dict(le=B(P.cross_over(m, s) & (r < 50) & (c < b)), lx=B((r > 70) & (c > u)), long_only=True)


@njit(cache=True)
def _friction(h, l, c, bb):
    n = len(c); f = np.full(n, np.nan)
    for i in range(bb, n):
        s = 0.0
        for k in range(1, bb + 1):
            if h[i - k] >= c[i] and l[i - k] <= c[i]:
                s += (1.0 + bb) / (k + bb)
        f[i] = s
    return f


@njit(cache=True)
def _keep(lo, sh, end):
    n = len(lo); kl = np.zeros(n); ks = np.zeros(n); a = 0.0; b = 0.0
    for i in range(n):
        if lo[i]: a = 1.0
        if sh[i] or end[i]: a = 0.0
        if sh[i]: b = 1.0
        if lo[i] or end[i]: b = 0.0
        kl[i] = a; ks[i] = b
    return kl, ks


@reg('LUBE 摩擦力突破', 30, 7495151, 1972)
def s_lube(df):
    fr = pd.Series(_friction(df.h.values, df.l.values, df.c.values, 500))
    lowf, highf = P.lowest(fr, 100), P.highest(fr, 100)
    midf = lowf * 0.5 + highf * 0.5; low2 = lowf * 1.1 + highf * -0.1
    c = df.c; fir = (4 * c + 3 * c.shift().fillna(0) + 2 * c.shift(2).fillna(0) + c.shift(3).fillna(0)) / 10
    tr_ = np.where(fir > fir.shift(), 1, -1)
    lo = (fr < low2.shift(5)) & (tr_ == 1); sh = (fr < low2.shift(5)) & (tr_ == -1); end = fr > midf.shift(5)
    return dict(le=B(lo), se=B(sh), lx=B(sh | end), sx=B(lo | end))


@reg('EMA 交叉 20/60(hl2)', 60, 9501944, 1893)
def s_ema_cross2(df):
    hl2 = (df.h + df.l) / 2; f, s = P.ema(hl2, 20), P.ema(hl2, 60)
    return dict(le=B(P.cross_over(f, s)), se=B(P.cross_under(f, s)))


@njit(cache=True)
def _atr_psar(o, h, l, c, a, start, inc, mx):
    n = len(c); tb = np.zeros(n)
    psar = np.nan; af = start; td = 0; ep = np.nan; bars = 0
    for i in range(1, n):
        if i == 1:
            td = 1 if c[0] > o[0] else -1
            ch = True
        else:
            l2s = td == 1 and c[i] <= psar
            s2l = td == -1 and c[i] >= psar
            ch = l2s or s2l
            if l2s: td = -1
            elif s2l: td = 1
        if i > 1:
            if ch: bars = td
            elif td == 1: bars += 1
            else: bars -= 1
        ep_prev = ep
        if ch: af = start
        elif (td == 1 and h[i] > ep_prev) or (td == -1 and l[i] < ep_prev): af = min(mx, af + inc)
        if ch: ep = h[i] if td == 1 else l[i]
        else: ep = max(ep_prev, h[i]) if td == 1 else min(ep_prev, l[i])
        ai = a[i] if not np.isnan(a[i]) else h[i] - l[i]
        if i == 1: psar = l[0] if c[0] > o[0] else h[0]
        elif ch: psar = ep_prev
        else: psar = psar + af * ai if td == 1 else psar - af * ai
        tb[i] = bars
    return tb


@reg('ATR 抛物线SAR(QuantNomad)', 60, 8217378, 1876)
def s_atr_psar(df):
    tb = _atr_psar(df.o.values, df.h.values, df.l.values, df.c.values, P.atr(df.h, df.l, df.c, 14).values, 0.02, 0.02, 0.2)
    return dict(le=tb == 1, se=tb == -1)


@njit(cache=True)
def _noro_trend(dn_ok, up_ok):
    n = len(dn_ok); t = np.zeros(n); x = 0.0
    for i in range(n):
        if dn_ok[i]: x = -1.0
        elif up_ok[i]: x = 1.0
        t[i] = x
    return t


@reg("Noro 通道剥头皮 v1.6", 60, 4464636, 1838)
def s_noro_bands(df):
    c, o = df.c, df.o
    hi, lo = P.highest(c, 20), P.lowest(c, 20); ce = (hi + lo) / 2
    ds = P.sma((c - ce).abs(), 20); hd, ld, ld2 = ce + ds, ce - ds, ce - 2 * ds
    t = pd.Series(_noro_trend(B((c < ld) & (df.h < ce)), B((c > hd) & (df.l > ce))))
    body = (c - o).abs(); sb = P.ema(body, 30) / 10 * 10
    bar = np.sign(c - o)
    up7 = (t == 1) & (((bar == -1) & (bar.shift() == -1)) | ((body > sb) & (bar == -1)))
    dn7 = (t == 1) & (((bar == 1) & (bar.shift() == 1)) | (c > hd))
    up8 = (t == -1) & (((bar == -1) & (bar.shift() == -1)) | (c < ld2))
    dn8 = (t == -1) & (((bar == 1) & (bar.shift() == 1)) | ((body > sb) & (bar == 1)))
    # 逆势那边下单量为 0（不开新仓），原版用它在"赚钱时"平掉原来的仓（take=0%）
    return dict(le=B(up7), se=B(dn8), lx=B(dn7), sx=B(up8), pexit=True)


@reg('Coinrule 顺势剥头皮(只做多)', 60, 10874629, 1623)
def s_coinrule_scalp(df):
    c = df.c; r = P.rsi(c, 14)
    le = P.cross_under(c, P.sma(c, 9)) & (P.sma(c, 50) > P.sma(c, 100)) & (r > 50)
    return dict(le=B(le), lx=B((r < 30) | (r > 70)), long_only=True)


@njit(cache=True)
def _zz(r1, s1, pi):
    n = len(r1); tr = np.zeros(n); t = 1.0; LL = s1[0]; HH = r1[0]
    for i in range(n):
        if t > 0:
            if r1[i] >= HH: HH = r1[i]
            elif s1[i] < HH * (1 - pi):
                t = -1.0; LL = s1[i]
        else:
            if s1[i] <= LL: LL = s1[i]
            elif r1[i] > LL * (1 + pi):
                t = 1.0; HH = s1[i]
        tr[i] = t
    return tr


@reg('ZigZag RSI 15分钟(只做多)', 15, 9054840, 1530)
def s_zz_rsi(df):
    c = df.c; v = P.rsi(c, 5)
    lo, sh = B(P.cross_over(v, 25)), B(P.cross_under(v, 75))
    r1 = pd.Series(np.where(lo, c, np.nan)).ffill().fillna(0).values
    s1 = pd.Series(np.where(sh, c, np.nan)).ffill().fillna(0).values
    t = pd.Series(_zz(r1, s1, 0.01))
    return dict(le=B(P.cross_over(t, 0)), lx=B(P.cross_under(t, 0)), long_only=True)


@reg('简单RSI 超卖买(止盈3%止损10%)', 5, 9329736, 1183)
def s_simple_rsi(df):
    r = P.rsi(df.c, 5)
    # 原版最多加仓 7 次；这里只开一单
    return dict(le=B(r <= 25), long_only=True, tp=0.03, sl=0.10)


@reg("Noro 趋势均线 v2.0", 60, 4422473, 1167)
def s_noro_trend(df):
    c, o, h, l = df.c, df.o, df.h, df.l
    ce = (P.highest(c, 21) + P.lowest(c, 21)) / 2; ce2 = (P.highest(c, 5) + P.lowest(c, 5)) / 2
    t = pd.Series(_noro_trend(B((h < ce) & (h.shift() < ce.shift())), B((l > ce) & (l.shift() > ce.shift()))))
    # _noro_trend 的优先级：先看空头条件（原版先判断 low>center 多头，两者不会同时成立）
    bar = np.sign(c - o)
    red2 = (bar == -1) & (bar.shift() == -1); green2 = (bar == 1) & (bar.shift() == 1)
    fr = P.rsi(c, 2); ln = (c - P.sma(c, 10)).abs(); sm = P.sma(ln, 100); mn = np.minimum(o, c)
    up3 = (c < o) & (ln > sm * 3) & (mn < pd.Series(mn).shift()) & (fr < 10)
    up = (t == 1) & (l < ce2) & red2
    dn = (t == -1) & (h > ce2) & green2
    up2 = (h < ce) & (h < ce2) & (bar == -1)
    return dict(le=B(up | up2 | up3), se=B(dn & ~(up | up2 | up3)))


@reg('均线剥头皮 MA Scalper(只做多)', 15, 10488313, 1165)
def s_ma_scalper(df):
    c = df.c; s9, s50, s100, s200 = (P.sma(c, n) for n in (9, 50, 100, 200))
    le = P.cross_over(s9, s50) & (s50 < s100) & (s100 < s200)
    return dict(le=B(le), lx=B(P.cross_over(s9, s200)), long_only=True)


@njit(cache=True)
def _pivrev(h, l, swh, swl):
    n = len(h); LE = np.zeros(n, np.bool_); SE = np.zeros(n, np.bool_); hp = np.full(n, np.nan); lp = np.full(n, np.nan)
    le = False; se = False; hpr = np.nan; lpr = np.nan
    for i in range(n):
        if not np.isnan(swh[i]): hpr = swh[i]; le = True
        elif le and h[i] > hpr: le = False
        if not np.isnan(swl[i]): lpr = swl[i]; se = True
        elif se and l[i] < lpr: se = False
        LE[i] = le; SE[i] = se; hp[i] = hpr; lp[i] = lpr
    return LE, SE, hp, lp


@reg('枢轴反转 Pivot Reversal(QuantNomad)', 60, 6076844, 727)
def s_pivrev(df):
    swh, swl = P.pivothigh(df.h, 4, 4).values, P.pivotlow(df.l, 4, 4).values
    LE, SE, hp, lp = _pivrev(df.h.values, df.l.values, swh, swl)
    tick = df.c.values * 1e-5
    return dict(le=LE, se=SE, es=(hp + tick, lp - tick))


def mtf(df, coin, chart_tf, tf, f):
    """request.security(另一个周期, f) 不偷看：每根图表K线收盘时，取那时已经收完的最近一根 tf 周期K线的值"""
    import runner
    o = runner.frame(coin, tf).reset_index(names='t')
    v = pd.Series(np.asarray(f(o), float), index=o.t.values + tf * 60_000)          # 收盘时间
    close_t = df.t.values + chart_tf * 60_000
    return v.reindex(close_t, method='ffill').values


@reg('黄金白银30分钟 RSI+EMA162', 30, 3679368, 2134)
def s_gold30(df):
    c = df.c; r = P.rsi(c, 14); e = P.ema(c, 162)
    # 原版 strategy.exit 只写了 when 没给价格，等于不起作用：只靠反手
    return dict(le=B((c > e) & (r < 35)), se=B((c < e) & (r > 35)))


@reg('Swing Hull+RSI+EMA', 60, 3506912, 2056)
def s_swing_hull(df):
    c = df.c
    def hull_parts(x):
        d = 2 * P.wma(x, 50) - P.wma(x, 100); return P.wma(d, 10)
    n1 = hull_parts(c); n2 = hull_parts(c.shift())
    mf, ms = P.ema(c, 50), P.ema(c, 100); r = P.rsi(c, 14)
    le = (n1 > n2) & (c < mf) & (c.shift() > mf.shift())
    se = ~(n1 > n2) & (c < ms) & (c.shift() > mf.shift())
    # 原版英镑日元止损 750 跳 ≈ 0.4%
    return dict(le=B(le), se=B(se), lx=B(P.cross_over(r, 80)), sx=B(P.cross_under(r, 20)), sl=0.004)


@reg('RSI 二元期权(拿3根)', 5, 3836755, 1960)
def s_rsi_binary(df):
    r = P.rsi(df.c, 6)
    return dict(le=B(P.cross_under(r, 13)), se=B(P.cross_over(r, 82)), max_bars=3)


@njit(cache=True)
def _cool(bc, sc, cd):
    n = len(bc); le = np.zeros(n, np.bool_); se = np.zeros(n, np.bool_); b = cd; s = cd
    for i in range(n):
        if bc[i]:
            if b >= cd:
                le[i] = True; s = cd; b = 0
            else:
                b += 1
        if sc[i]:
            if s >= cd:
                se[i] = True; s = 0; b = cd
            else:
                s += 1
    return le, se


@reg('多周期RSI 5/15/30(TradeDots)', 30, 16919373, 1908)
def s_mtf_rsi(df, coin):
    r1 = mtf(df, coin, 30, 5, lambda o: P.rsi(o.c, 14))
    r2 = mtf(df, coin, 30, 15, lambda o: P.rsi(o.c, 14))
    r3 = P.rsi(df.c, 14).values
    le, se = _cool((r1 < 30) & (r2 < 30) & (r3 < 30), (r1 > 70) & (r2 > 70) & (r3 > 70), 5)
    return dict(le=le, se=se)


@reg('外汇剥头皮 布林+RSI+ADX(原1分钟)', 5, 11492005, 1899)
def s_fx_scalp(df):
    c = df.c; r = P.rsi(c, 20); b, u, l = P.bb(c, 60, 2.0)
    _, _, adx = P.dmi(df.h, df.l, df.c, 14, 14)
    le = (c < u) & P.cross_over(r, 35) & (adx < 32)
    se = (c > u) & P.cross_under(r, 65) & (adx < 32)
    # 原版欧元兑美元 止盈止损各 90 跳 ≈ 0.08%
    return dict(le=B(le), se=B(se), lx=B(P.cross_under(c, b)), sx=B(P.cross_over(c, b)), sl=0.0008, tp=0.0008)


@reg('TSI+CCI+Hull(止盈0.5%)', 15, 7881053, 1884)
def s_tsi_cci_hull(df):
    p = df.c; pc = p.diff()
    tsi = 100 * P.ema(P.ema(pc, 50), 50) / P.ema(P.ema(pc.abs(), 50), 50)
    n1 = P.wma(2 * P.wma(p, 13) - P.wma(p, 26), 5)
    dev = pd.Series(P._mad(p.values.astype(float), 26))
    cci = (p - n1) / (0.015 * dev)
    t1 = P.ema(tsi * 5, 125); t2 = t1.shift(2)
    hs = P.wma(2 * P.wma(n1, 13) - P.wma(n1, 26), 5)
    lc = (t1 > t2) & (hs < p) & (cci > 0); sc = (t1 < t2) & (hs > p) & (cci < 0)
    le = lc & (cci > cci.shift()) & (n1 > n1.shift())
    se = sc & (cci < cci.shift()) & (n1 < n1.shift())
    return dict(le=B(le), se=B(se), lx=B(se), sx=B(le), tp=0.005)


@reg('PMax on RSI + T3(原1分钟)', 5, 10986709, 1739)
def s_pmax_rsi(df):
    c, h, l = df.c, df.h, df.l
    i1 = (c - c.shift()).clip(lower=0); i2 = (c.shift() - c).clip(lower=0)
    def ww(x, n=14):
        x = np.nan_to_num(np.asarray(x, float)); o = np.zeros(len(x)); a = 1 / n
        for k in range(len(x)):
            o[k] = a * x[k] + (1 - a) * (o[k - 1] if k else 0)
        return o
    rs = ww(i1) / ww(i2); rsi = pd.Series(100 - 100 / (1 + rs))
    au, ad = P.sma(i1, 14), P.sma(i2, 14); cp = c.shift()
    k1 = np.where(h > cp, h - cp, 0); k2 = np.where(h < cp, cp - h, 0); k3 = np.where(l > cp, l - cp, 0); k4 = np.where(l < cp, cp - l, 0)
    rih = 100 - 100 / (1 + ((au * 13 + k1) / 14) / ((ad * 13 + k2) / 14))
    ril = 100 - 100 / (1 + ((au * 13 + k3) / 14) / ((ad * 13 + k4) / 14))
    trr = pd.concat([rih - ril, (rih - rsi.shift()).abs(), (ril - rsi.shift()).abs()], axis=1).max(axis=1)
    atr = P.sma(trr, 10)
    a = 0.7; e = [rsi]
    for _ in range(6): e.append(P.ema(e[-1], 8))
    t3 = -a ** 3 * e[6] + (3 * a * a + 3 * a ** 3) * e[5] + (-6 * a * a - 3 * a - 3 * a ** 3) * e[4] + (1 + 3 * a + a ** 3 + 3 * a * a) * e[3]
    pm = pd.Series(_pmax(t3.values, atr.values, 3.0))
    return dict(le=B(P.cross_over(t3, pm)), se=B(P.cross_under(t3, pm)))


@reg('yuthavithi 波动率力度剥头皮(原1分钟)', 5, 3203897, 1736)
def s_yutha(df):
    c, o = df.c, df.o
    a_f, a_s = P.atr(df.h, df.l, c, 20), P.atr(df.h, df.l, c, 50)
    xf = (df.v * c.diff().fillna(0)).cumsum(); f, s = P.ema(xf, 3), P.ema(xf, 20)
    bear = (f < s) & (a_f > a_s) & ((f.shift() > s.shift()) | (a_f.shift() < a_s.shift())) & (c < o)
    bull = (f > s) & (a_f > a_s) & ((f.shift() < s.shift()) | (a_f.shift() < a_s.shift())) & (c > o)
    return dict(le=B(bull), se=B(bear))


@reg('三重SuperTrend+EMA+随机RSI', 60, 10795040, 1708)
def s_triple_st(df, coin):
    c = df.c
    e15 = mtf(df, coin, 60, 15, lambda o: P.ema(o.c, 200))
    r = P.rsi(c, 14); k = P.sma(P.stoch(r, r, r, 14), 3); d = P.sma(k, 3)
    _, _, t1 = st_classic(df, 10, 1.0); _, _, t2 = st_classic(df, 11, 2.0); _, _, t3 = st_classic(df, 12, 3.0)
    # 原版 "close > trend and trend1 and trend2" 在 Pine 里是 (close > trend) and trend1 and trend2，trend=±1 一直为真
    allu = c > t1
    le = (c > e15) & (k > d) & (k.shift() <= d.shift()) & (k < 28) & allu
    sh2 = (c < t2) | True                       # twolinesoverprice 同理恒为真
    se = (c < e15) & (k < d) & (k.shift() >= d.shift()) & (k > 78) & sh2
    ll, hh = P.lowest(df.l, 30), P.highest(df.h, 25)
    # 止损 = 进场那根的近 30 根最低 / 25 根最高；止盈 = 风险 × 2（多）/ × 1.5（空），按信号那根算
    lt = c + (c - ll) * 2; stp = c - (hh - c) * 1.5
    return dict(le=B(le), se=B(se), slp=(ll.values, hh.values), tpp=(lt.values, stp.values))


@reg('3EMA+RSI 追踪止损(Free990,只做多)', 10, 18019298, 1637)
def s_free990(df):
    c = df.c; r = P.rsi(c, 14); a, b, cc = P.ema(c, 10), P.ema(c, 20), P.ema(c, 100)
    le = P.cross_over(a, b) & (a > cc) & (c > df.o)
    # 原版：止损 5%；追踪止损按跳数（1000CAT 上 50 跳 ≈ 0.5% 启动、10 跳 ≈ 0.1% 回撤）；拿满 24 根且赚钱就平
    return dict(le=B(le), lx=B(r > 70), long_only=True, sl=0.05, trail=0.001, tact=0.005, xbp=24)


@reg('XAUUSD 剥头皮 AGRESIV', 5, 21923693, 1612)
def s_xau(df):
    c = df.c; f, s = P.ema(c, 5), P.ema(c, 13); r = P.rsi(c, 7); a = P.atr(df.h, df.l, c, 14)
    le = P.cross_over(f, s) & (r > 55); se = P.cross_under(f, s) & (r < 45)
    # 止盈 1.2ATR、止损 0.8ATR、追踪 0.5ATR 启动（没设回撤 = 一回落就走）；原版"ATR>0.5"是黄金价格单位，币上去掉
    return dict(le=B(le), se=B(se), slp=((c - 0.8 * a).values, (c + 0.8 * a).values), tpp=((c + 1.2 * a).values, (c - 1.2 * a).values),
                trail=0.0, tact=(0.5 * a / c).values)


@reg('TradersAI UTBot(灵敏度10)', 15, 6750220, 1599)
def s_tradersai(df):
    t = pd.Series(_ut(df.c.values, P.atr(df.h, df.l, df.c, 10).values, 10.0))
    return dict(le=B((df.c > t) & P.cross_over(df.c, t)), se=B((df.c < t) & P.cross_over(t, df.c)))


@njit(cache=True)
def _tt(c, hi, lo):
    n = len(c); pos = np.zeros(n); ret = 0.0; p = 0.0
    for i in range(n):
        if np.isnan(hi[i]) or np.isnan(lo[i]):
            pos[i] = p; continue
        if c[i] > hi[i] and c[i] > lo[i]: ret = hi[i]
        elif c[i] < lo[i] and c[i] < hi[i]: ret = lo[i]
        if c[i] > ret: p = 1.0
        elif c[i] < ret: p = -1.0
        pos[i] = p
    return pos


@reg('趋势交易者+MACD(原1分钟)', 5, 7373383, 1512)
def s_trend_trader(df):
    avg = P.wma(P.tr(df.h, df.l, df.c), 21)
    hi = P.highest(df.h, 21).shift() - avg.shift() * 3; lo = P.lowest(df.l, 21).shift() + avg.shift() * 3
    pos = _tt(df.c.values, hi.values, lo.values)
    _, _, h = P.macd(df.c, 12, 26, 9)
    return dict(le=B((pos == 1) & (h > 0)), se=pos == -1)


@reg('Coinrule RSI+均线100/150 多空', 30, 14179202, 753)
def s_coinrule_rsi_sma(df):
    c = df.c; r = P.rsi(c, 14); f, s = P.sma(c, 100), P.sma(c, 150)
    return dict(le=B(P.cross_over(f, s) & (r > 50)), se=B(P.cross_over(s, f) & (r < 50)))


@reg('Coinrule RSI抄底(只做多)', 15, 9211055, 918)
def s_coinrule_dip(df):
    c = df.c; r = P.rsi(c, 14)
    return dict(le=B((r < 35) & (c < P.sma(c, 100))), lx=B(r > 65), long_only=True)


@reg('组合机器人 RSI+ADX+20均线+通道突破', 5, 14713710, 929)
def s_combined_bot(df):
    c = df.c; st, d = P.supertrend(df.h, df.l, c, 3, 10); s20 = P.sma(c, 20); a = P.rsi(c, 14)
    buy = (d < 0) & (c > st) & (c > s20) & (a > 70)
    ub, db = P.highest(df.h, 5), P.lowest(df.l, 5)
    # 原版每根都挂 5 根通道上下沿的停损单（突破就进），外加上面的追多信号；跌破 20 均线或 RSI<30 全平
    le = np.ones(len(c), bool); se = np.ones(len(c), bool)
    es_l = np.where(B(buy), np.nan, (ub * 1.00001).values)
    return dict(le=le, se=se, es=(es_l, (db * 0.99999).values), lx=B(P.cross_under(c, s20) | (a < 30)), sx=B(P.cross_under(c, s20) | (a < 30)))


@reg('双SuperTrend(ATR止盈)', 15, 12157389, 942)
def s_double_st(df):
    c = df.c; _, d1 = P.supertrend(df.h, df.l, c, 1.0, 14); _, d2 = P.supertrend(df.h, df.l, c, 4.0, 14)
    a = P.atr(df.h, df.l, c, 14)
    le = (d1 < 0) & (d2 < 0) & ((d1.shift() > 0) | (d2.shift() > 0))
    se = (d1 > 0) & (d2 > 0) & ((d1.shift() < 0) | (d2.shift() < 0))
    # 原版加仓（pyramiding）不做；止盈 = 进场那根收盘 ± 2ATR
    return dict(le=B(le), se=B(se), tpp=((c + 2 * a).values, (c - 2 * a).values))


@njit(cache=True)
def _st_simple(c, up, dn):
    n = len(c); tu = np.zeros(n); td = np.zeros(n); t = np.ones(n)
    for i in range(n):
        tu[i] = max(up[i], tu[i - 1]) if i > 0 and c[i - 1] > tu[i - 1] else up[i]
        td[i] = min(dn[i], td[i - 1]) if i > 0 and c[i - 1] < td[i - 1] else dn[i]
        if i > 0 and c[i] > td[i - 1]: t[i] = 1
        elif i > 0 and c[i] < tu[i - 1]: t[i] = -1
        else: t[i] = t[i - 1] if i > 0 else 1
    return t


@reg('SuperTrend 双周期+MACD', 60, 4922302, 959)
def s_st2macd(df, coin):
    c = df.c; hl2 = (df.h + df.l) / 2; a = P.atr(df.h, df.l, c, 1)
    t = pd.Series(_st_simple(c.values, (hl2 - a).values, (hl2 + a).values))
    def htf(o):
        h2 = (o.h + o.l) / 2; aa = P.atr(o.h, o.l, o.c, 1)
        return _st_simple(o.c.values, (h2 - aa).values, (h2 + aa).values)
    mt = mtf(df, coin, 60, 120, htf)
    m = P.sma(c, 12) - P.sma(c, 26); sg = P.sma(m, 9)
    le = (t == 1) & (t.shift() == -1) & (mt == 1) & (m > sg)
    se = (t == -1) & (t.shift() == 1) & (mt == -1) & (m < sg)
    # 原版 ETH 止盈 500 跳 / 止损 400 跳 ≈ 1.2% / 1.0%
    return dict(le=B(le), se=B(se), tp=0.012, sl=0.010)


@reg('随机指标趋势(只做多)', 15, 10377381, 918)
def s_stoch_trend(df):
    k = P.sma(P.stoch(df.c, df.h, df.l, 200), 100)
    return dict(le=B(P.cross_over(k, 50)), lx=B(P.cross_under(k, 60)), long_only=True)


def _fractals(h, l, n=2):
    h, l = pd.Series(h), pd.Series(l)
    def side(x, gt):
        cmp = (lambda a, b: a < b) if gt else (lambda a, b: a > b)
        cmpe = (lambda a, b: a <= b) if gt else (lambda a, b: a >= b)
        c0 = x.shift(n)
        down = np.ones(len(x), bool)
        for i in range(1, n + 1):
            down &= cmp(x.shift(n - i), c0).values
        ups = []
        for k in range(5):
            f = np.ones(len(x), bool)
            for i in range(1, n + 1):
                g = np.ones(len(x), bool)
                for m in range(1, k + 1):
                    g &= cmpe(x.shift(n + m), c0).values
                f &= g & cmp(x.shift(n + i + k), c0).values
            ups.append(f)
        return down & np.logical_or.reduce(ups)
    return side(h, True), side(l, False)


@reg('威廉分形+EMA+RSI(止盈0.5%止损3.1%)', 15, 11744312, 721)
def s_fractals(df):
    up, dn = _fractals(df.h.values, df.l.values, 2)
    c = df.c; a, b, cc = P.ema(c, 20), P.ema(c, 50), P.ema(c, 100); r = P.rsi(c, 14)
    le = (a > b) & (a > cc) & dn & (df.l.shift(2) > cc) & (r.shift(2) < r)
    se = (a < b) & (a < cc) & up & (df.h.shift(2) < cc) & (r.shift(2) > r)
    return dict(le=B(le), se=B(se), tp=0.005, sl=0.031)


@reg('ATR+RSI 追踪止损(liwei666)', 15, 14474655, 745)
def s_atr_rsi(df):
    c = df.c; a = P.atr(df.h, df.l, c, 26); am = P.sma(a, 45); r = P.rsi(c, 15)
    nm = am / c * 100
    ok = (nm >= 0.3) & (nm <= 0.7) & (a > am)
    return dict(le=B(ok & (r > 60)), se=B(ok & ~(r > 60) & (r < 40)), trail=0.015, tp=(0.03, 0.06))


@reg('动量 随机RSI 30分钟(TVHUB)', 30, 9420176, 691)
def s_mom_srsi(df):
    c = df.c; r = P.rsi(c, 12); k = P.ema(P.stoch(r, r, r, 12), 3); d = P.ema(k, 6)
    gc, dc = P.cross_over(k, d), P.cross_under(k, d)
    oversold = gc & (d <= 30.6); overbought = dc & (d >= 85.29)
    up = P.ema(c, 1) > P.ema(c, 60)
    le = oversold & up; se = overbought & ~up
    lx = (up & (P.barssince(overbought) == 6)) | (~up & overbought)
    sx = (~up & (P.barssince(oversold) == 6)) | (up & oversold)
    return dict(le=B(le), se=B(se), lx=B(lx), sx=B(sx), sl=(0.10, 0.20), tp=(0.08, 0.35))


@reg('Best SuperTrend BTCUSD(1小时DEMA交叉)', 45, 4065274, 736)
def s_best_st(df, coin):
    def dema(o, n):
        e = P.ema(o.c, n); return 2 * e - P.ema(e, n)
    f = pd.Series(mtf(df, coin, 45, 60, lambda o: dema(o, 55))); s = pd.Series(mtf(df, coin, 45, 60, lambda o: dema(o, 24)))
    return dict(le=B(P.cross_over(s.shift(), f.shift())), se=B(P.cross_under(s.shift(), f.shift())))


@reg('一目 先行带交叉(Kumo Twist)', 60, 4236721, 741)
def s_kumo(df):
    dc = lambda n: (P.lowest(df.l, n) + P.highest(df.h, n)) / 2
    l1 = (dc(9) + dc(26)) / 2; l2 = dc(52)
    gl, gs = P.cross_over(l1, l2), P.cross_under(l1, l2)
    return dict(le=B(gl), se=B(gs), es=((df.h * 1.00001).values, (df.l * 0.99999).values))


@reg('Rob Booker ADX突破', 60, 13727336, 739)
def s_adx_break(df):
    _, _, adx = P.dmi(df.h, df.l, df.c, 14, 14)
    up, lo = P.highest(df.h, 20).shift(), P.lowest(df.l, 20).shift(); w = up - lo
    low_adx = adx < 18
    le = P.cross(df.c, up) & low_adx; se = P.cross(df.c, lo) & low_adx & ~le
    # 只在空仓时开；止盈 = 1 倍箱体宽，止损 = 0.5 倍（按进场价算）
    return dict(le=B(le), se=B(se), tp=(w / df.c).values, sl=(0.5 * w / df.c).values, lx=None)


@reg('反困住逆势单剥头皮', 60, 9606867, 753)
def s_trapped(df):
    src = (df.h + df.l) / 2
    k = P.sma(P.stoch(src, df.h, df.l, 60), 13); r = P.rsi(src, 5)
    upper = np.minimum(np.minimum(k - 50, r - 50), 35 - P.lowest(r, 15))
    lower = np.maximum(np.maximum(k - 50, r - 50), 100 - 35 - P.highest(r, 15))
    return dict(le=B(upper > 0), se=B(lower < 0))


@reg('GetTrend 370分钟K线方向', 30, 3752791, 772)
def s_gettrend(df, coin):
    o = pd.Series(mtf(df, coin, 30, 370, lambda x: x.o)); c = pd.Series(mtf(df, coin, 30, 370, lambda x: x.c))
    return dict(le=B(P.cross_over(c, o)), se=B(P.cross_under(c, o)))


@reg('BTFD 放量超卖抄底(原3分钟,分批止盈)', 5, 15507345, 761)
def s_btfd(df):
    vc = df.v > P.sma(df.v, 70) * 2.5; r = P.rsi(df.c, 20)
    return dict(le=B(vc & (r <= 30)), long_only=True, sl=0.05, ptp=[(0.004, 0.2), (0.006, 0.4), (0.008, 0.4)])


@reg('日线收盘比较(昨天涨就做多)', 60, 3295631, 6377)
def s_daily_close(df, coin):
    c_today = pd.Series(mtf(df, coin, 60, 1440, lambda o: o.c))
    c_prev = pd.Series(mtf(df, coin, 60, 1440, lambda o: o.c.shift()))
    buying = (c_today - c_prev) / c_prev > 0
    return dict(le=B(buying), se=B(~buying))


def vwap_anchor(df, period_ms, src=None, offset_ms=0):
    src = (df.h + df.l + df.c) / 3 if src is None else src
    g = (df.t.values - offset_ms) // period_ms
    pv = (src * df.v).groupby(g).cumsum(); vv = df.v.groupby(g).cumsum()
    vw = pv / vv
    var = (df.v * src * src).groupby(g).cumsum() / vv - vw * vw
    return vw, np.sqrt(var.clip(lower=0)), g


@reg('周VWAP+斐波那契偏离', 60, 8822242, 5088)
def s_vwap_fibo(df):
    # 周 VWAP（周一 00:00 UTC 开始）；原版按递推写的标准差 ≈ 成交量加权标准差
    vw, sd, _ = vwap_anchor(df, 7 * 86_400_000, offset_ms=4 * 86_400_000)
    p1, p2, m1, m2 = vw + 1.618 * sd, vw + 2.618 * sd, vw - 1.618 * sd, vw - 2.618 * sd
    l, h = df.l, df.h
    ok = sd / df.c > 0.015            # 原版 "sd > 150"（BTC 美元）换成相对值 1.5%
    bull = P.cross_under(l.shift(), m1.shift()) & (l.shift() >= m2.shift()) & (l > m2) & (l < m1) & ok
    bear = P.cross_over(h.shift(), p1.shift()) & (h.shift() <= p2.shift()) & (h < p2) & (h > p1) & ok
    return dict(le=B(bull), se=B(bear), lx=B(P.cross_over(h, vw) | P.cross_under(l, m2)), sx=B(P.cross_under(l, vw) | P.cross_over(h, p2)))


@reg('VWAP 趋势跟随(wbburgin)', 15, 15231464, 4539)
def s_vwap_trend(df):
    vw, sd, _ = vwap_anchor(df, 86_400_000)
    u1, l1, u2, l2 = vw + sd * 0.88, vw - sd * 0.88, vw + 2 * sd * 0.88, vw - 2 * sd * 0.88
    up = pd.Series(_trend_up(vw.values) > 0)
    return dict(le=B(P.cross_over(df.c, u1) & up), se=B(P.cross_under(df.c, l1) & ~up),
                lx=B(P.cross_under(df.c, u2)), sx=B(P.cross_over(df.c, l2)))


@njit(cache=True)
def _adapt_len(charged, lo, hi, pct):
    n = len(charged); out = np.zeros(n); L = (lo + hi) / 2
    for i in range(n):
        L = max(lo, L * (1 - pct)) if charged[i] else min(hi, L * (1 + pct))
        out[i] = L
    return out


@njit(cache=True)
def _wma_var(x, lens):
    n = len(x); out = np.full(n, np.nan)
    for i in range(n):
        L = int(lens[i])
        if L < 1 or i - L + 1 < 0: continue
        s = 0.0; w = 0.0; bad = False
        for k in range(L):
            v = x[i - L + 1 + k]
            if np.isnan(v): bad = True; break
            s += v * (k + 1); w += k + 1
        if not bad: out[i] = s / w
    return out


def xhma_var(src, lens):
    lens = np.floor(lens).astype(np.int64)
    a = _wma_var(src, np.floor(lens / 2)); b = _wma_var(src, lens.astype(float))
    return _wma_var(2 * a - b, np.floor(np.sqrt(lens)))


@reg('72s 自适应HMA+', 15, 10392609, 4751)
def s_72s(df):
    c, h, l = df.c, df.h, df.l
    ab = P.atr(h, l, c, 21); r = P.rsi(c, 14)
    charged = B(P.atr(h, l, c, 14) > P.atr(h, l, c, 46))
    dl = _adapt_len(charged, 172.0, 233.0, 0.03141); ml = _adapt_len(charged, 89.0, 121.0, 0.03141)
    dh = pd.Series(xhma_var(c.values, dl)); mh = pd.Series(xhma_var(c.values, ml))
    hh, ll = P.highest(h, 34), P.lowest(l, 34); sr = 25 / (hh - ll) * ll
    dt = (dh.shift(2) - dh) / c * sr
    ang = np.round(180 * np.arccos(1 / np.sqrt(1 + dt * dt)) / np.pi); slope = np.where(dt > 0, -ang, ang)
    slope = pd.Series(slope)
    a40 = P.atr(h, l, c, 40); up_tl, lo_tl = dh + 2.7 * a40, dh - 2.7 * a40
    st_up, st_lo = dh + 1.35 * a40, dh - 1.35 * a40
    upS = (slope >= 17) & pd.Series(charged); dnS = (slope <= -17) & pd.Series(charged)
    fu = upS & ~upS.shift().fillna(False).astype(bool); fd = dnS & ~dnS.shift().fillna(False).astype(bool)
    buy = fu & (c > dh) & (l <= up_tl) & (r > 51) & (r <= 70)
    sell = fd & (c < dh) & (h >= lo_tl) & (r < 49) & (r >= 30)
    obuy = fu & (r > 70) & (c > dh) & ((h + l) / 2 > up_tl) & (mh > dh)
    osell = fd & (r < 30) & (c < dh) & ((h + l) / 2 < lo_tl) & (mh < dh)
    mx = 4.5 * ab
    sl_b = np.maximum(lo_tl, l - mx); sl_s = np.minimum(up_tl, h + mx)       # Half Distance Zone，最多 4.5ATR
    tp_b, tp_s = h + 3 * ab, l - 3 * ab
    # 平仓：赚够 1ATR 后 RSI 转弱或跌破 HMA；或者强势单的条件（原版两个标记启动后一直为 1，这里两组条件都用）
    lxp = ((P.cross_under(r, 70) | (r < 50)) | P.cross_under(c, dh)) | P.cross_under(r, 45) | ((df.o < st_up) & ((h + l) / 2 < st_up))
    sxp = ((P.cross_over(r, 30) | (r > 50)) | P.cross_over(c, dh)) | P.cross_over(r, 55) | ((df.o > st_lo) & ((h + l) / 2 > st_lo))
    lx = (df.o < dh) & (r < 60); sx = (df.o > dh) & (r > 40)
    return dict(le=B(buy | obuy), se=B(sell | osell), lx=B(lx), sx=B(sx), lxp=B(lxp), sxp=B(sxp), pmin=ab.values,
                slp=(sl_b.values, sl_s.values), tpp=(tp_b.values, tp_s.values))


@njit(cache=True)
def _qqe(rsx, dar):
    n = len(rsx); lb = np.zeros(n); sb = np.zeros(n); tr = np.ones(n); tl = np.zeros(n)
    for i in range(n):
        if np.isnan(rsx[i]) or np.isnan(dar[i]):
            tl[i] = np.nan; continue
        nsb = rsx[i] + dar[i]; nlb = rsx[i] - dar[i]
        p_lb = lb[i - 1] if i > 0 else 0.0; p_sb = sb[i - 1] if i > 0 else 0.0
        r1 = rsx[i - 1] if i > 0 else np.nan
        lb[i] = max(p_lb, nlb) if (r1 > p_lb and rsx[i] > p_lb) else nlb
        sb[i] = min(p_sb, nsb) if (r1 < p_sb and rsx[i] < p_sb) else nsb
        # cross(RSIndex, shortband[1])：RSIndex 和上一根 shortband 的序列交叉
        sb2 = sb[i - 2] if i > 1 else 0.0; lb2 = lb[i - 2] if i > 1 else 0.0
        c1 = i > 1 and ((rsx[i] > p_sb) != (r1 > sb2)) and not np.isnan(r1)
        c2 = i > 1 and ((p_lb > rsx[i]) != (lb2 > r1)) and not np.isnan(r1)
        t = tr[i - 1] if i > 0 else 1.0
        if c1: t = 1.0
        elif c2: t = -1.0
        tr[i] = t
        tl[i] = lb[i] if t == 1 else sb[i]
    return tl


def _qqe_line(c, rp, sf, q):
    r = P.rsi(c, rp); rm = P.ema(r, sf)
    wp = rp * 2 - 1
    dar = P.ema(P.ema((rm.shift() - rm).abs(), wp), wp) * q
    return rm, pd.Series(_qqe(rm.values, dar.values))


@reg('QQE MOD+SSL Hybrid+WAE 三指标', 60, 13127575, 5245)
def s_qqe_ssl_wae(df):
    c, h, l = df.c, df.h, df.l
    hma = lambda x, n: P.wma(2 * P.wma(x, n // 2) - P.wma(x, n), int(round(np.sqrt(n))))
    rm, tl = _qqe_line(c, 6, 6, 3.0); rm2, _ = _qqe_line(c, 6, 5, 1.61)
    b = P.sma(tl - 50, 50); d = 0.35 * P.stdev(tl - 50, 50)
    green = (rm2 - 50 > 3) & (rm - 50 > b + d); red = (rm2 - 50 < -3) & (rm - 50 < b - d)
    qb = green & ~green.shift().fillna(False).astype(bool); qs = red & ~red.shift().fillna(False).astype(bool)
    bbmc = hma(c, 60); rg = P.ema(P.tr(h, l, c), 60); uk, lk = bbmc + rg * 0.2, bbmc - rg * 0.2
    sb, ss = (c > uk) & (c > bbmc), (c < lk) & (c < bbmc)
    m = P.ema(c, 20) - P.ema(c, 40); t1 = (m - m.shift()) * 180
    e1 = 2 * 2.0 * P.stdev(c, 20)
    wb = (t1 > 0) & (t1 > e1); ws = (-t1 > 0) & (-t1 > e1)
    eh, el = hma(h, 15), hma(l, 15)
    hlv = pd.Series(np.where(c > eh, 1.0, np.where(c < el, -1.0, np.nan))).ffill()
    ssl_exit = pd.Series(np.where(hlv < 0, eh, el))
    xl = P.cross_over(ssl_exit, c); xs = P.cross_over(c, ssl_exit)
    # 止损 = 最近 10 根最低 / 最高（每根更新）；止盈 = 反向穿越退出线且这单在赚钱
    return dict(le=B(qb & sb & wb), se=B(qs & ss & ws), slp=(P.lowest(l, 10).values, P.highest(h, 10).values), dyn=True,
                lxp=B(xl), sxp=B(xs), pmin=0.0)


@njit(cache=True)
def _cap_per_cross(cond, xev, cap):
    n = len(cond); out = np.zeros(n, np.bool_); cnt = 0
    for i in range(n):
        if xev[i]: cnt = 0
        if cond[i] and cnt < cap:
            out[i] = True; cnt += 1
    return out


@reg('Hulk 剥头皮(原x35杠杆5分钟)', 5, 13034113, 2172)
def s_hulk(df):
    c = df.c; r = P.rsi(c, 20) - 50
    _, _, adx = P.dmi(df.h, df.l, c, 14, 14)
    _, _, mh = P.macd(c, 12, 26, 11)           # 原版用 1 分钟 MACD 柱，这里只有 5 分钟
    th = 5.45 / 40000 * c                       # 原版"5.45 美元"(BTC) 换成相对价格 ≈ 0.0136%
    ef, es = P.ema(c, 50), P.ema(c, 200)
    xev = B(P.cross(ef, es))
    lc = (c > es) & (mh < -th) & (adx > 33) & (ef > es) & (r < 2) & (r > -2)
    sc = (c < es) & (mh > th) & (adx > 33) & (ef < es) & (r < 2) & (r > -2)
    le = _cap_per_cross(B(lc), xev, 6); se = _cap_per_cross(B(sc), xev, 6)
    return dict(le=le, se=se, tpp=((c * 1.0135).values, (c * 0.9865).values), slp=((c * 0.989).values, (c * 1.011).values))
