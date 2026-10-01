"""NFI 真实逐小时盯市：按每笔交易的实际加仓/减仓订单，算出组合每小时的浮动盈亏、真实回撤、最多占用资金。"""
import json, zipfile, glob, numpy as np, pandas as pd
import sys; F = sys.argv[1] if len(sys.argv) > 1 else '/home/user/ext/ft/data/okx/futures'
px = {}
def price(pair):
    if pair not in px:
        d = pd.read_feather(f"{F}/{pair.split('/')[0]}_USDT_USDT-1h-futures.feather"); px[pair] = d.set_index('date').close
    return px[pair]
for z in sorted(glob.glob(sys.argv[2] if len(sys.argv) > 2 else 'nfi/bt_*.zip')):
    Z = zipfile.ZipFile(z); n = [x for x in Z.namelist() if x.endswith('.json') and '_config' not in x][0]
    s = list(json.loads(Z.read(n))['strategy'].values())[0]; T = s['trades']
    idx = pd.date_range(pd.Timestamp(s['backtest_start'], tz='UTC'), pd.Timestamp(s['backtest_end'], tz='UTC'), freq='h')
    pnl = pd.Series(0.0, index=idx); used = pd.Series(0.0, index=idx); worst = []
    for t in T:
        d = -1 if t['is_short'] else 1; p = price(t['pair']).reindex(idx, method='ffill')
        amt = pd.Series(0.0, index=idx); cash = pd.Series(0.0, index=idx); cost = pd.Series(0.0, index=idx)
        for o in t['orders']:
            ts = pd.Timestamp(o['order_filled_timestamp'], unit='ms', tz='UTC').ceil('h')
            if ts > idx[-1]: continue
            side = 1 if o['ft_order_side'] == ('sell' if t['is_short'] else 'buy') else -1     # +1 加仓，-1 减仓
            a = o['amount'] * side; c = o['safe_price'] * o['amount']
            amt[ts:] += a; cash[ts:] -= d * side * c; cost[ts:] += side * c
        end = pd.Timestamp(t['close_timestamp'], unit='ms', tz='UTC').ceil('h') if t['close_timestamp'] else idx[-1] + pd.Timedelta('1h')
        live = (idx >= pd.Timestamp(t['open_timestamp'], unit='ms', tz='UTC').floor('h')) & (idx < end)
        v = (cash + d * amt * p).where(live, 0.0)
        after = idx >= end
        v[after] = t['profit_abs'] if t['close_timestamp'] else 0.0
        pnl += v.fillna(0); used += (amt * p).where(live, 0.0).abs().fillna(0)
        worst.append((v.where(live).min(), t['pair'], t['open_date'], t.get('close_date')))
    dd = (pnl.cummax() - pnl).max()
    w = min(worst)
    print(z.split('/')[-1][:30], f"笔数 {len(T)}  期末盈亏 {pnl.iloc[-1]:+.0f}U  真实最大回撤 {dd:.0f}U  最多同时占用 {used.max():.0f}U  "
          f"单笔最差浮亏 {w[0]:.0f}U（{w[1]} {w[2][:10]}）", flush=True)
