"""FSupertrendStrategy 多空 + BTC 大盘过滤的独立回测（规则照 freqtrade 原策略，参数不改）。
指标（1小时）：supertrend（technical.indicators 同算法：TA-Lib TRANGE、ATR=TR 的简单均值）。
进场做多：ST(4,8)、ST(7,9)、ST(1,8) 三条都向上（倍数,周期）；做空=价格镜像(1/价格)后的同样条件；BTC 前一根日线 收盘>EMA50 只做多，≤ 只做空。
出场（freqtrade 顺序）：固定止损 26.5% → ROI 止盈（持仓第0根 10%、第1根 5%、第2根起 2.5%，均为扣费后净收益）→ 移动止损（从最高价回撤 26.5%，盈利超过 10% 后回撤 5%）
      → 出场信号 ST(3,18) 向下（空单取镜像）下一根开盘平。freqtrade 规则：同根K线进出场信号同时出现不进场；持仓中进场信号仍在时忽略出场信号。资金：固定每笔100U、最多10仓、币按名称顺序，手续费单边 fee。"""
import numpy as np, pandas as pd
from numba import njit
import adx_bt as A
H = A.H; D = A.D
BUY = ((4, 8), (7, 9), (1, 8)); EXIT = (3, 18)
SL = 0.265; TRAIL_POS = 0.05; TRAIL_OFF = 0.10


def roi_at(k):
    return 0.10 if k == 0 else (0.05 if k == 1 else 0.025)


@njit(cache=True)
def _st(h, l, c, atr, period, mult):
    n = len(c); bub = (h + l) / 2 + mult * atr; blb = (h + l) / 2 - mult * atr
    fub = np.zeros(n); flb = np.zeros(n); st = np.zeros(n)
    for i in range(period, n):
        fub[i] = bub[i] if (bub[i] < fub[i - 1] or c[i - 1] > fub[i - 1]) else fub[i - 1]
        flb[i] = blb[i] if (blb[i] > flb[i - 1] or c[i - 1] < flb[i - 1]) else flb[i - 1]
    for i in range(period, n):
        if st[i - 1] == fub[i - 1]:
            st[i] = fub[i] if c[i] <= fub[i] else flb[i]
        elif st[i - 1] == flb[i - 1]:
            st[i] = flb[i] if c[i] >= flb[i] else fub[i]
    out = np.zeros(n, np.int8)
    for i in range(n):
        if st[i] > 0: out[i] = -1 if c[i] < st[i] else 1
    return out


def st_dir(h, l, c, period, mult):
    """1=up, -1=down, 0=未形成（与 technical.indicators.supertrend 的 stx 相同）。"""
    tr = np.empty(len(c)); tr[0] = np.nan
    tr[1:] = np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])])
    atr = pd.Series(tr).rolling(period).mean().to_numpy()
    return _st(h, l, c, atr, int(period), float(mult))


def signals(f):
    h, l, c = (np.asarray(f[k], float) for k in ('high', 'low', 'close'))
    ent = np.ones(len(c), bool)
    for m, p in BUY: ent &= st_dir(h, l, c, p, m) == 1
    ex = st_dir(h, l, c, EXIT[1], EXIT[0]) == -1
    return ent, ex


def run(frames, btc_daily, fee=0.001, max_open=10, t0=None, t1=None, stake=100, wallet=1000, short=True, delay=0, use_btc=True):
    pairs = sorted(frames); prep = {}; bal = [float(wallet)]
    for p in pairs:
        f = frames[p]; ts = np.asarray(f['ts'], np.int64)
        ent, ex = signals(f)
        if use_btc: ent = ent & A.btc_up(btc_daily, ts + H)
        if short:
            sent, sex = signals(A.mirror(f)); sent = sent & A.btc_down(btc_daily, ts + H)
        else:
            sent = sex = np.zeros(len(ts), bool)
        # freqtrade 规则：同一根K线既有进场又有出场信号 → 不进场；持仓中进场信号还在 → 出场信号不算
        lex = ex & ~ent; ssx = sex & ~sent
        ent = ent & ~ex & ~sent; sent = sent & ~sex & ~ent
        ex, sex = lex, ssx
        prep[p] = dict(ts=ts, o=np.asarray(f['open'], float), h=np.asarray(f['high'], float), l=np.asarray(f['low'], float),
                       ent=ent, ex=ex, sent=sent, sex=sex, pos=None)
    allts = np.unique(np.concatenate([prep[p]['ts'] for p in pairs]))
    if t0: allts = allts[allts >= t0]
    if t1: allts = allts[allts < t1]
    index = {p: {int(t): i for i, t in enumerate(prep[p]['ts'])} for p in pairs}
    open_n = 0; trades = []

    def close(p, P, pos, t, px, why):
        e = pos['entry']
        r = (px * (1 - fee) - e * (1 + fee)) / (e * (1 + fee)) if pos['d'] == 1 else 1 - (px * (1 + fee)) / (e * (1 - fee))
        trades.append(dict(pair=p, t=pos['t'], exit_t=int(t), r=r, why=why, d=pos['d']))
        P['pos'] = None; P['last_exit'] = int(t); bal[0] += stake * (1 + r)

    def manage(p, P, i, t):
        pos = P['pos']; e = pos['entry']; d = pos['d']; k = pos['k']; o = P['o'][i]; first = int(t) == pos['t']
        if pos['pending_exit']: close(p, P, pos, t, o, '信号'); return True
        if d == 1:
            pos['hwm'] = max(pos['hwm'], P['h'][i])
            dist = TRAIL_POS if pos['hwm'] / e - 1 > TRAIL_OFF else SL
            pos['stop'] = max(pos['stop'], pos['hwm'] * (1 - dist))
            hit = P['l'][i] <= pos['stop']; trailing = pos['stop'] > pos['stop0']
            # freqtrade 顺序：固定止损 → ROI 止盈 → 移动止损
            if hit and not trailing: close(p, P, pos, t, min(pos['stop'], o) if not first else pos['stop'], '止损'); return True
            tp = e * (1 + fee) * (1 + roi_at(k)) / (1 - fee)
            if P['h'][i] >= tp: close(p, P, pos, t, max(tp, o) if not first else tp, '止盈'); return True
            if hit: close(p, P, pos, t, min(pos['stop'], o) if not first else pos['stop'], '移动止损'); return True
            if P['ex'][i]: pos['pending_exit'] = True
        else:
            pos['lwm'] = min(pos['lwm'], P['l'][i])
            dist = TRAIL_POS if 1 - pos['lwm'] / e > TRAIL_OFF else SL
            pos['stop'] = min(pos['stop'], pos['lwm'] * (1 + dist))
            hit = P['h'][i] >= pos['stop']; trailing = pos['stop'] < pos['stop0']
            if hit and not trailing: close(p, P, pos, t, max(pos['stop'], o) if not first else pos['stop'], '止损'); return True
            tp = e * (1 - fee) * (1 - roi_at(k)) / (1 + fee)
            if P['l'][i] <= tp: close(p, P, pos, t, min(tp, o) if not first else tp, '止盈'); return True
            if hit: close(p, P, pos, t, max(pos['stop'], o) if not first else pos['stop'], '移动止损'); return True
            if P['sex'][i]: pos['pending_exit'] = True
        pos['k'] = k + 1
        return False

    for t in allts:
        live = [(p, index[p].get(int(t))) for p in pairs]
        live = [(p, i) for p, i in live if i is not None]
        for p, i in live:
            P = prep[p]
            if P['pos'] is not None and manage(p, P, i, t): open_n -= 1
        for p, i in live:
            P = prep[p]
            kk = i - 1 - delay
            if P['pos'] is None and kk >= 0 and (P['ent'][kk] or P['sent'][kk]) and open_n < max_open and (t0 is None or P['ts'][kk] >= t0) \
                    and P.get('last_exit') != int(t) and bal[0] >= stake:
                d = 1 if P['ent'][kk] else -1; e = P['o'][i]
                P['pos'] = dict(entry=e, t=int(t), pending_exit=False, d=d, k=0, hwm=e, lwm=e, stop=e * (1 - SL) if d == 1 else e * (1 + SL))
                P['pos']['stop0'] = P['pos']['stop']
                open_n += 1; bal[0] -= stake
                if manage(p, P, i, t): open_n -= 1
    for p in pairs:
        pos = prep[p]['pos']
        if pos: trades.append(dict(pair=p, t=pos['t'], exit_t=int(prep[p]['ts'][-1]), r=0.0, why='结束', d=pos['d']))
    return pd.DataFrame(trades)
