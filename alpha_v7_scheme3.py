"""方案三：ADX 动量多空 + BTC 大盘过滤（研究：backtest/okx_replay/开源库对比/adx_bt.py、adx_verify.py；
规则来自 freqtrade 公开策略 ADXMomentum，加 BTC 日线 EMA50 大盘过滤与镜像做空，参数一律不改）。

周期：1 小时，只用已收盘K线。
  做多：ADX(14)>25 且 MOM(14)>0 且 +DI(25)>25 且 +DI>−DI，并且 BTC 前一根已收盘日线收盘 > EMA50（至少 50 根日线）。
  做空：把价格上下翻转（1/价格）后同样的做多条件（镜像），并且 BTC 前一根已收盘日线收盘 ≤ EMA50。
  出场：止盈 = 扣双边 0.1% 手续费后净赚 1%（交易所止盈单）；止损 25%（交易所止损单）；
        出现反向信号（多单：ADX>25 且 MOM<0 且 −DI>25 且 +DI<−DI；空单取镜像）→ 下一根开盘市价平；不设最长持仓。
  仓位：每笔固定 = 账户权益 10%，最多同时 10 仓。
指标与 TA-Lib 的 ADX / PLUS_DI / MINUS_DI / MOM 逐位对齐（Wilder 平滑，同样的起算方式），不需要安装 TA-Lib。
"""
from __future__ import annotations
import numpy as np

H = 3600000
D = 86400000
ROI = 0.01            # 扣手续费后净止盈
SL = 0.25             # 止损
FEE_BT = 0.001        # 回测里用的单边手续费（止盈价按它反推，与回测一致）
EQUITY_FRAC = 0.10    # 每笔 = 权益 10%
MAX_POSITIONS = 10
ADX_N, DI_N, MOM_N, TH = 14, 25, 14, 25.0
MIN_BARS = 2 * ADX_N + DI_N + 10


def _dm_tr(h, l, c):
    """与 TA-Lib 相同：+DM/−DM/TR 从第 1 根开始（第 0 根只作前值）。"""
    dp = h[1:] - h[:-1]; dm = l[:-1] - l[1:]
    pdm = np.where((dp > 0) & (dp > dm), dp, 0.0)
    mdm = np.where((dm > 0) & (dp < dm), dm, 0.0)
    tr = np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])])
    return pdm, mdm, tr


def _wilder_sum(x, n):
    """TA-Lib 的 Wilder 累计：前 n-1 个直接相加，之后 s = s - s/n + x。返回与 x 同长（前 n-2 个为 nan）。"""
    out = np.full(len(x), np.nan)
    if len(x) < n - 1: return out
    s = float(np.sum(x[:n - 1])); out[n - 2] = s
    for i in range(n - 1, len(x)):
        s = s - s / n + x[i]; out[i] = s
    return out


def di(h, l, c, n):
    """TA-Lib PLUS_DI / MINUS_DI（长度同输入，前 n 根为 nan）。"""
    h, l, c = (np.asarray(x, float) for x in (h, l, c))
    pdm, mdm, tr = _dm_tr(h, l, c)
    sp, sm, st = _wilder_sum(pdm, n), _wilder_sum(mdm, n), _wilder_sum(tr, n)
    with np.errstate(divide='ignore', invalid='ignore'):
        p = np.where(st > 0, 100 * sp / st, 0.0); m = np.where(st > 0, 100 * sm / st, 0.0)
    p[:n - 1] = np.nan; m[:n - 1] = np.nan          # 对应输入下标 < n
    return np.r_[np.nan, p], np.r_[np.nan, m]


def adx(h, l, c, n):
    """TA-Lib ADX：DI 用 n 期 Wilder；DX 取前 n 个平均作首值，之后 (prev*(n-1)+dx)/n。前 2n-1 根为 nan。"""
    h, l, c = (np.asarray(x, float) for x in (h, l, c))
    pdm, mdm, tr = _dm_tr(h, l, c)
    sp, sm, st = _wilder_sum(pdm, n), _wilder_sum(mdm, n), _wilder_sum(tr, n)
    out = np.full(len(c), np.nan)
    with np.errstate(divide='ignore', invalid='ignore'):
        pi = np.where(st > 0, 100 * sp / st, 0.0); mi = np.where(st > 0, 100 * sm / st, 0.0)
        den = pi + mi; dx = np.where(den > 0, 100 * np.abs(mi - pi) / den, 0.0)
    first = n - 1                                   # dx 数组里第一个完整平滑值的下标（对应输入下标 n）
    if len(dx) < first + n: return out
    a = float(np.mean(dx[first:first + n])); out[first + n] = a    # 输入下标 2n-1
    for i in range(first + n, len(dx)):
        a = (a * (n - 1) + dx[i]) / n; out[i + 1] = a
    return out


def mom(c, n):
    c = np.asarray(c, float); out = np.full(len(c), np.nan); out[n:] = c[n:] - c[:-n]; return out


def signals(f):
    """返回 (做多进场, 做多出场) 布尔数组（每根已收盘K线收盘时）。"""
    h, l, c = (np.asarray(f[k], float) for k in ('high', 'low', 'close'))
    a = adx(h, l, c, ADX_N); p, m = di(h, l, c, DI_N)[0], di(h, l, c, DI_N)[1]; mo = mom(c, MOM_N)
    with np.errstate(invalid='ignore'):
        ent = (a > TH) & (mo > 0) & (p > TH) & (p > m)
        ex = (a > TH) & (mo < 0) & (m > TH) & (p < m)
    return np.nan_to_num(ent).astype(bool), np.nan_to_num(ex).astype(bool)


def mirror(f):
    return dict(high=1 / np.asarray(f['low'], float), low=1 / np.asarray(f['high'], float), close=1 / np.asarray(f['close'], float))


def btc_state(btc_daily, now_ms):
    """now_ms 时刻可用的 BTC 大盘状态：前一根已收盘日线 收盘 vs EMA50。返回 1（上方）/-1（下方或等于）/0（数据不足）。"""
    ts = np.asarray(btc_daily.get('ts', []), np.int64); c = np.asarray(btc_daily.get('close', []), float)
    done = ts + D <= now_ms
    ts, c = ts[done], c[done]
    if len(c) < 51: return 0
    e = c[0]; k = 2 / 51
    for x in c[1:]: e = e + k * (x - e)
    return 1 if c[-1] > e else -1


def evaluate(f, btc_daily, now_ms):
    """最后一根已收盘 1 小时K线上的方案三信号。返回 dict(side=1/-1/0, reason, btc)。"""
    ts = np.asarray(f['ts'], np.int64)
    done = ts + H <= now_ms
    g = {k: np.asarray(v)[done] for k, v in f.items()}
    if len(g['ts']) < MIN_BARS: return dict(side=0, reason=f'1小时已收盘K线不足 {MIN_BARS} 根', btc=0)
    b = btc_state(btc_daily, int(g['ts'][-1]) + H)
    if b == 0: return dict(side=0, reason='BTC 日线不足 51 根，大盘状态未知', btc=0)
    le, _ = signals(g); se, _ = signals(mirror(g))
    if b == 1 and le[-1]: return dict(side=1, reason='ADX>25、动量>0、+DI>25 且 +DI>−DI；BTC 日线在 EMA50 上方', btc=b, bar_ts=int(g['ts'][-1]))
    if b == -1 and se[-1]: return dict(side=-1, reason='（镜像）ADX>25、动量<0、−DI>25 且 −DI>+DI；BTC 日线在 EMA50 下方', btc=b, bar_ts=int(g['ts'][-1]))
    return dict(side=0, reason=('BTC 在 EMA50 上方，只看做多：本根没有做多信号' if b == 1 else 'BTC 在 EMA50 下方，只看做空：本根没有做空信号'), btc=b)


def exit_due(f, side, entry_bar_ts, now_ms):
    """持仓的反向出场信号：从进场那根K线起（含）任一根已收盘K线出现出场信号 → 该平仓。"""
    ts = np.asarray(f['ts'], np.int64)
    done = ts + H <= now_ms
    g = {k: np.asarray(v)[done] for k, v in f.items()}
    if len(g['ts']) < MIN_BARS: return False
    _, ex = signals(g if side == 1 else mirror(g))
    since = g['ts'] >= int(entry_bar_ts)
    return bool(np.any(ex & since))


def targets(entry, side):
    """止盈/止损价（与回测一致：净 1% 按单边 0.1% 手续费反推；止损 25%）。"""
    if side == 1:
        return entry * (1 + FEE_BT) * (1 + ROI) / (1 - FEE_BT), entry * (1 - SL)
    return entry * (1 - FEE_BT) * (1 - ROI) / (1 + FEE_BT), entry * (1 + SL)
