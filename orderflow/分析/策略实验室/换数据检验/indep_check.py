"""独立重写（不用 sim.py、不用 s19_momo.py）：追强势币 5 分钟版，102 个币，看和原回测是否一致"""
import sys, glob, numpy as np, pandas as pd
sys.path.insert(0, '/home/user/ext/long/lab'); import data
COST_IN, COST_OUT, STOPX = 0.0005 + 0.0002, 0.0005 + 0.0002, 0.0005
rows = []
for c in data.coins():
    k = pd.read_parquet(f'/home/user/ext/long/k/{c}.parquet').sort_values('ts').drop_duplicates('ts').set_index('ts')
    C, O, L, Q = k.close.values, k.open.values, k.low.values, k.quote_volume.values
    n = len(C); i = 288 + 1; last = -10**9
    q12 = pd.Series(Q).rolling(12).sum().values
    qavg = pd.Series(Q).rolling(2016, min_periods=288).mean().values * 12
    while i < n - 1:
        if i - last >= 288 and C[i - 288] > 0 and C[i] / C[i - 288] - 1 > 0.2 and q12[i] >= 3 * qavg[i] and C[i] > C[i - 12]:
            e = O[i + 1] * 1.0002; st = e * 0.85; end = min(i + 288, n - 1); ex = None
            for j in range(i + 1, end + 1):
                if L[j] <= st: ex = min(O[j], st) * (1 - STOPX); break
            if ex is None: ex = C[end] * (1 - 0.0002); j = end
            rows.append((c, k.index[i], ex / e - 1 - 0.001)); last = i; i = j + 1; continue
        i += 1
T = pd.DataFrame(rows, columns=['coin', 't', 'ret'])
pf = lambda r: r[r > 0].sum() / -r[r < 0].sum()
print('独立重写：', len(T), '笔 胜率', round((T.ret > 0).mean() * 100), '% 每笔', round(T.ret.mean() * 100, 2), '% PF', round(pf(T.ret), 2))
