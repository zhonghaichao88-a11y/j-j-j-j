"""NFI 全币复测汇总：每笔 100U、仓位不限，所以各批结果可以直接相加。逐小时盯市算整个组合的真实浮亏。"""
import json, zipfile, glob, sys, os, numpy as np, pandas as pd
F = 'ft/data_all/okx/futures'
OLD = {p.split('/')[0] for p in json.load(open('nfi/config_nfiU.json'))['exchange']['pair_whitelist']}
px = {}
def price(pair):
    if pair not in px:
        px[pair] = pd.read_feather(f"{F}/{pair.split('/')[0]}_USDT_USDT-1h-futures.feather").set_index('date').close
    return px[pair]
for tag, name in (('A', '原版（只加了滑点和资金费）'), ('B', '加止损（保证金亏 50% 就砍）')):
    zs = sorted(glob.glob(f'nfi/all10/cfg10{tag}_*.zip'))
    if not zs: print(name, '没有结果'); continue
    T, start, end, npairs = [], None, None, 0
    for z in zs:
        Z = zipfile.ZipFile(z); n = [x for x in Z.namelist() if x.endswith('.json') and '_config' not in x][0]
        s = list(json.loads(Z.read(n))['strategy'].values())[0]; T += s['trades']
        start, end = s['backtest_start'], s['backtest_end']
    D = pd.DataFrame(T)
    D['coin'] = D.pair.str.split('/').str[0]
    D['n_entries'] = D.orders.apply(lambda o: sum(1 for x in o if x['ft_is_entry']))
    print(f'\n===== {name}：{len(zs)} 批，{D.coin.nunique()} 个币出过单 =====')
    for nm, X in (('全部', D), ('只看做多', D[~D.is_short]), ('只看做空', D[D.is_short]), ('原来测过的 40 币', D[D.coin.isin(OLD)]), ('新加的币', D[~D.coin.isin(OLD)])):
        if not len(X): continue
        print(f'{nm}: {len(X)} 笔 胜率 {(X.profit_abs>0).mean()*100:.1f}% 合计 {X.profit_abs.sum():+.0f}U 每笔 {X.profit_abs.mean():+.2f}U '
              f'最差一笔 {X.profit_abs.min():.0f}U 资金费合计 {X.funding_fees.sum() if "funding_fees" in X else float("nan"):+.0f}U')
    print('出场原因', D.exit_reason.value_counts().head(8).to_dict())
    print('补仓次数：中位', int(D.n_entries.median()), ' 90%', int(D.n_entries.quantile(.9)), ' 最多', int(D.n_entries.max()))
    by = D.groupby('coin').profit_abs.sum().sort_values()
    print('亏钱的币', (by < 0).sum(), '/', len(by), ' 最亏 5 个', by.head(5).round(0).to_dict())
    print('最差 5 笔', D.nsmallest(5, 'profit_abs')[['pair', 'open_date', 'close_date', 'profit_abs', 'n_entries', 'exit_reason']].to_string(index=False))
    D['m'] = pd.to_datetime(D.close_date).dt.strftime('%Y-%m')
    print('每月（按平仓）', D.groupby('m').profit_abs.sum().round(0).to_dict())
    # 逐小时盯市
    idx = pd.date_range(pd.Timestamp(start, tz='UTC'), pd.Timestamp(end, tz='UTC'), freq='h')
    pnl = np.zeros(len(idx)); used = np.zeros(len(idx))
    for t in T:
        d = -1 if t['is_short'] else 1
        p = price(t['pair']).reindex(idx, method='ffill').values
        amt = np.zeros(len(idx)); cash = np.zeros(len(idx))
        for o in t['orders']:
            k = idx.searchsorted(pd.Timestamp(o['order_filled_timestamp'], unit='ms', tz='UTC').ceil('h'))
            if k >= len(idx): continue
            side = 1 if o['ft_order_side'] == ('sell' if t['is_short'] else 'buy') else -1
            amt[k:] += o['amount'] * side; cash[k:] -= d * side * o['safe_price'] * o['amount']
        k0 = idx.searchsorted(pd.Timestamp(t['open_timestamp'], unit='ms', tz='UTC').floor('h'))
        k1 = idx.searchsorted(pd.Timestamp(t['close_timestamp'], unit='ms', tz='UTC').ceil('h')) if t['close_timestamp'] else len(idx)
        v = np.nan_to_num(cash + d * amt * p)
        pnl[k0:k1] += v[k0:k1]; pnl[k1:] += t['profit_abs']
        used[k0:k1] += np.nan_to_num(np.abs(amt * p))[k0:k1]
    s = pd.Series(pnl, index=idx)
    dd = (s.cummax() - s); k = dd.values.argmax()
    print(f'逐小时盯市：期末 {s.iloc[-1]:+.0f}U，最大回撤 {dd.max():.0f}U（{idx[k]:%Y-%m-%d}），最多同时占用 {used.max()/3:.0f}U 保证金（3 倍杠杆）')
