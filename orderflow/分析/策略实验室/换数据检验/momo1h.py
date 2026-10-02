"""追强势币 1 小时版（不算资金费，偏保守）：整点收盘 24 小时涨超 a、这 1 小时成交额 ≥ 过去 7 天平均每小时 v 倍、这 1 小时收阳
→ 下一小时开盘买，止损 15%，拿 24 小时；成本：吃单 0.05%×2 + 滑点 0.02%×2，止损多 0.05%"""
import numpy as np, pandas as pd


def run(df, a=0.2, v=3.0, sd=0.15, hold=24):
    C, O, L, Q, T = (df[k].values for k in ('c', 'o', 'l', 'qv', 'ts'))
    n = len(C)
    qavg = pd.Series(Q).rolling(168, min_periods=24).mean().values
    out, i, last = [], 25, -10 ** 9
    while i < n - 1:
        if (i - last >= 24 and C[i - 24] > 0 and C[i] / C[i - 24] - 1 > a and qavg[i] > 0
                and Q[i] >= v * qavg[i] and C[i] > C[i - 1] and T[i] - T[i - 24] == 24 * 3_600_000):
            e = O[i + 1] * 1.0002
            st, end, ex, j = e * (1 - sd), min(i + hold, n - 1), None, i + 1
            for j in range(i + 1, end + 1):
                if L[j] <= st:
                    ex = min(O[j], st) * (1 - 0.0005)
                    break
            if ex is None:
                j = end
                ex = C[end] * (1 - 0.0002)
            out.append((int(T[i]), ex / e - 1 - 0.001))
            last, i = i, j + 1
            continue
        i += 1
    return out


def summary(name, rows, cut=None):
    T = pd.DataFrame(rows, columns=['coin', 't', 'ret'])
    if not len(T):
        print(name, '没有单'); return T
    pf = lambda r: r[r > 0].sum() / -r[r < 0].sum() if (r < 0).any() else float('inf')
    q = pd.to_datetime(T.t, unit='ms').dt.to_period('Q')
    byq = T.groupby(q).ret.mean()
    s = T.ret.sort_values()
    days = (T.t.max() - T.t.min()) / 86_400_000
    print(f'{name}: {T.coin.nunique()} 个币 {len(T)} 笔（每天 {len(T)/max(days,1):.1f}）胜率 {(T.ret>0).mean()*100:.0f}% 每笔 {T.ret.mean()*100:+.2f}% '
          f'PF {pf(T.ret):.2f} | 去掉最赚 1% 的单 PF {pf(s.iloc[:-max(1,len(s)//100)]):.2f} | 赚钱季度 {(byq>0).sum()}/{len(byq)}', flush=True)
    return T
