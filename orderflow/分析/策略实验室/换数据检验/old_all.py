"""2022-01 ~ 2023-12 币安数据（从没用过）检验，参数一个不改。
做多：清洗接盘 不过滤 / BTC 200 天线 / 持仓量 24h 过滤 / 两个一起
做空：多头摊平 + BTC 熊市 + 低点（s24 选中的）/ 候选（低点 + 全市场散户偏多，s26）/ A（+ 大户偏空，持仓门槛 3%，拿 24h）/ B（持仓门槛 3%，拿 24h）
每个做空都配一个随机做空对照：同样的市场状态（和同样的单币条件里不是"信号"的那部分）、同样止损和持仓，随机挑时间。
全市场状态用这 38 个币算（比 2024~2026 的 111 个币少）。"""
import sys, os, json, numpy as np, pandas as pd
sys.path.insert(0, '/home/user/ext/long/lab')
import data, sim, report
data.ROOT = '/home/user/ext/oos/f5'
import s00_flush as F0, s24_trap_tune as S24, s26_trap_ofregime as S26, s27_trap_more as S27
from data import flow
pf = report.pf
coins = [c for c in open('flush_old_coins.txt').read().split() if os.path.exists(f'f5/met/{c}.parquet') and os.path.exists(f'f5/spot/{c}.parquet')]
b = pd.read_parquet('old/BTCUSDT.parquet')
dd = b.c.groupby(b.ts // 86_400_000).last(); ma = dd.rolling(200).mean()
BEAR = ((dd < ma) & ma.notna()).shift(1).fillna(False).astype(bool)

# 全市场每小时中位数（和 of_ind 同口径：小时收完后才能用）
hrs = []
for c in coins:
    df = data.load(c)
    g = df.groupby(df.index // 3_600_000)
    h = pd.DataFrame({'c': g.c.last(), 'qv': g.qv.sum(), 'bq': g.bq.sum(), 'oi': g.oi.last(), 'ls': g.ls.last(), 'fund': g.fund.last()})
    z = lambda s: (s - s.rolling(168).mean()) / s.rolling(168).std()
    hrs.append(pd.DataFrame({'t': h.index * 3_600_000, 'perp_flow_24h': ((2 * h.bq - h.qv).rolling(24).sum() / h.qv.rolling(24).sum()).values,
                             'oi_24h': h.oi.pct_change(24).values, 'funding': h.fund.values, 'retail_ls_z': z(h.ls).values}))
M = pd.concat(hrs).groupby('t').median(); M.index = M.index + 3_600_000
S26._M = M

SHORTS = {'s24 多头摊平+BTC熊市+低点': (S24, {'d': 0.05, 'o': 0.05, 'x': 'low', 'sd': 0.05, 'hold': 144}),
          's26 候选 低点+全市场散户偏多': (S26, {'d': 0.05, 'x': 'low', 'm': 'ls', 'sd': 0.10, 'hold': 144}),
          's27 A +大户偏空 门槛3% 拿24h': (S27, {'c': 'tls', 'o': 0.03, 'hold': 288}),
          's27 B 门槛3% 拿24h': (S27, {'c': 'none', 'o': 0.03, 'hold': 288})}
out = {k: [] for k in SHORTS}; rnd = {k: [] for k in SHORTS}; FL = []
rng = np.random.default_rng(1)
for c in coins:
    raw = data.load(c)
    bear = pd.Series(raw.index.values // 86_400_000, index=raw.index).map(BEAR).fillna(False).astype(bool)
    # 做多：清洗接盘 + 过滤用的数据
    f = F0.prep(raw.copy())
    tr = pd.DataFrame(F0.trades(f, F0.GRID[0]), columns=sim.COLS)
    if len(tr):
        tr['oi24h'] = tr.t.map(pd.Series(f.oi.pct_change(288).values, index=f.index))
        tr['bull'] = ~tr.t.map(bear)
        FL.append(tr)
    for k, (S, p) in SHORTS.items():
        S.GRID = [p]
        df = S.prep(raw.copy())
        df['bear'] = bear.values
        out[k] += S.trades(df, p)
        if S is S24:
            ok = df.bear
        else:
            ok = (df.m_ls > 0) & ((df.tls_z < 0) if p.get('c') == 'tls' else True)
        idx = np.flatnonzero(ok.fillna(False).values[:-300])
        n = len([1 for x in out[k] if x[0] == df.attrs.get('name')])
        if len(idx):
            sd = p.get('sd', 0.10)
            rnd[k] += sim.run(df, np.sort(rng.choice(idx, size=min(len(idx), max(20, 5 * n)), replace=False)), -1, sd, hold=p['hold'], entry='market', cooldown=1)
    print(c, flush=True)

print('\n币', len(coins), '｜BTC 在 200 天线下方的天数占比', {y: round(v, 2) for y, v in BEAR.groupby(pd.to_datetime(BEAR.index * 86_400_000, unit='ms').year).mean().items() if y in (2022, 2023)})
def line(nm, T):
    if not len(T):
        return f'{nm}: 0 笔'
    y = T.groupby(pd.to_datetime(T.t, unit='ms').dt.year).ret.apply(lambda r: f'{len(r)}笔 PF {pf(r):.2f}').to_dict()
    cs = T.groupby('coin').ret.sum().sort_values(ascending=False)
    return (f'{nm}: {len(T)} 笔 胜率 {(T.ret > 0).mean():.0%} 每笔 {T.ret.mean() * 1e4:+.0f} 基点 PF {pf(T.ret):.2f} | {y} | 去前5币 PF {pf(T[~T.coin.isin(cs.index[:5])].ret):.2f}'
            f' | 100U(每笔10%) {report.portfolio(T, size=0.10).get("27个月后")} 回撤 {report.portfolio(T, size=0.10).get("最大回撤%")}%')
print('\n=== 做多：清洗接盘')
T = pd.concat(FL)
th = 0.01668
for nm, X in (('不过滤', T), ('BTC 200 天线', T[T.bull]), ('持仓量 24h ≤ 1.7%', T[T.oi24h <= th]), ('两个一起', T[(T.oi24h <= th) & T.bull])):
    print(line(nm, X))
print('\n=== 做空')
for k in SHORTS:
    X = pd.DataFrame(out[k], columns=sim.COLS); R = pd.DataFrame(rnd[k], columns=sim.COLS)
    print(line(k, X)); print('   随机做空对照:', len(R), '笔 PF', round(pf(R.ret), 2) if len(R) else None)
    X.to_parquet(f'old_{k.split()[0]}_{k.split()[1]}.parquet')
