"""清洗接盘：用 2022-01 ~ 2023-12 的币安数据重新跑（参数完全不动），再加 BTC 200 天均线过滤"""
import sys, os, pandas as pd, numpy as np
sys.path.insert(0, '/home/user/ext/long/lab')
import data, report, s00_flush as S
data.ROOT = '/home/user/ext/oos/f5'
coins = [c for c in open('flush_old_coins.txt').read().split() if os.path.exists(f'f5/met/{c}.parquet') and c != 'BTC']
rows = []
for c in coins:
    df = S.prep(data.load(c))
    rows += S.trades(df, S.GRID[0])
import sim
T = pd.DataFrame(rows, columns=sim.COLS)
b = pd.read_parquet('old/BTCUSDT.parquet'); d = b.c.groupby(b.ts // 86_400_000).last()
ma = d.rolling(200).mean(); bull = ((d > ma) & ma.notna()).shift(1)
T['bull'] = (T.t // 86_400_000).map(bull)
pf = report.pf
def show(nm, X):
    if not len(X): print(nm, '0 笔'); return
    q = pd.to_datetime(X.t, unit='ms').dt.to_period('Q'); byq = X.groupby(q).ret.mean()
    print(f'{nm}: {len(X)} 笔 {X.coin.nunique()} 币 胜率 {(X.ret>0).mean()*100:.0f}% 每笔 {X.ret.mean()*100:+.2f}% PF {pf(X.ret):.2f} '
          f'| 不算资金费 PF {pf(X.ret+X.fund):.2f} | 赚钱季度 {(byq>0).sum()}/{len(byq)} | 100U→{report.portfolio(X).get("27个月后")}U 回撤 {report.portfolio(X).get("最大回撤%")}%')
print('币', len(coins))
show('不过滤', T); show('大盘多头（BTC 在 200 天线上）', T[T.bull == True]); show('大盘空头', T[T.bull == False])
for y, g in T.groupby(pd.to_datetime(T.t, unit='ms').dt.year):
    show(f'  {y} 不过滤', g); show(f'  {y} 多头', g[g.bull == True])
print('BTC 多头天数占比', {y: round(v, 2) for y, v in bull.groupby(pd.to_datetime(bull.index * 86_400_000, unit='ms').year).mean().items() if y in (2022, 2023)})
T.to_parquet('T_flush_old.parquet')
