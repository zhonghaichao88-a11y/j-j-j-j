"""按 TradingView 策略的规则逐根模拟（不看未来）：
- 信号在第 i 根收盘算出，第 i+1 根开盘价成交（TV 默认 process_orders_on_close=false）
- 反向信号 = 先平再反手（strategy.entry 默认行为）；平仓信号同样下一根开盘成交
- strategy.exit 的止损 / 止盈：
    sl / tp      = 按进场价的百分比
    slp / tpp    = 绝对价格（信号那根算出来的；dyn=True 时每根用上一根收盘算出的最新值，比如移动止损线）
    trail        = 追踪止损（离最高 / 最低价的百分比）
  K线内触发；同一根先算止损（往坏处算）；跳空越过就按开盘价
- 每边扣 吃单 0.05% + 滑点 0.02%；止损再多扣 0.05%
- 一个币同时只拿一单（pyramiding=1），只算每笔收益率"""
import numpy as np
import numba

FEE, SLIP, STOP_SLIP = 0.0005, 0.0002, 0.0005


def _arr(x, n):
    if x is None:
        return np.full(n, np.nan)
    if np.isscalar(x):
        return np.full(n, float(x))
    return np.asarray(x, float)


def _pair(x, n):
    """(多单用, 空单用)"""
    if isinstance(x, tuple):
        return _arr(x[0], n), _arr(x[1], n)
    a = _arr(x, n)
    return a, a


@numba.njit(cache=True)
def _run(O, H, L, C, le, se, lx, sx, SLl, SLs, TPl, TPs, SPl, SPs, TQl, TQs, dyn, TR, max_bars, fee, slip, sslip,
         ESl, ESs, PT, PF, pexit, TA, xbp, LXP, SXP, PMIN):
    """ESl/ESs：进场停损单价格（NaN = 市价）；PT/PF：分批止盈（距离进场价比例 / 平掉原仓位的比例），最后剩下的仓位走 止损/止盈/信号"""
    n = len(O)
    res = np.zeros((n, 6))          # t_in idx, t_out idx, side, ret, why, bars
    k = 0
    npt = len(PT)
    pos = 0; e = 0.0; j0 = 0; stp = np.nan; tgt = np.nan; ext = 0.0; trl = np.nan
    rem = 1.0; acc = 0.0; done = np.zeros(max(npt, 1), np.bool_)
    for i in range(n - 1):
        j = i + 1
        want = 0
        if le[i] and se[i]:
            want = -pos if pos != 0 else 1          # 多空停损单同时挂着：有仓位时只有反方向那张有意义
            if pos == 0 and ESl[i] == ESl[i] and ESs[i] == ESs[i]:
                want = 1 if H[j] >= ESl[i] else (-1 if L[j] <= ESs[i] else 1)   # 空仓时看哪张先被碰到（都碰到按多单）
        elif le[i]:
            want = 1
        elif se[i]:
            want = -1
        xl = lx[i] and (not pexit or C[i] > e)       # pexit：平仓信号只在赚钱时才平（Noro 系列写法）
        xs = sx[i] and (not pexit or C[i] < e)
        if pos == 1 and LXP[i] and C[i] >= e + PMIN[i]: xl = True     # 赚够 PMIN 才平的信号
        if pos == -1 and SXP[i] and C[i] <= e - PMIN[i]: xs = True
        if xbp > 0 and pos != 0 and i - j0 + 1 >= xbp:   # xbp：拿满 N 根且在赚钱就平
            if pos == 1 and C[i] > e: xl = True
            if pos == -1 and C[i] < e: xs = True
        if pos != 0 and ((pos == 1 and (xl or want == -1)) or (pos == -1 and (xs or want == 1))):
            acc += rem * pos * (O[j] / e - 1)
            res[k, 0] = j0; res[k, 1] = j; res[k, 2] = pos
            res[k, 3] = acc - 2 * (fee + slip); res[k, 4] = 0; res[k, 5] = j - j0 + 1
            k += 1; pos = 0
        if pos == 0 and want != 0:
            lvl = ESl[i] if want == 1 else ESs[i]
            px = O[j]
            ok = True
            if lvl == lvl:                   # 停损单进场：多单价格涨到 lvl 才买（开盘已越过就按开盘价）
                if want == 1:
                    if H[j] >= lvl: px = max(O[j], lvl)
                    else: ok = False
                else:
                    if L[j] <= lvl: px = min(O[j], lvl)
                    else: ok = False
            if ok:
                pos = want; e = px; j0 = j; rem = 1.0; acc = 0.0
                for q in range(npt): done[q] = False
                if pos == 1:
                    stp = e * (1 - SLl[i]) if SLl[i] > 0 else (SPl[i] if SPl[i] > 0 else np.nan)
                    tgt = e * (1 + TPl[i]) if TPl[i] > 0 else (TQl[i] if TQl[i] > 0 else np.nan)
                else:
                    stp = e * (1 + SLs[i]) if SLs[i] > 0 else (SPs[i] if SPs[i] > 0 else np.nan)
                    tgt = e * (1 - TPs[i]) if TPs[i] > 0 else (TQs[i] if TQs[i] > 0 else np.nan)
                trl = TR[i]; ext = e
        elif pos != 0 and dyn:
            if pos == 1:
                if SPl[i] > 0: stp = SPl[i]
                if TQl[i] > 0: tgt = TQl[i]
            else:
                if SPs[i] > 0: stp = SPs[i]
                if TQs[i] > 0: tgt = TQs[i]
        if pos != 0:
            s_eff = stp
            act = TA[j0 - 1] if j0 > 0 else np.nan          # 追踪止损启动条件：浮盈先到 act 才开始追
            armed = not (act > 0) or (pos == 1 and ext >= e * (1 + act)) or (pos == -1 and ext <= e * (1 - act))
            if trl >= 0 and armed and not np.isnan(trl):
                ts = ext * (1 - trl) if pos == 1 else ext * (1 + trl)
                if np.isnan(s_eff) or (pos == 1 and ts > s_eff) or (pos == -1 and ts < s_eff):
                    s_eff = ts
            hit = False
            if not np.isnan(s_eff) and ((pos == 1 and L[j] <= s_eff) or (pos == -1 and H[j] >= s_eff)):
                px = min(O[j], s_eff) if pos == 1 else max(O[j], s_eff)
                acc += rem * pos * (px / e - 1)
                res[k, 0] = j0; res[k, 1] = j; res[k, 2] = pos
                res[k, 3] = acc - 2 * (fee + slip) - sslip * rem; res[k, 4] = 1; res[k, 5] = j - j0 + 1
                k += 1; pos = 0; hit = True
            else:
                for q in range(npt):          # 分批止盈（挂单）
                    if not done[q] and rem > 1e-9:
                        lv = e * (1 + pos * PT[q])
                        if (pos == 1 and H[j] >= lv) or (pos == -1 and L[j] <= lv):
                            f = min(PF[q], rem)
                            acc += f * pos * (lv / e - 1); rem -= f; done[q] = True
                if rem <= 1e-9:
                    res[k, 0] = j0; res[k, 1] = j; res[k, 2] = pos
                    res[k, 3] = acc - 2 * (fee + slip); res[k, 4] = 2; res[k, 5] = j - j0 + 1
                    k += 1; pos = 0; hit = True
                elif not np.isnan(tgt) and ((pos == 1 and H[j] >= tgt) or (pos == -1 and L[j] <= tgt)):
                    px = max(O[j], tgt) if pos == 1 else min(O[j], tgt)
                    acc += rem * pos * (px / e - 1)
                    res[k, 0] = j0; res[k, 1] = j; res[k, 2] = pos
                    res[k, 3] = acc - 2 * (fee + slip); res[k, 4] = 2; res[k, 5] = j - j0 + 1
                    k += 1; pos = 0; hit = True
                elif max_bars > 0 and j - j0 + 1 >= max_bars:
                    acc += rem * pos * (C[j] / e - 1)
                    res[k, 0] = j0; res[k, 1] = j; res[k, 2] = pos
                    res[k, 3] = acc - 2 * (fee + slip); res[k, 4] = 3; res[k, 5] = j - j0 + 1
                    k += 1; pos = 0; hit = True
            if not hit and pos != 0:
                ext = max(ext, H[j]) if pos == 1 else min(ext, L[j])
    return res[:k]


WHY = {0: '信号平仓', 1: '止损', 2: '止盈', 3: '到时间'}


def run(df, le, se=None, lx=None, sx=None, sl=None, tp=None, slp=None, tpp=None, dyn=False, trail=None, max_bars=0,
        long_only=False, short_only=False, es=None, ptp=(), pexit=False, tact=None, xbp=0, lxp=None, sxp=None, pmin=None):
    """es：进场停损单价格 (多, 空)；ptp：分批止盈 [(距离, 比例), ...]"""
    n = len(df)
    b = lambda x: np.zeros(n, np.bool_) if x is None else np.nan_to_num(np.asarray(x, float)).astype(np.bool_)
    le_, se_, lx_, sx_ = b(le), b(se), b(lx), b(sx)
    if long_only:
        sx_ = sx_ | se_                # 只做多：做空信号当成平多（TV 里 strategy.risk.allow_entry_in(long) 的效果）
        lx_ = lx_ | se_
        se_ = np.zeros(n, np.bool_)
    if short_only:
        lx_ = lx_ | le_
        le_ = np.zeros(n, np.bool_)
    SLl, SLs = _pair(sl, n); TPl, TPs = _pair(tp, n); SPl, SPs = _pair(slp, n); TQl, TQs = _pair(tpp, n)
    TR = _arr(trail, n)
    v = lambda k: df[k].values.astype(float)
    return _run(v('o'), v('h'), v('l'), v('c'), le_, se_, lx_, sx_, SLl, SLs, TPl, TPs, SPl, SPs, TQl, TQs,
                bool(dyn), TR, int(max_bars or 0), FEE, SLIP, STOP_SLIP, *_pair(es, n),
                np.array([a for a, _ in ptp], float), np.array([b for _, b in ptp], float), bool(pexit),
                _arr(tact, n), int(xbp or 0), b(lxp), b(sxp), np.nan_to_num(_arr(pmin, n)))
