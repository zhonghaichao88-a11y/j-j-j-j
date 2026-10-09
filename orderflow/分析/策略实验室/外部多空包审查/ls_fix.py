# 能不能修：在正确口径下（扣费、按实际仓位）试几种常见修法
import sys, itertools, numpy as np, pandas as pd
sys.path.insert(0, sys.argv[1])
from strategies.long_common import load_symbol
from strategies.long_reversion import compute_ind
from strategies.short_reversion import aroon_up
S = "BTC ETH SOL DOGE ADA AVAX LINK DOT NEAR ATOM LTC BCH ALGO ICP FIL SAND MANA".split()
P = [('2022', '2022-01-01', '2022-12-31'), ('2023', '2023-01-01', '2023-12-31'), ('2024', '2024-01-01', '2024-12-31'), ('2025', '2025-01-01', '2025-12-31')]

def bt(close, entry, exit_, side, tp, hold, dca, fee_r, hs=0.08, add=0.5, use_sig=True):
    n = len(close); pos = 0; out = []
    for i in range(50, n):
        px = close[i]
        if pos == 0:
            if entry[i]: pos, avg, e0, bar, k, qty, fee = 1, px, px, i, 0, 1.0, px * fee_r
            continue
        if k < len(dca):
            th = avg * (1 - side * dca[k])
            if (px <= th) if side > 0 else (px >= th):
                avg = (avg * qty + px * add) / (qty + add); qty += add; k += 1; fee += add * px * fee_r
        ret = side * (px - avg) / avg
        if ret >= tp or (use_sig and exit_[i]) or i - bar >= hold or ret <= -hs:
            fee += qty * px * fee_r
            out.append(qty * side * (px - avg) / e0 - fee / e0); pos = 0
    return out

IND = {}
for c in S:
    df = load_symbol(c, '1h')
    for pn, a, b in P:
        d = df[(df.index >= a) & (df.index <= b)]
        if len(d) >= 200:
            ind = compute_ind(d); ind['au'] = aroon_up(d.high.to_numpy(), d.low.to_numpy(), 14); IND[(c, pn)] = ind
rows = []
for side in (1, -1):
    for th, dca, sig, tp, fee in itertools.product((10, 20, 30, 45) if side > 0 else (90, 80, 70),
                                                   ((), (0.015, 0.03, 0.05)), (True, False), (0.02, 0.03, 0.05), (0.0008, 0.0004)):
        res = {}
        for pn, _, _ in P:
            r = []
            for c in S:
                ind = IND.get((c, pn))
                if ind is None: continue
                r3, r14 = ind['r3'], ind['r14']
                if side > 0: e, x = r3 < th, r3 > 65
                else: e, x = (r3 > th) & (r14 > 60) & (ind['au'] < 85), r3 < 35
                r += bt(ind['close'], e, x, side, tp, 144, dca, fee, use_sig=sig)
            r = np.array(r); res[pn] = (len(r), (r > 0).mean(), r[r > 0].sum() / -r[r < 0].sum(), r.sum() * 4)
        rows.append(dict(方向='多' if side > 0 else '空', RSI门槛=th, 补仓='有' if dca else '无', RSI出场='有' if sig else '无', 止盈=tp,
                         费率=('吃单0.08%' if fee == 0.0008 else '挂单0.04%'), 最差PF=round(min(v[2] for v in res.values()), 2),
                         **{pn: f'{v[0]}笔 胜{v[1]:.0%} PF{v[2]:.2f} {v[3]:+.0f}U' for pn, v in res.items()}))
R = pd.DataFrame(rows).sort_values('最差PF', ascending=False)
pd.set_option('display.width', 300); pd.set_option('display.max_colwidth', 40)
print('试了', len(R), '种；四年都 PF>1 的：', (R.最差PF > 1).sum(), '；四年都 PF≥1.3 的：', (R.最差PF >= 1.3).sum())
for d in ('多', '空'): print(R[R.方向 == d].head(6).to_string(index=False))
R.to_csv(sys.argv[2], index=False)
