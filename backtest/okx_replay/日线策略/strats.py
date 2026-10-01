"""日线策略测试（无前视）：第 d 天收盘时只用 ≤d 的数据算出权重 W[d]，赚 d→d+1 的收盘涨跌；按换手扣成本。
总仓位绝对值之和 = 1（等权）。"""
import numpy as np, pandas as pd, itertools
X = pd.read_pickle('daily.pkl')

def pnl(W, c, fee):
    r = c.pct_change().shift(-1)                       # d→d+1 的收益，用 d 的权重
    W = W.reindex_like(c).fillna(0.0); W = W.where(r.notna() | (W == 0), 0.0)
    gross = (W * r.fillna(0)).sum(axis=1)
    turn = (W - W.shift(1).fillna(0)).abs().sum(axis=1)
    return gross - fee * turn, turn
def norm(S):
    a = S.abs().sum(axis=1).replace(0, np.nan); return S.div(a, axis=0).fillna(0.0)

# 1 时间序列动量：过去 L 天涨就做多、跌就做空（或只做多）
def tsmom(x, L, long_only=False):
    m = np.sign(x['c'] / x['c'].shift(L) - 1)
    if long_only: m = m.clip(lower=0)
    return norm(m.fillna(0))
# 1b 海龟：收盘突破过去 N 天最高（不含当天）做多，跌破过去 M 天最低平多；空单镜像
def turtle(x, N, M, long_only=False):
    c, h, l = x['c'], x['h'], x['l']
    up = c > h.shift(1).rolling(N).max(); dn = c < l.shift(1).rolling(N).min()
    xl = c < l.shift(1).rolling(M).min(); xs = c > h.shift(1).rolling(M).max()
    pos = pd.DataFrame(0.0, index=c.index, columns=c.columns); cur = np.zeros(c.shape[1])
    U, Dn, XL, XS = up.values, dn.values, xl.values, xs.values
    out = np.zeros(c.shape)
    for i in range(len(c)):
        cur = np.where((cur > 0) & XL[i], 0, cur); cur = np.where((cur < 0) & XS[i], 0, cur)
        cur = np.where((cur == 0) & U[i], 1, cur)
        if not long_only: cur = np.where((cur == 0) & Dn[i], -1, cur)
        cur = np.where(np.isnan(c.values[i]), 0, cur); out[i] = cur
    return norm(pd.DataFrame(out, index=c.index, columns=c.columns))
# 2 横截面动量：每 7 天按过去 L 天涨幅排序，做多前 20%、做空后 20%
def xsmom(x, L, hold=7):
    ret = x['c'] / x['c'].shift(L) - 1
    W = pd.DataFrame(np.nan, index=ret.index, columns=ret.columns)
    for i in range(L, len(ret), hold):
        r = ret.iloc[i].dropna()
        if len(r) < 10: continue
        q = r.rank(pct=True); w = pd.Series(0.0, index=ret.columns)
        w[q[q >= 0.8].index] = 1; w[q[q <= 0.2].index] = -1
        W.iloc[i] = w
    return norm(W.ffill(limit=hold - 1).fillna(0))
# 3 暴涨暴跌后反向：当天涨跌超过 k 倍过去30天波动 → 第二天反向持有一天
def reversal(x, k):
    r = x['c'].pct_change(); sd = r.shift(1).rolling(30).std()
    z = r / sd; s = pd.DataFrame(0.0, index=r.index, columns=r.columns)
    s[z > k] = -1; s[z < -k] = 1
    return norm(s)
# 4 周一效应：只在周一持有（周日收盘买、周一收盘卖），BTC 或全部币等权
def monday(x, coin=None):
    c = x['c']; W = pd.DataFrame(0.0, index=c.index, columns=c.columns)
    sel = [coin] if coin and coin in c.columns else list(c.columns)
    is_sun = c.index.dayofweek == 6                  # 周日收盘决定 → 赚周一
    W.loc[is_sun, sel] = 1.0
    return norm(W.where(c.notna(), 0))

def stats(p):
    if len(p) < 20 or p.std() == 0: return dict(年化='-', 夏普='-', 回撤='-', PF='-')
    eq = (1 + p).cumprod(); dd = (1 - eq / eq.cummax()).max()
    pos, neg = p[p > 0].sum(), -p[p < 0].sum()
    return dict(年化=f'{(eq.iloc[-1] ** (365 / len(p)) - 1) * 100:+.0f}%', 夏普=round(p.mean() / p.std() * np.sqrt(365), 2), 回撤=f'{dd * 100:.0f}%', PF=round(pos / neg, 2) if neg > 0 else '-')

STRATS = {}
for L in (10, 20, 60):
    STRATS[f'时间动量 L{L} 多空'] = lambda x, L=L: tsmom(x, L)
    STRATS[f'时间动量 L{L} 只做多'] = lambda x, L=L: tsmom(x, L, True)
for N, M in ((20, 10), (55, 20)):
    STRATS[f'海龟 {N}/{M} 多空'] = lambda x, N=N, M=M: turtle(x, N, M)
    STRATS[f'海龟 {N}/{M} 只做多'] = lambda x, N=N, M=M: turtle(x, N, M, True)
for L in (7, 14, 28):
    STRATS[f'币种轮动 L{L}'] = lambda x, L=L: xsmom(x, L)
for k in (2, 3):
    STRATS[f'暴涨暴跌反向 k{k}'] = lambda x, k=k: reversal(x, k)
STRATS['周一效应 BTC'] = lambda x: monday(x, 'BTCUSDT' if 'BTCUSDT' in x['c'].columns else 'BTC-USDT-SWAP')
STRATS['周一效应 全部币'] = lambda x: monday(x)
STRATS['对照：全部币等权持有'] = lambda x: norm(x['c'].notna().astype(float))

PERIODS = {'更早137(2021-10~2024-09)': [('2022', '2022-01-01', '2023-01-01'), ('2023', '2023-01-01', '2024-01-01'), ('2024前三季', '2024-01-01', '2024-10-01')],
           '币安独有416(2024-09~2026-08)': [('第一年', '2024-09-29', '2025-09-29'), ('第二年', '2025-09-29', '2026-09-01')],
           '币安前100(2024-05~2026-09)': [('2024下半年', '2024-07-01', '2025-01-01'), ('2025', '2025-01-01', '2026-01-01'), ('2026前三季', '2026-01-01', '2026-10-01')],
           '欧易(2024-09~2026-09)': [('第一年', '2024-10-01', '2025-10-01'), ('第二年', '2025-10-01', '2026-10-01')]}
if __name__ == '__main__':
    rows = []
    for sname, fn in STRATS.items():
        for dname, x in X.items():
            W = fn(x)
            for fee in (0.0, 0.0005, 0.0015):
                p, turn = pnl(W, x['c'], fee)
                for lab, a, b in PERIODS[dname]:
                    q = p[(p.index >= a) & (p.index < b)]
                    rows.append(dict(策略=sname, 数据=dname, 段=lab, 成本=fee, 日均换手=round(turn[(turn.index >= a) & (turn.index < b)].mean(), 2), **stats(q)))
        print(sname, 'ok', flush=True)
    R = pd.DataFrame(rows); R.to_csv('daily_results.csv', index=False); print('DONE')
