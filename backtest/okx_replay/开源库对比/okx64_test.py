"""方案三（ADXMomentum 多空 + BTC 日线EMA50）在欧易 64 个没测过的币上：规则不改，固定每笔100U、最多10仓、钱包990；
成本单边 0.10% / 0.15% / 0.30%；另：每个币单独看（不限仓位，所有信号都做）。"""
import sys, json, os, numpy as np, pandas as pd
sys.path.insert(0, '/home/user/ext'); import adx_bt as A
syms = json.load(open('universe.json'))
def npz(p): z = np.load(p); return {k: z[k] for k in ('ts', 'open', 'high', 'low', 'close')}
fr = {s: npz(f'{s}_1h.npz') for s in syms if os.path.exists(f'{s}_1h.npz')}
fr = {s: f for s, f in fr.items() if len(f['ts']) > 300}
b = npz('BTC_1d.npz'); btc = dict(ts=b['ts'], close=b['close'])
def ms(x): return int(pd.Timestamp(x, tz='UTC').timestamp() * 1000)
def st(T, wallet=990):
    if T.empty: return dict(笔数=0)
    r = T.r.values; pnl = r * 100; o = np.argsort(T.exit_t.values); eq = np.cumsum(pnl[o])
    dd = (np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]).max()
    day = pd.Series(pnl, index=pd.to_datetime(T.exit_t.values, unit='ms')).resample('D').sum() / wallet * 100
    return dict(笔数=len(r), 空单=int((T.d == -1).sum()), 胜率=f'{(r > 0).mean() * 100:.0f}%', 每笔=f'{r.mean() * 100:+.3f}%',
                收益=f'{pnl.sum() / wallet * 100:+.0f}%', 最大回撤=f'{dd / wallet * 100:.0f}%', 最差一天=f'{day.min():+.0f}%', 止损次数=int((T.why == '止损').sum()))
P = (('第一年', '2024-09-29', '2025-09-29'), ('第二年', '2025-09-29', '2026-10-01'))
print('币数', len(fr), '（K线不足300根的不算）')
rows = []
for lab, a, c in P:
    for fee in (0.001, 0.0015, 0.003):
        T = A.run(fr, btc, fee=fee, t0=ms(a), t1=ms(c), wallet=990, short=True)
        rows.append(dict(段=lab, 成本=f'{fee * 100:.2f}%', **st(T))); print(rows[-1], flush=True)
R = pd.DataFrame(rows); R.to_csv('okx64_result.csv', index=False)
# 每个币单独（不限仓位、所有信号都做），成本0.15%，两年合计
T = A.run(fr, btc, fee=0.0015, t0=ms('2024-09-29'), t1=ms('2026-10-01'), wallet=10**9, max_open=10**6, short=True)
g = T.groupby('pair').agg(笔数=('r', 'size'), 胜率=('r', lambda r: (r > 0).mean()), 合计=('r', 'sum'), 最大单亏=('r', 'min')).sort_values('合计')
g.to_csv('okx64_per_coin.csv')
print(f'每个币单独：赚钱的币 {(g.合计 > 0).sum()} 个，亏钱的币 {(g.合计 <= 0).sum()} 个，没信号 {len(fr) - len(g)} 个')
print('亏最多的5个：'); print((g.head(5)[['笔数', '合计', '最大单亏']] * [1, 100, 100]).round(1).to_string())
print('赚最多的5个：'); print((g.tail(5)[['笔数', '合计', '最大单亏']] * [1, 100, 100]).round(1).to_string())
pd.set_option('display.width', 250); print(R.to_string(index=False)); print('DONE')
