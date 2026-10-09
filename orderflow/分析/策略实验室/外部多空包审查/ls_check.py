# 用对方代码原样跑，再按正确口径重算（扣手续费、按加仓后的实际仓位算盈亏）
import sys, numpy as np, pandas as pd
sys.path.insert(0, sys.argv[1])
from strategies.long_common import load_symbol, backtest_long
from strategies import long_pure as D
from strategies.short_reversion import short_strategy
from strategies.long_reversion import compute_ind
S = "BTC ETH SOL DOGE ADA AVAX LINK DOT NEAR ATOM LTC BCH ALGO ICP FIL SAND MANA".split()
P = [('2022', '2022-01-01', '2022-12-31'), ('2023', '2023-01-01', '2023-12-31'), ('2024', '2024-01-01', '2024-12-31'), ('2025', '2025-01-01', '2025-12-31')]
FEE = 0.0008

def bt(close, entry, exit_, side, tp, hold, dca, hs=0.08, add=0.5):
    """和对方 backtest_long / short_strategy 一样的逻辑，多记下每笔的仓位和全部手续费"""
    n = len(close); pos = 0; out = []
    for i in range(50, n):
        px = close[i]
        if pos == 0:
            if entry[i]:
                pos, avg, e0, bar, k, qty, fee = 1, px, px, i, 0, 1.0, px * FEE
            continue
        if k < len(dca):
            th = avg * (1 - side * dca[k])
            if (px <= th) if side > 0 else (px >= th):
                avg = (avg * qty + px * add) / (qty + add); qty += add; k += 1; fee += add * px * FEE
        ret = side * (px - avg) / avg
        if ret >= tp or exit_[i] or i - bar >= hold or ret <= -hs:
            fee += qty * px * FEE
            pnl = qty * side * (px - avg) - fee                  # 真实盈亏（以第一笔 1 份为单位，价格单位）
            out.append(dict(ret_pct=ret * 100, qty=qty, net=pnl / e0, gross_win=ret > 0, net_win=pnl > 0))
            pos = 0
    return out

rows = []
for c in S:
    df = load_symbol(c, '1h')
    for pn, a, b in P:
        d = df[(df.index >= a) & (df.index <= b)]
        if len(d) < 200: continue
        ind = compute_ind(d); cl = ind['close']
        # 多头 D：rsi45 / tp3% / hold144 / 三层 DCA
        e, x = D.gen_signals(ind, 45)
        for t in bt(cl, e, x, 1, 0.03, 144, (0.015, 0.03, 0.05)): rows.append(dict(策略='多头D', 年=pn, 币=c, **t))
        # 空头：rsi70 / exit35 / tp5% / hold144 / 三层 DCA（和 short_strategy 同条件）
        r3, r14 = ind['r3'], ind['r14']
        from strategies.short_reversion import aroon_up, rsi
        au = aroon_up(d.high.to_numpy(), d.low.to_numpy(), 14)
        e = (r3 > 70) & (r14 > 60) & (au < 85); x = r3 < 35
        for t in bt(cl, e, x, -1, 0.05, 144, (0.015, 0.03, 0.05)): rows.append(dict(策略='空头', 年=pn, 币=c, **t))
T = pd.DataFrame(rows)
pf = lambda s: s[s > 0].sum() / -s[s < 0].sum()
for (st, yr), g in T.groupby(['策略', '年']):
    their = (g.ret_pct / 100 * 4).sum()                      # 对方口径：ret_pct × 4U，不扣费、不管加了几份
    real = (g.net * 4).sum()                                  # 正确：扣全部手续费，按实际仓位（第一笔 4U，每次加仓 +2U）
    print(f'{st} {yr}: {len(g)}笔 | 对方口径 胜率{g.gross_win.mean():.1%} 收益{their:+.1f}U | '
          f'正确口径 胜率{g.net_win.mean():.1%} PF{pf(g.net):.2f} 收益{real:+.1f}U | 加过仓的单 {(g.qty > 1).mean():.0%}，这些单正确口径合计 {(g[g.qty > 1].net * 4).sum():+.1f}U')
