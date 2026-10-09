"""扫止损（补仓）按"占用资金"重算：程序记录里的 ret 是按最后整个仓位算的，没补满时会放大。
这里按每笔开仓时计划的整单资金（开仓时权益 × 10%）算：ret_cap = 盈亏U ÷ (开仓时权益 × 10%)。
单币回放从 1000U 开始，开仓时权益 = 1000 + 之前平掉的单的盈亏（同一时间只有一单，准确）。"""
import glob, os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compare as C
P = []
for f in glob.glob('/home/user/ext/replay/sweep/*.parquet'):
    if f.endswith('.sig.parquet'): continue
    x = pd.read_parquet(f)
    if not len(x): continue
    x = x.sort_values('t_close'); x['coin'] = os.path.basename(f)[:-8]
    eq = 1000 + x.pnl.cumsum().shift(1).fillna(0)
    # 开仓时权益：t_open 之前已平掉的单的盈亏
    closed = x[['t_close', 'pnl']].sort_values('t_close'); cs = closed.pnl.cumsum().values; ct = closed.t_close.values
    x['eq_open'] = [1000 + (cs[np.searchsorted(ct, t, 'right') - 1] if np.searchsorted(ct, t, 'right') > 0 else 0) for t in x.t_open]
    x['ret_cap'] = x.pnl / (0.10 * x.eq_open)
    P.append(x)
P = pd.concat(P); P['t_in'] = P.t_open // 300_000 * 300_000; P['t_out'] = P.t_close // 300_000 * 300_000
P = P[P.t_in >= C.ms('2022-01-10')]
print('程序（按占用资金）：', C.stats(P, 'ret_cap'))
print('程序（记录里的 ret，被放大）：', C.stats(P, 'ret'))
for y, g in P.groupby(pd.to_datetime(P.t_in, unit='ms').dt.year): print(' ', y, C.stats(g, 'ret_cap'))
for s in (1, -1): print(' 做多' if s == 1 else ' 做空', C.stats(P[P.side == s], 'ret_cap'))
B = C.backtest('sweep'); B = B[B.coin.isin(P.coin.unique()) & (B.t >= C.ms('2022-01-10'))]
M = P.merge(B, on=['coin', 't_in'], suffixes=('_p', '_b')); print('两边都有', len(M), '每笔差（程序按占用资金 - 回测）', round((M.ret_cap - M.ret_b).mean() * 100, 3), '%，平仓同一根', round((M.t_out_p == M.t_out_b).mean() * 100), '%')
print('回测（同一批币）：', C.stats(B))
