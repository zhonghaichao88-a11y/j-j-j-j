# 规则实验室 3：NFI 里没有的指标 / 公式（直接从K线算，不用 freqtrade）+ 更灵活的出场。
# 进场（做多 / 做空各一套，幅度多档）：
#   Z     1 小时收盘偏离 24 / 72 小时均线的标准差倍数（z 分数均值回归）
#   VN    过去 1 / 4 小时涨跌除以波动（按波动算的急跌急涨，单位"几个标准差"）
#   IDIO  币自己的涨跌减去 BTC 带动的部分（beta），再除以波动；BTC 自己没大跌/大涨
#   CRSI  Connors RSI（1 小时）=（RSI3 + 连涨连跌天数的 RSI2 + 涨跌幅百分位）/ 3
#   IBS   4 小时K线收盘在当根高低之间的位置 + RSI21
#   DON   跌破 / 突破 20 天最低 / 最高（顺势：熊市跟空、牛市跟多）
# 出场 7 种：0~3 同前（固定止盈止损），4 = 止盈 1×ATR、止损 3×ATR（1 小时 ATR14）、最多 48h，
#   5 = 回到 24 小时均线就走（止损 8%、最多 48h），6 = 移动止盈（赚 2% 后回撤 1% 走，止损 6%，最多 48h）
import os, sys, glob, time, numpy as np, pandas as pd, talib
from numba import njit
sys.path.insert(0, '/home/user/ext/nfisig')
from labelmod import label, EXITS, TAKER, MAKER, SLIP, STOP_SLIP
datadir, t0s, t1s, out = sys.argv[1:5]
os.makedirs(out, exist_ok=True)


@njit(cache=True)
def label_x(o, h, l, c, idx, side, mode, atr, sma, hold):
    """mode 4 ATR 止盈止损；5 回到均线；6 移动止盈。返回收益、持仓根数"""
    n = len(o); res = np.full(len(idx), np.nan); dur = np.full(len(idx), -1, np.int16)
    for q in range(len(idx)):
        i = idx[q] + 1
        if i >= n - 1: continue
        e = o[i] * (1 + side * SLIP); fee = TAKER; ex = np.nan
        if mode == 4:
            a = atr[idx[q]]
            if not (a > 0): continue
            tg = e + side * a; st = e - side * 3 * a
        elif mode == 5:
            st = e * (1 - side * 0.08); tg = 0.0
        else:
            st = e * (1 - side * 0.06); tg = 0.0
        best = e
        for j in range(i, min(n, i + hold)):
            if (l[j] <= st) if side > 0 else (h[j] >= st):
                ex = (min(o[j], st) if side > 0 else max(o[j], st)) * (1 - side * STOP_SLIP); fee += TAKER; dur[q] = j - i + 1; break
            if mode == 4:
                if (h[j] >= tg) if side > 0 else (l[j] <= tg):
                    ex = tg; fee += MAKER; dur[q] = j - i + 1; break
            elif mode == 5:
                if j > i and ((c[j] >= sma[j]) if side > 0 else (c[j] <= sma[j])):
                    ex = c[j] * (1 - side * SLIP); fee += TAKER; dur[q] = j - i + 1; break
            else:
                best = max(best, h[j]) if side > 0 else min(best, l[j])
                if side * (best / e - 1) >= 0.02:
                    trail = best * (1 - side * 0.01)
                    if (l[j] <= trail) if side > 0 else (h[j] >= trail):
                        ex = (min(o[j], trail) if side > 0 else max(o[j], trail)) * (1 - side * SLIP); fee += TAKER; dur[q] = j - i + 1; break
        if np.isnan(ex):
            if i + hold > n: continue
            ex = c[min(n, i + hold) - 1] * (1 - side * SLIP); fee += TAKER; dur[q] = hold
        res[q] = side * (ex / e - 1) - fee
    return res, dur


def load(c):
    f = f'{datadir}/futures/{c}_USDT_USDT-5m-futures.feather'
    d = pd.read_feather(f)[['date', 'open', 'high', 'low', 'close', 'volume']]
    d = d[(d.date >= pd.Timestamp(t0s, tz='UTC') - pd.Timedelta(days=60)) & (d.date < pd.Timestamp(t1s, tz='UTC'))].reset_index(drop=True)
    return d


def hourly(d, rule='1h'):
    k = d.set_index('date').resample(rule, label='left', closed='left').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
    return k


def to5(d, k, rule, arr):
    """大周期K线收盘时的值，放到那根大K线最后一根 5 分钟K线上，之后一直沿用到下一根大K线收盘"""
    s = pd.Series(arr, index=k.index + pd.Timedelta(rule) - pd.Timedelta('5min'))
    return s.reindex(d.date).ffill().values


def streak(c):
    s = np.zeros(len(c))
    for i in range(1, len(c)):
        if c[i] > c[i - 1]: s[i] = s[i - 1] + 1 if s[i - 1] > 0 else 1
        elif c[i] < c[i - 1]: s[i] = s[i - 1] - 1 if s[i - 1] < 0 else -1
    return s


btc = load('BTC'); bh = hourly(btc)
btc_r1 = bh.close.pct_change().values
coins = sorted({os.path.basename(f).split('_USDT_USDT')[0] for f in glob.glob(datadir + '/futures/*-5m-futures.feather')})
if os.environ.get('PART'):
    k_, n_ = map(int, os.environ['PART'].split('/')); coins = [c for i, c in enumerate(coins) if i % n_ == k_]
for c in coins:
    if os.path.exists(f'{out}/{c}.parquet'): continue
    t_ = time.time()
    try:
        d = load(c)
    except Exception as e:
        print('ERR', c, e, flush=True); continue
    if len(d) < 6000: continue
    o, h, l, cl = (d[x].values.astype(float) for x in ('open', 'high', 'low', 'close'))
    k = hourly(d); kc, kh, kl = k.close.values, k.high.values, k.low.values
    r1 = np.r_[np.nan, kc[1:] / kc[:-1] - 1]
    # ---- 1 小时特征
    F = {}
    for w in (24, 72):
        m, s = pd.Series(kc).rolling(w).mean().values, pd.Series(kc).rolling(w).std().values
        F[f'z{w}'] = (kc - m) / s
    vol1 = pd.Series(r1).rolling(24 * 7, min_periods=48).std().values          # 1 小时收益的 7 天波动
    for n in (1, 4):
        rn = pd.Series(kc).pct_change(n).values
        F[f'vn{n}'] = rn / (vol1 * np.sqrt(n))
    bt = pd.Series(btc_r1, index=bh.index).reindex(k.index).values
    cov = pd.Series(r1).rolling(24 * 7, min_periods=48).cov(pd.Series(bt)).values; var = pd.Series(bt).rolling(24 * 7, min_periods=48).var().values
    beta = cov / var; resid = r1 - beta * bt
    rv = pd.Series(resid).rolling(24 * 7, min_periods=48).std().values
    F['idio1'] = resid / rv
    F['idio4'] = pd.Series(resid).rolling(4).sum().values / (rv * 2)
    F['btc4'] = pd.Series(bt).rolling(4).sum().values
    rsi3 = talib.RSI(kc, 3); rs2 = talib.RSI(streak(kc), 2)
    pr = pd.Series(r1).rolling(100).apply(lambda x: (x[:-1] < x[-1]).mean() * 100, raw=True).values
    F['crsi'] = (rsi3 + rs2 + pr) / 3
    F['sma24'] = pd.Series(kc).rolling(24).mean().values
    F['atr'] = talib.ATR(kh, kl, kc, 14)
    # 4 小时 IBS
    k4 = hourly(d, '4h'); ibs4 = (k4.close - k4.low) / (k4.high - k4.low).replace(0, np.nan); rsi21_4 = talib.RSI(k4.close.values, 21)
    # 日线唐奇安（用 1 小时收盘对比过去 20 天最高最低，不含当前）
    dmax = pd.Series(kh).rolling(24 * 20).max().shift(1).values; dmin = pd.Series(kl).rolling(24 * 20).min().shift(1).values
    G = {nm: to5(d, k, '1h', v) for nm, v in F.items()}
    G['ibs4'] = to5(d, k4, '4h', ibs4.values); G['rsi21_4'] = to5(d, k4, '4h', rsi21_4)
    G['dmax'] = to5(d, k, '1h', dmax); G['dmin'] = to5(d, k, '1h', dmin)
    G['hclose'] = to5(d, k, '1h', kc)
    # 只在每根 1 小时 / 4 小时K线收盘那一根 5 分钟上判断（不重复触发）
    last1 = np.zeros(len(d), bool); last1[np.searchsorted(d.date.values, (k.index + pd.Timedelta('55min')).values).clip(0, len(d) - 1)] = True
    last4 = np.zeros(len(d), bool); last4[np.searchsorted(d.date.values, (k4.index + pd.Timedelta('235min')).values).clip(0, len(d) - 1)] = True
    ok = (d.date >= pd.Timestamp(t0s, tz='UTC')).values.copy(); ok[-1] = False
    R = []
    for x in (2.0, 2.5, 3.0, 3.5):
        for w in (24, 72):
            R += [(f'L_Z{w}_{x}', 1, last1 & (G[f'z{w}'] < -x)), (f'S_Z{w}_{x}', -1, last1 & (G[f'z{w}'] > x))]
    for x in (2.5, 3.0, 4.0, 5.0):
        for n in (1, 4):
            R += [(f'L_VN{n}_{x}', 1, last1 & (G[f'vn{n}'] < -x)), (f'S_VN{n}_{x}', -1, last1 & (G[f'vn{n}'] > x))]
            R += [(f'L_IDIO{n}_{x}', 1, last1 & (G[f'idio{n}'] < -x) & (G['btc4'] > -0.01)),
                  (f'S_IDIO{n}_{x}', -1, last1 & (G[f'idio{n}'] > x) & (G['btc4'] < 0.01))]
    for x in (5, 10, 15, 20):
        R += [(f'L_CRSI_{x}', 1, last1 & (G['crsi'] < x)), (f'S_CRSI_{x}', -1, last1 & (G['crsi'] > 100 - x))]
    for x in (0.1, 0.15, 0.25):
        R += [(f'L_IBS4_{x}', 1, last4 & (G['ibs4'] < x) & (G['rsi21_4'] < 45)), (f'S_IBS4_{x}', -1, last4 & (G['ibs4'] > 1 - x) & (G['rsi21_4'] > 55))]
    R += [('L_DONbreak', 1, last1 & (G['hclose'] > G['dmax'])), ('S_DONbreak', -1, last1 & (G['hclose'] < G['dmin']))]
    tms = d.date.values.astype('datetime64[ms]').astype(np.int64)
    atr5, sma5 = np.nan_to_num(G['atr']), np.nan_to_num(G['sma24'], nan=np.inf)
    rows = []
    for nm, side, trig in R:
        idx = np.where(np.nan_to_num(trig, nan=0).astype(bool) & ok)[0].astype(np.int64)
        if not len(idx): continue
        rec = {'rule': nm, 't': tms[idx + 1]}
        for k_, (tp, sl, hd) in enumerate(EXITS):
            r_, du_ = label(o, h, l, cl, idx, float(side), tp, sl, hd); rec[f'y{k_}'] = r_.astype(np.float32); rec[f'd{k_}'] = du_
        sm = sma5 if side > 0 else np.where(np.isinf(sma5), -np.inf, sma5)
        for mode in (4, 5, 6):
            r_, du_ = label_x(o, h, l, cl, idx, float(side), mode, atr5, sm, 48 * 12); rec[f'y{mode}'] = r_.astype(np.float32); rec[f'd{mode}'] = du_
        rows.append(pd.DataFrame(rec))
    T = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=['rule', 't'])
    T['rule'] = T.rule.astype('category'); T.to_parquet(f'{out}/{c}.parquet', compression='zstd')
    print(c, len(T), round(time.time() - t_), flush=True)
