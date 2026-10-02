"""照 GainzAlgo 官方公开说明的四步逻辑自己写的版本（不是原版代码），2024-01 ~ 2026-03，46 个币。
做多（做空完全对称）：
  1 反转K线：前一根收阴，这一根收阳，并且实体吞没前一根实体
  2 波动够大：这根K线振幅 ≥ 1 倍 ATR(14)，实体占振幅一半以上
  3 RSI 确认：最近 5 根里 RSI(14) 到过 30 以下，这一根 RSI 比上一根高
  4 短期趋势：前 10 根是跌的（反转前要先有一段下跌）
进场：信号K线收盘后，下一根开盘价（吃单）。止损：信号K线最低点；止盈：止损距离的 1 / 1.5 / 2 倍。
同一根K线里止损止盈都碰到，按止损算。最多拿 48 根。成本：一进一出 0.12%（吃单 + 滑点）。
周期：5 分钟、15 分钟、1 小时（由 5 分钟合成）。"""
import pandas as pd, numpy as np, glob, os

COST = 0.0012


def resample(d, n):
    if n == 1:
        return d
    g = d.ts // (300_000 * n)
    return pd.DataFrame({'ts': d.ts.groupby(g).first(), 'open': d.open.groupby(g).first(), 'high': d.high.groupby(g).max(),
                         'low': d.low.groupby(g).min(), 'close': d.close.groupby(g).last()}).reset_index(drop=True)


def rsi(c, n=14):
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / n).mean(), (-d.clip(upper=0)).ewm(alpha=1 / n).mean()
    return 100 - 100 / (1 + up / dn)


def signals(d):
    o, h, l, c = d.open, d.high, d.low, d.close
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    r = rsi(c)
    body = (c - o).abs()
    big = ((h - l) >= atr) & (body >= 0.5 * (h - l))
    long_ = (c.shift() < o.shift()) & (c > o) & (c >= o.shift()) & (o <= c.shift()) & big \
        & (r.rolling(5).min() < 30) & (r > r.shift()) & (c.shift(1) < c.shift(11))
    short = (c.shift() > o.shift()) & (c < o) & (c <= o.shift()) & (o >= c.shift()) & big \
        & (r.rolling(5).max() > 70) & (r < r.shift()) & (c.shift(1) > c.shift(11))
    return long_.fillna(False).values, short.fillna(False).values


def run(d, rr, hold=48):
    L, S = signals(d)
    O, H, Lo, C, T = d.open.values, d.high.values, d.low.values, d.close.values, d.ts.values
    out, busy = [], -1
    for i in np.where(L | S)[0]:
        if i <= busy or i + 1 >= len(O):
            continue
        side = 1 if L[i] else -1
        e = O[i + 1]
        st = Lo[i] if side == 1 else H[i]
        risk = (e - st) * side
        if risk <= 0:
            continue
        tp = e + side * rr * risk
        ex, j = None, i + 1
        for j in range(i + 1, min(i + 1 + hold, len(O))):
            if (Lo[j] <= st) if side == 1 else (H[j] >= st):
                ex = st
                break
            if (H[j] >= tp) if side == 1 else (Lo[j] <= tp):
                ex = tp
                break
        if ex is None:
            ex = C[j]
        ret = side * (ex / e - 1) - COST
        out.append((T[i], ret, ret / (risk / e)))
        busy = j
    return out


if __name__ == '__main__':
    files = sorted(glob.glob('/home/user/ext/long/k/*.parquet'))
    pf = lambda x: x[x > 0].sum() / -x[x < 0].sum()
    for n, name in ((1, '5分钟'), (3, '15分钟'), (12, '1小时')):
        data = {f: resample(pd.read_parquet(f, columns=['ts', 'open', 'high', 'low', 'close']).sort_values('ts').drop_duplicates('ts').reset_index(drop=True), n) for f in files}
        for rr in (1.0, 1.5, 2.0):
            rows = []
            for f, d in data.items():
                rows += run(d, rr)
            R = pd.DataFrame(rows, columns=['ts', 'ret', 'R'])
            R['q'] = pd.to_datetime(R.ts, unit='ms').dt.to_period('Q').astype(str)
            byq = R.groupby('q').ret.mean()
            gross = R.ret + COST
            print(f'{name} 止盈{rr}R: {len(R)}笔 胜率{(R.ret > 0).mean() * 100:.0f}% 扣成本前每笔{gross.mean() * 1e4:+.1f}基点 '
                  f'扣成本后{R.ret.mean() * 1e4:+.1f}基点 平均{R.R.mean():+.2f}R PF{pf(R.ret):.2f} 赚钱季度{(byq > 0).sum()}/{len(byq)}', flush=True)
