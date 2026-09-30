import json, os, numpy as np, pandas as pd
D='/home/user/okx_data'; OUT='/home/user/ext/ft/data/okx'
os.makedirs(OUT, exist_ok=True); os.makedirs(OUT+'/futures', exist_ok=True)
insts=json.load(open(f'{D}/universe_5m40.json'))
RULE={'5m':'5min','15m':'15min','30m':'30min','1h':'1h','4h':'4h','1d':'1D'}
for inst in insts:
    z=np.load(f'{D}/{inst}_5m2y.npz')
    df=pd.DataFrame({k:z[k] for k in ('open','high','low','close','volume')}); df['date']=pd.to_datetime(z['ts'],unit='ms',utc=True)
    df=df.set_index('date')
    base=inst.split('-')[0]
    for tf,rule in RULE.items():
        g=df if tf=='5m' else df.resample(rule,label='left',closed='left').agg(dict(open='first',high='max',low='min',close='last',volume='sum')).dropna()
        g=g.reset_index()[['date','open','high','low','close','volume']]
        g.to_feather(f'{OUT}/{base}_USDT-{tf}.feather')
        g.to_feather(f'{OUT}/futures/{base}_USDT_USDT-{tf}-futures.feather')
        if tf=='1h':
            g.to_feather(f'{OUT}/futures/{base}_USDT_USDT-1h-mark.feather')
    # 资金费率：没有完整历史，按 0 处理（每 8 小时一条）
    fr=pd.DataFrame({'date':pd.date_range(df.index[0].ceil('8h'),df.index[-1],freq='8h'),'open':0.0,'high':0.0,'low':0.0,'close':0.0,'volume':0.0})
    fr.to_feather(f'{OUT}/futures/{base}_USDT_USDT-8h-funding_rate.feather')
    print(inst, df.index[0].date(), df.index[-1].date(), flush=True)
