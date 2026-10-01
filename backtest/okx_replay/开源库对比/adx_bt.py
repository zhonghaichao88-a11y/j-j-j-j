"""ADXMomentum + BTC 大盘过滤（只做多）的独立回测，规则照 freqtrade 原策略：
指标（1小时）：ADX(14)、+DI(25)、−DI(25)、MOM(14)（TA-Lib，与原策略同）。
进场：ADX>25 且 MOM>0 且 +DI>25 且 +DI>−DI，且 BTC 前一根已收盘日线 收盘>EMA50（至少有50根日线）；信号K收盘后下一根开盘价进场。
出场（每根K线内按 freqtrade 顺序）：止损 −25%（最低价触及）→ 止盈 ROI 1%（按含手续费的净收益 1% 反推价格，最高价触及）→
      出场信号（ADX>25 且 MOM<0 且 −DI>25 且 +DI<−DI）下一根开盘价出。
资金：固定每笔 100U，最多同时 10 仓（同一时刻按币列表顺序先到先得），手续费单边 fee。
用法：run(frames: {币: {ts,open,high,low,close}}, btc_daily: {ts, close}, fee, roi, adx_th, ...) → 交易列表"""
import numpy as np, pandas as pd, talib

H = 3600000; D = 86400000
STRICT = [False]
FILTER = [None]    # 可选：f -> (允许做多, 允许做空) 两个布尔数组（只能用已收盘数据）   # True = 盘中腾出的仓位要到下一根才能用（无前视）


def btc_up(btc_daily, ts_h):
    """每个1小时K线开盘时刻可用的 BTC 状态：前一根已收盘日线 close>EMA50。"""
    c = pd.Series(btc_daily['close']); e = c.ewm(span=50, adjust=False).mean()
    up = ((c > e) & (np.arange(len(c)) >= 50)).values; dts = np.asarray(btc_daily['ts'])
    idx = np.searchsorted(dts + D, ts_h, side='right') - 1          # 日线收盘时刻 <= 该小时开盘
    return np.where(idx >= 0, up[np.clip(idx, 0, None)], False)


def btc_down(btc_daily, ts_h):
    c = pd.Series(btc_daily['close']); e = c.ewm(span=50, adjust=False).mean()
    dn = ((c <= e) & (np.arange(len(c)) >= 50)).values; dts = np.asarray(btc_daily['ts'])
    idx = np.searchsorted(dts + D, ts_h, side='right') - 1
    return np.where(idx >= 0, dn[np.clip(idx, 0, None)], False)


def mirror(f):
    """价格上下翻转（1/价格）：翻转后的做多信号 = 原价格上的做空信号（与 freqtrade 多空版 regime_variants 相同）。"""
    return dict(high=1 / np.asarray(f['low'], float), low=1 / np.asarray(f['high'], float), close=1 / np.asarray(f['close'], float))


def signals(f, adx_th=25, di_th=25):
    h, l, c = (np.asarray(f[k], float) for k in ('high', 'low', 'close'))
    adx = talib.ADX(h, l, c, 14); pdi = talib.PLUS_DI(h, l, c, 25); mdi = talib.MINUS_DI(h, l, c, 25); mom = talib.MOM(c, 14)
    ent = (adx > adx_th) & (mom > 0) & (pdi > di_th) & (pdi > mdi)
    ex = (adx > adx_th) & (mom < 0) & (mdi > di_th) & (pdi < mdi)
    return np.nan_to_num(ent).astype(bool), np.nan_to_num(ex).astype(bool)


def run(frames, btc_daily, fee=0.001, roi=0.01, sl=0.25, adx_th=25, di_th=25, max_open=10, t0=None, t1=None, use_btc=True, stake=100, wallet=1000, short=False, delay=0, max_hold=0):
    pairs = sorted(frames); prep = {}; bal = [float(wallet)]   # 可用余额：亏损后可能不够开满 10 仓（与 freqtrade 一致）
    for p in pairs:
        f = frames[p]; ts = np.asarray(f['ts'], np.int64)
        ent, ex = signals(f, adx_th, di_th)
        if use_btc: ent = ent & btc_up(btc_daily, ts + H)                # 信号K收盘时刻 = 下一根开盘
        lo_ok = so_ok = None
        if FILTER[0] is not None: lo_ok, so_ok = FILTER[0](f); ent = ent & lo_ok
        if short:
            sent, sex = signals(mirror(f), adx_th, di_th)
            if use_btc: sent = sent & btc_down(btc_daily, ts + H)
            if so_ok is not None: sent = sent & so_ok
        else:
            sent = sex = np.zeros(len(ts), bool)
        prep[p] = dict(ts=ts, o=np.asarray(f['open'], float), h=np.asarray(f['high'], float), l=np.asarray(f['low'], float),
                       ent=ent, ex=ex, sent=sent, sex=sex, pos=None, i=0)
    allts = np.unique(np.concatenate([prep[p]['ts'] for p in pairs]))
    if t0: allts = allts[allts >= t0]
    if t1: allts = allts[allts < t1]
    index = {p: {int(t): i for i, t in enumerate(prep[p]['ts'])} for p in pairs}
    open_n = 0; trades = []

    def close(p, P, pos, t, px, why):
        e = pos['entry']
        if pos['d'] == 1: r = (px * (1 - fee) - e * (1 + fee)) / (e * (1 + fee))
        else: r = 1 - (px * (1 + fee)) / (e * (1 - fee))                 # freqtrade 空单收益口径
        trades.append(dict(pair=p, t=pos['t'], exit_t=int(t), r=r, why=why, d=pos['d']))
        P['pos'] = None; P['last_exit'] = int(t); bal[0] += stake * (1 + r)

    def manage(p, P, i, t):
        """本根K线内的持仓处理（与 freqtrade 回测顺序一致）；返回是否平仓。"""
        pos = P['pos']; e = pos['entry']; d = pos['d']
        if pos['pending_exit']: close(p, P, pos, t, P['o'][i], '信号'); return True
        if max_hold and int(t) - pos['t'] >= max_hold * H: close(p, P, pos, t, P['o'][i], '超时'); return True
        if d == 1:
            stop = e * (1 - sl)
            if P['l'][i] <= stop: close(p, P, pos, t, min(stop, P['o'][i]), '止损'); return True
            tp = e * (1 + fee) * (1 + roi) / (1 - fee)
            if P['h'][i] >= tp: close(p, P, pos, t, (max(tp, P['o'][i]) if int(t) != pos['t'] else tp), '止盈'); return True
            if P['ex'][i]: pos['pending_exit'] = True
        else:
            stop = e * (1 + sl)
            if P['h'][i] >= stop: close(p, P, pos, t, max(stop, P['o'][i]), '止损'); return True
            tp = e * (1 - fee) * (1 - roi) / (1 + fee)
            if P['l'][i] <= tp: close(p, P, pos, t, (min(tp, P['o'][i]) if int(t) != pos['t'] else tp), '止盈'); return True
            if P['sex'][i]: pos['pending_exit'] = True
        return False

    for t in allts:
        live = [(p, index[p].get(int(t))) for p in pairs]
        live = [(p, i) for p, i in live if i is not None]
        if STRICT[0]:
            # 严格口径：开盘时只有“开盘就平掉的仓”（上一根出场信号）能腾出位置；本小时盘中止盈止损腾出的位置，要到下一根才能用
            for p, i in live:
                P = prep[p]
                if P['pos'] is not None and (P['pos']['pending_exit'] or (max_hold and int(t) - P['pos']['t'] >= max_hold * H)):
                    close(p, P, P['pos'], t, P['o'][i], '信号' if P['pos']['pending_exit'] else '超时'); open_n -= 1
            slots = max_open - open_n; new = []
            for p, i in live:
                P = prep[p]
                kk = i - 1 - delay
                if slots > 0 and P['pos'] is None and kk >= 0 and (P['ent'][kk] or P['sent'][kk]) and (t0 is None or P['ts'][kk] >= t0) \
                        and P.get('last_exit') != int(t) and bal[0] >= stake:
                    new.append((p, i)); slots -= 1
            for p, i in live:
                P = prep[p]
                if P['pos'] is not None and manage(p, P, i, t): open_n -= 1
            for p, i in new:
                P = prep[p]; kk = i - 1 - delay
                P['pos'] = dict(entry=P['o'][i], t=int(t), pending_exit=False, d=1 if P['ent'][kk] else -1); open_n += 1; bal[0] -= stake
                if manage(p, P, i, t): open_n -= 1
            continue
        # 1) 先处理已有持仓（腾出仓位）
        for p, i in live:
            P = prep[p]
            if P['pos'] is not None and manage(p, P, i, t): open_n -= 1
        # 2) 再按币列表顺序开新仓；新仓在本根K线内也要检查止损/止盈
        for p, i in live:
            P = prep[p]
            k = i - 1 - delay                                           # delay=1：信号后再晚一根K线进场
            if P['pos'] is None and k >= 0 and (P['ent'][k] or P['sent'][k]) and open_n < max_open and (t0 is None or P['ts'][k] >= t0) \
                    and P.get('last_exit') != int(t) and bal[0] >= stake:
                P['pos'] = dict(entry=P['o'][i], t=int(t), pending_exit=False, d=1 if P['ent'][k] else -1); open_n += 1; bal[0] -= stake
                if manage(p, P, i, t): open_n -= 1
    for p in pairs:
        pos = prep[p]['pos']
        if pos: trades.append(dict(pair=p, t=pos['t'], exit_t=int(prep[p]['ts'][-1]), r=0.0, why='结束', d=pos['d']))
    return pd.DataFrame(trades)


def summary(T, stake=100, wallet=1000):
    if T.empty: return dict(笔数=0)
    r = T.r.values; pnl = (r * stake); eq = np.cumsum(pnl[np.argsort(T.exit_t.values)])
    dd = (np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]).max()
    return dict(笔数=len(r), 胜率=f'{(r > 0).mean() * 100:.0f}%', 每笔=f'{r.mean() * 100:+.3f}%', 收益=f'{pnl.sum() / wallet * 100:+.1f}%',
                最大回撤=f'{dd / wallet * 100:.1f}%', 最大单亏=f'{r.min() * 100:.1f}%', 止损次数=int((T.why == '止损').sum()), 空单=int((T.get('d', pd.Series([1] * len(T))) == -1).sum()))
