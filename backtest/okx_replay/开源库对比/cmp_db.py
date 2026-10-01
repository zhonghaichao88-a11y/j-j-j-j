import glob, os, numpy as np, pandas as pd, types
import adx_bt as A
M = types.ModuleType('M'); _src = open('adx_more.py', encoding='utf-8').read(); exec(_src[:_src.index('out = []')], M.__dict__)
def npz(p): z = np.load(p); return {k: z[k] for k in ('ts', 'open', 'high', 'low', 'close')}
fr = {os.path.basename(p)[:-4]: npz(p) for p in glob.glob('/home/user/okx_data/bntop/*.npz')}
btc = M.daily(fr['BTCUSDT'])
ms = lambda x: int(pd.Timestamp(x, tz='UTC').timestamp() * 1000)
T = A.run(fr, btc, fee=0.001, t0=ms('2024-06-01'), t1=ms('2026-10-01'), wallet=990, short=True)
D = pd.read_csv('/home/user/doubao/trades_di25.csv'); D['et'] = pd.to_datetime(D.entry_t); D['xt'] = pd.to_datetime(D.exit_t)
T['et'] = pd.to_datetime(T.t, unit='ms', utc=True); T['xt'] = pd.to_datetime(T.exit_t, unit='ms', utc=True)
# 平均持仓时长
print('我 平均持仓h', ((T.xt - T.et).dt.total_seconds() / 3600).mean().round(1), ' 豆包', ((D.xt - D.et).dt.total_seconds() / 3600).mean().round(1))
for typ_me, typ_db in (('止盈', 'TP'), ('信号', 'REV')):
    a = T[T.why == typ_me]; b = D[D.type == typ_db]
    print(typ_me, '我 平均持仓h', ((a.xt - a.et).dt.total_seconds() / 3600).mean().round(1), ' 豆包', ((b.xt - b.et).dt.total_seconds() / 3600).mean().round(1))
e1 = T[(T.pair == 'ETHUSDT') & (T.et >= '2025-01-01') & (T.et < '2025-02-01')][['d', 'et', 'xt', 'why', 'r']]
e2 = D[(D.symbol == 'ETHUSDT') & (D.et >= '2025-01-01') & (D.et < '2025-02-01')][['side', 'et', 'xt', 'type', 'ret']]
pd.set_option('display.width', 200); print('我 ETH 2025-01'); print(e1.to_string(index=False)); print('豆包 ETH 2025-01'); print(e2.to_string(index=False))
m = T.merge(D, left_on=['pair', 'et'], right_on=['symbol', 'et'], suffixes=('_me', '_db'))
m['same_exit'] = m.xt_me == m.xt_db
print('同币同时间进场的单', len(m), '其中平仓时间也一样', int(m.same_exit.sum()))
diff = m[~m.same_exit]
print(diff[['pair', 'et', 'xt_me', 'why', 'r', 'xt_db', 'type', 'ret']].head(12).to_string(index=False))
# 每小时平均持仓数
def occ(df):
    ev = pd.concat([pd.Series(1, index=df.et), pd.Series(-1, index=df.xt)]).sort_index().cumsum()
    return ev.mean()
print('平均同时持仓数 我', round(occ(T), 2), ' 豆包', round(occ(D), 2))
key_db = set(zip(D.symbol, D.et))
un = T[[ (p, e) not in key_db for p, e in zip(T.pair, T.et)]].copy()
print('我开了、豆包没开的', len(un))
# 在那个时刻，豆包手上几个仓、是否已经持有该币
import bisect
iv = D[['symbol', 'et', 'xt']].values
def db_state(sym, t):
    held = 0; same = False
    for s, a, b in iv:
        if a <= t < b:
            held += 1; same |= (s == sym)
    return held, same
smp = un.sample(400, random_state=1)
st = [db_state(p, e) for p, e in zip(smp.pair, smp.et)]
smp['豆包持仓数'] = [x[0] for x in st]; smp['豆包已持有该币'] = [x[1] for x in st]
print('豆包当时已满10仓', (smp['豆包持仓数'] >= 10).mean().round(3), ' 豆包已持有该币', smp['豆包已持有该币'].mean().round(3), ' 都不是', ((smp['豆包持仓数'] < 10) & ~smp['豆包已持有该币']).mean().round(3))
ex = smp[(smp['豆包持仓数'] < 10) & ~smp['豆包已持有该币']].head(5)
print(ex[['pair', 'et', 'why', '豆包持仓数']].to_string(index=False))
