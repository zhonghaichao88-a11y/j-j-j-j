"""逐笔模拟（不看未来）：信号在第 i 根K线收盘时产生。
进场：market = 下一根开盘价 + 滑点（吃单）；limit = 挂在 lpx，之后 lbars 根内价格穿过才成交（挂单），成交那根也检查止损（往坏处算）。
出场：同一根里先算止损（止损价或跳空开盘价，再加止损滑点，吃单）→ 止盈（挂单）→ 到 hold 根收盘平（吃单+滑点）。
资金费率：持仓期间每次结算，多单付 rate、空单收 rate。
每个币同一时间只拿一单；信号后 cooldown 根内不再接新信号。"""
import numpy as np
TAKER, MAKER, SLIP, STOP_SLIP = 0.0005, 0.0002, 0.0002, 0.0005


def run(df, sig, side, sd, td=None, hold=144, entry='market', lpx=None, lbars=1, cooldown=None):
    """sig: 信号所在行号数组（升序）；side/sd/td/lpx 可以是数或同长数组；sd/td 是止损/止盈距离（占进场价比例，td 可为 inf）"""
    O, H, L, C = (df[k].values for k in ('o', 'h', 'l', 'c'))
    T = df.index.values
    fts, frate = df.attrs.get('fts', np.array([])), df.attrs.get('frate', np.array([]))
    n = len(O)
    sig = np.asarray(sig)
    k = len(sig)
    arr = lambda x: np.full(k, x, dtype=float) if np.isscalar(x) or x is None else np.asarray(x, dtype=float)
    holds = arr(hold).astype(int)                    # 每笔可以有自己的持有根数（比如拿到当天结束）
    cools = holds if cooldown is None else arr(cooldown).astype(int)
    side, sd = arr(side), arr(sd)
    td = arr(np.inf if td is None else td)
    lpx = arr(np.nan if lpx is None else lpx)
    out = []
    nxt = -1
    busy = -1
    for m, i in enumerate(sig):
        if i <= nxt or i <= busy or i + 1 >= n:
            continue
        nxt = i + cools[m] - 1
        s = side[m]
        if entry == 'market':
            j0, e, fee_in = i + 1, O[i + 1] * (1 + s * SLIP), TAKER
        else:
            px, j0 = lpx[m], None
            for j in range(i + 1, min(i + 1 + lbars, n)):
                if (L[j] < px) if s > 0 else (H[j] > px):
                    j0 = j
                    break
            if j0 is None:
                continue
            e, fee_in = px, MAKER
        if not (sd[m] > 0) or not np.isfinite(e):
            continue
        st = e * (1 - s * sd[m])
        tg = e * (1 + s * td[m]) if np.isfinite(td[m]) else None
        end = min(j0 + holds[m] - 1, n - 1)
        ex, why, fee_out, j = None, '到时间', TAKER, j0
        for j in range(j0, end + 1):
            if (L[j] <= st) if s > 0 else (H[j] >= st):
                base = min(O[j], st) if s > 0 else max(O[j], st)      # 跳空开盘越过止损就按开盘价算
                ex, why, fee_out = base * (1 - s * STOP_SLIP), '止损', TAKER
                break
            tp_ok = entry == 'market' or j > j0                       # 挂单成交那根不知道先后，不算止盈
            if tg is not None and tp_ok and ((H[j] > tg) if s > 0 else (L[j] < tg)):
                ex, why, fee_out = tg, '止盈', MAKER
                break
        if ex is None:
            j = end
            ex = C[j] * (1 - s * SLIP)
        fund = 0.0
        if len(fts):
            a, b = np.searchsorted(fts, T[j0], 'right'), np.searchsorted(fts, T[j], 'right')
            fund = frate[a:b].sum() * s
        ret = s * (ex / e - 1) - fee_in - fee_out - fund
        out.append((df.attrs.get('name', ''), int(T[i]), int(T[j0]), int(T[j]), int(s), ret, ret / sd[m], why, fund))
        busy = j
    return out


COLS = ['coin', 't', 't_in', 't_out', 'side', 'ret', 'R', 'why', 'fund']
