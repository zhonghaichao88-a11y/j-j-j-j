"""补 freqtrade 数据：12h（现货+合约，从 5m 合成）与 1m（仅现货，1分钟策略都是现货；来自 1m2y）。"""
import json, os, numpy as np, pandas as pd
D = '/home/user/okx_data'; OUT = '/home/user/ext/ft/data/okx'
for inst in json.load(open(f'{D}/universe_5m40.json')):
    base = inst.split('-')[0]
    z = np.load(f'{D}/{inst}_5m2y.npz')
    df = pd.DataFrame({k: z[k] for k in ('open', 'high', 'low', 'close', 'volume')}); df['date'] = pd.to_datetime(z['ts'], unit='ms', utc=True)
    g = df.set_index('date').resample('12h', label='left', closed='left').agg(dict(open='first', high='max', low='min', close='last', volume='sum')).dropna().reset_index()
    g = g[['date', 'open', 'high', 'low', 'close', 'volume']]
    g.to_feather(f'{OUT}/{base}_USDT-12h.feather'); g.to_feather(f'{OUT}/futures/{base}_USDT_USDT-12h-futures.feather')
    p = f'{D}/{inst}_1m2y.npz'
    if os.path.exists(p):
        z = np.load(p)
        m = pd.DataFrame({k: z[k] for k in ('open', 'high', 'low', 'close', 'volume')}); m.insert(0, 'date', pd.to_datetime(z['ts'], unit='ms', utc=True))
        m.to_feather(f'{OUT}/{base}_USDT-1m.feather')
    print(inst, flush=True)
