import pandas as pd, numpy as np, glob
fs=sorted(glob.glob('/home/user/ext/v7rec/recorder_data/*.csv'))
df=pd.concat([pd.read_csv(f) for f in fs])
df=df.sort_values(['inst','ts']).drop_duplicates(['inst','ts'])
num=['open','high','low','close','buy_usdt','sell_usdt','imbalance','spread_bps','bid5_usdt','ask5_usdt','funding_rate','oi_usdt','liq_buy_usdt','liq_sell_usdt']
for c in num: df[c]=pd.to_numeric(df[c],errors='coerce')
# 只要分钟连续、有成交额的币
g=df.groupby('inst')
vol=g.apply(lambda x:(x.buy_usdt+x.sell_usdt).sum(), include_groups=False)
keep=vol[vol>5e7].index   # 两天成交额>5千万U
df=df[df.inst.isin(keep)].copy()
print('币数',len(keep))
out=[]
for inst,x in df.groupby('inst'):
    x=x.set_index('ts').sort_index()
    idx=np.arange(x.index.min(),x.index.max()+1,60000)
    x=x.reindex(idx)
    c=x.close.ffill()
    tot=(x.buy_usdt+x.sell_usdt).fillna(0)
    f=pd.DataFrame(index=x.index)
    f['inst']=inst
    f['r_1']=c.pct_change(1); f['r_5']=c.pct_change(5); f['r_15']=c.pct_change(15); f['r_60']=c.pct_change(60)
    d=(x.buy_usdt-x.sell_usdt).fillna(0)
    f['flow_1']=d/tot.replace(0,np.nan)
    f['flow_5']=d.rolling(5).sum()/tot.rolling(5).sum()
    f['flow_15']=d.rolling(15).sum()/tot.rolling(15).sum()
    f['imb']=x.imbalance
    f['imb_5']=x.imbalance.rolling(5,min_periods=3).mean()
    f['depth_ratio']=(x.bid5_usdt-x.ask5_usdt)/(x.bid5_usdt+x.ask5_usdt)
    f['vol_surge']=tot/tot.rolling(60,min_periods=30).mean()
    oi=x.oi_usdt.ffill()
    f['oi_5']=oi.pct_change(5); f['oi_15']=oi.pct_change(15)
    f['funding']=x.funding_rate.ffill()
    lq=(x.liq_sell_usdt.fillna(0)-x.liq_buy_usdt.fillna(0))   # 正=多单爆
    f['liq_net_5']=lq.rolling(5).sum()/ (tot.rolling(60,min_periods=30).mean()*5)
    f['liq_tot_5']=(x.liq_sell_usdt.fillna(0)+x.liq_buy_usdt.fillna(0)).rolling(5).sum()/(tot.rolling(60,min_periods=30).mean()*5)
    f['spread']=x.spread_bps
    rng=(x.high-x.low)/c
    f['vola_15']=rng.rolling(15).mean()
    for h in (5,15,60):
        f[f'fwd_{h}']=c.shift(-h)/c-1      # 未来 h 分钟收益（从这一分钟收盘开始）
    f['day']=pd.to_datetime(f.index,unit='ms').date
    out.append(f)
F=pd.concat(out).replace([np.inf,-np.inf],np.nan)
F.to_pickle('/home/user/ext/v7an/F.pkl')
print(F.shape)
