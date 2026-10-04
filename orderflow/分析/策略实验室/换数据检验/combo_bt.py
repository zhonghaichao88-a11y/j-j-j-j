"""两个打法一起跑的组合回测（2022-01 ~ 2026-09）：清洗接盘（大盘过滤 + 持仓量 24h 过滤）+ 多头摊平做空（散户偏多 + 清洗平仓）。
每笔用当时权益的 10%，最多同时 N 单，同一个币同时只拿一单；单日亏损到上限后当天不再开新单（按开单前的已实现盈亏算）。"""
import sys, os, numpy as np, pandas as pd
sys.path.insert(0, '/home/user/ext/long/lab')
import data, sim, report
import s00_flush as F0
data.ROOT = '/home/user/ext/oos/f5'
b = pd.read_parquet('old/BTCUSDT.parquet'); dd = b.c.groupby(b.ts // 86_400_000).last(); ma = dd.rolling(200).mean()
BULL = ((dd > ma) & ma.notna()).shift(1).fillna(False).astype(bool)
rows = []
for c in open('flush_old_coins.txt').read().split():
    f = F0.prep(data.load(c))
    tr = pd.DataFrame(F0.trades(f, F0.GRID[0]), columns=sim.COLS)
    if len(tr):
        tr['oi24h'] = tr.t.map(pd.Series(f.oi.pct_change(288).values, index=f.index)); tr['bull'] = tr.t.map(lambda t: BULL.get(t // 86_400_000, False))
        rows.append(tr)
FO = pd.concat(rows); FO = FO[FO.bull & (FO.oi24h <= 0.01668)]
FN = pd.read_parquet('/home/user/ext/long/lab/results/flush_of_feats.parquet'); FN = FN[FN.bull & (FN.oi24h <= 0.01668)]
FL = pd.concat([FO, FN])[['coin', 't', 't_in', 't_out', 'ret']].assign(k='清洗接盘做多')
S = pd.read_parquet('/home/user/ext/long/lab/results/s29_pick.parquet')
SH = S.assign(t_in=S.t + 300_000, t_out=S.t + 300_000 + S.bars * 300_000, k='摊平做空')[['coin', 't', 't_in', 't_out', 'ret', 'k']]
ALL = pd.concat([FL, SH]).sort_values('t_in').reset_index(drop=True)


def sim_port(T, cap, size=0.10, day_stop=None, start=100.0):
    ev = sorted([(r.t_in, 1, i) for i, r in T.iterrows()] + [(r.t_out, 0, i) for i, r in T.iterrows()])
    eq, pos, curve, busy = start, {}, [], {}
    day, day_start, day_pnl = None, start, 0.0
    for t, typ, i in ev:
        d = t // 86_400_000
        if d != day:
            day, day_start, day_pnl = d, eq, 0.0
        r = T.loc[i]
        if typ == 1:
            if len(pos) >= cap or busy.get(r.coin, 0) > t:
                continue
            if day_stop and day_pnl <= -day_stop * day_start:
                continue
            pos[i] = eq * size; busy[r.coin] = r.t_out
        elif i in pos:
            pnl = pos.pop(i) * r.ret; eq += pnl; day_pnl += pnl
            curve.append((t, eq))
    c = pd.Series([e for _, e in curve], index=pd.to_datetime([t for t, _ in curve], unit='ms'))
    y = c.resample('YE').last(); y = pd.concat([pd.Series([start], index=[c.index[0] - pd.Timedelta(days=1)]), y])
    m = c.resample('ME').last().pct_change()
    return {'100U变成': round(c.iloc[-1]), '最大回撤%': round((c / c.cummax() - 1).min() * 100), '亏钱月': f'{int((m < 0).sum())}/{int(m.notna().sum())}',
            '每年': {k.year: f'{v:+.0%}' for k, v in (y.pct_change().dropna()).items()}}


pf = report.pf
for nm, X in (('清洗接盘做多', FL), ('摊平做空', SH), ('两个一起', ALL)):
    yy = X.groupby(pd.to_datetime(X.t, unit='ms').dt.year).ret.apply(lambda r: f'{len(r)}笔 PF{pf(r):.2f}').to_dict()
    print(f'{nm}: {len(X)} 笔 PF {pf(X.ret):.2f} | {yy}')
print()
for cap in (2, 4, 10):
    for ds in (None, 0.03, 0.05):
        for nm, X in (('只清洗接盘', FL.reset_index(drop=True)), ('只摊平做空', SH.reset_index(drop=True)), ('两个一起', ALL)):
            if ds is not None and nm != '两个一起':
                continue
            print(f'最多 {cap:2d} 单 | 单日亏损上限 {"不设" if ds is None else f"{ds:.0%}"} | {nm:6s}', sim_port(X, cap, day_stop=ds))
