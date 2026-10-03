"""100U 本金滚利（freqtrade 每单按当时可用余额 / 剩余仓位数开）：期末余额、每月月底余额、逐小时盯市的真实回撤（%）"""
import json, zipfile, glob, numpy as np, pandas as pd
F = 'ft/data_all/okx/futures'
NAMES = {'A': '原版', 'C': '最多补 3 次', 'D': '最多补 3 次 + 亏 30% 砍', 'E': '不补仓 + 亏 30% 砍'}
px = {}
def price(pair):
    if pair not in px:
        px[pair] = pd.read_feather(f"{F}/{pair.split('/')[0]}_USDT_USDT-1h-futures.feather").set_index('date').close
    return px[pair]
for t, name in NAMES.items():
    zs = glob.glob(f'nfi/u100/{t}*.zip')
    if not zs: print(name, '还没结果'); continue
    Z = zipfile.ZipFile(zs[0]); n = [x for x in Z.namelist() if x.endswith('.json') and '_config' not in x][0]
    s = list(json.loads(Z.read(n))['strategy'].values())[0]; T = s['trades']
    D = pd.DataFrame(T)
    idx = pd.date_range(pd.Timestamp(s['backtest_start'], tz='UTC'), pd.Timestamp(s['backtest_end'], tz='UTC'), freq='h')
    pnl = np.zeros(len(idx))
    for tr in T:
        d = -1 if tr['is_short'] else 1
        p = price(tr['pair']).reindex(idx, method='ffill').values
        amt = np.zeros(len(idx)); cash = np.zeros(len(idx))
        for o in tr['orders']:
            k = idx.searchsorted(pd.Timestamp(o['order_filled_timestamp'], unit='ms', tz='UTC').ceil('h'))
            if k >= len(idx): continue
            side = 1 if o['ft_order_side'] == ('sell' if tr['is_short'] else 'buy') else -1
            amt[k:] += o['amount'] * side; cash[k:] -= d * side * o['safe_price'] * o['amount']
        k0 = idx.searchsorted(pd.Timestamp(tr['open_timestamp'], unit='ms', tz='UTC').floor('h'))
        k1 = idx.searchsorted(pd.Timestamp(tr['close_timestamp'], unit='ms', tz='UTC').ceil('h')) if tr['close_timestamp'] else len(idx)
        v = np.nan_to_num(cash + d * amt * p)
        pnl[k0:k1] += v[k0:k1]; pnl[k1:] += tr['profit_abs']
    eq = pd.Series(100 + pnl, index=idx)
    dd = (eq / eq.cummax() - 1); k = dd.values.argmin()
    me = eq.resample('ME').last()
    print(f'\n== {name}：100U → {s["final_balance"]:.1f}U（{(s["final_balance"]/100-1)*100:+.0f}%），{len(D)} 笔，胜率 {(D.profit_abs>0).mean()*100:.1f}%')
    print(f'   逐小时盯市最大回撤 {dd.min()*100:.1f}%（{idx[k]:%Y-%m-%d}，当时账户 {eq.iloc[k]:.1f}U）；最低时账户 {eq.min():.1f}U')
    print('   每月月底账户（含浮盈浮亏）', {f'{i:%Y-%m}': round(v, 1) for i, v in me.items()})
    print(f'   最差一笔 {D.profit_abs.min():.1f}U（{D.loc[D.profit_abs.idxmin(), "pair"]}）；每笔开仓金额 中位 {D.stake_amount.median():.1f}U 最多 {D.max_stake_amount.max():.1f}U')
