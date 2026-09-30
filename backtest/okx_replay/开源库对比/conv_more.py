"""181 个新币（OKX 1小时K线，最多两年）→ freqtrade 合约格式（1h / 4h；资金费率按 0；标记价用K线）。"""
import json, os, numpy as np, pandas as pd
D = '/home/user/okx_data'; OUT = '/home/user/ext/ft/data/okx/futures'
ok = []
for inst in json.load(open(f'{D}/universe_more.json')):
    p = f'{D}/{inst}_1h.npz'
    if not os.path.exists(p): continue
    z = np.load(p); df = pd.DataFrame({k: z[k] for k in ('open', 'high', 'low', 'close', 'volume')})
    df['date'] = pd.to_datetime(z['ts'], unit='ms', utc=True); df = df.set_index('date')
    if len(df) < 24 * 60: continue
    base = inst.split('-')[0]
    for tf, rule in (('1h', None), ('4h', '4h')):
        g = df if rule is None else df.resample(rule, label='left', closed='left').agg(dict(open='first', high='max', low='min', close='last', volume='sum')).dropna()
        g.reset_index()[['date', 'open', 'high', 'low', 'close', 'volume']].to_feather(f'{OUT}/{base}_USDT_USDT-{tf}-futures.feather')
    df.reset_index()[['date', 'open', 'high', 'low', 'close', 'volume']].to_feather(f'{OUT}/{base}_USDT_USDT-1h-mark.feather')
    fr = pd.DataFrame({'date': pd.date_range(df.index[0].ceil('8h'), df.index[-1], freq='8h'), 'open': 0.0, 'high': 0.0, 'low': 0.0, 'close': 0.0, 'volume': 0.0})
    fr.to_feather(f'{OUT}/{base}_USDT_USDT-8h-funding_rate.feather')
    ok.append(base + '/USDT:USDT')
json.dump(ok, open('/home/user/ext/pairs_more.json', 'w')); print(len(ok))
