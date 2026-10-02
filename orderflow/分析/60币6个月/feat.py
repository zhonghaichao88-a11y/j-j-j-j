"""每个币 5 分钟一条特征 + 未来收益。只用当时及以前的数据。"""
import pandas as pd, numpy as np, glob, os
rows=[]
for f in sorted(glob.glob('/home/user/ext/free/coins/*.parquet')):
    d=pd.read_parquet(f).sort_values('ts').drop_duplicates('ts')
    if len(d)<100000: continue
    d=d.set_index('ts')
    g=pd.DataFrame({'c':d.close})
    g['qv']=d.quote_volume; g['buy']=d.taker_buy_quote_volume
    for c in ['bd_-0.2','bd_+0.2','bd_-1.0','bd_+1.0','bd_-5.0','bd_+5.0']:
        g[c]=d[c] if c in d else np.nan
    for c in ['oi_usd','ls','top_ls','taker_ratio','funding']:
        g[c]=d[c].ffill() if c in d else np.nan
    g['hi']=d.high; g['lo']=d.low
    # 5分钟一根
    t5=(g.index//300000)*300000
    a=g.groupby(t5).agg(c=('c','last'),qv=('qv','sum'),buy=('buy','sum'),hi=('hi','max'),lo=('lo','min'),
        b02=('bd_-0.2','mean'),a02=('bd_+0.2','mean'),b1=('bd_-1.0','mean'),a1=('bd_+1.0','mean'),b5=('bd_-5.0','mean'),a5=('bd_+5.0','mean'),
        oi=('oi_usd','last'),ls=('ls','last'),top_ls=('top_ls','last'),tr=('taker_ratio','last'),funding=('funding','last'))
    a=a.reindex(np.arange(a.index.min(),a.index.max()+1,300000))
    c=a.c.ffill()
    F=pd.DataFrame(index=a.index)
    F['inst']=os.path.basename(f).split('.')[0]
    for n,k in (('r_5',1),('r_15',3),('r_60',12),('r_240',48)): F[n]=c.pct_change(k)
    F['flow_5']=(2*a.buy-a.qv)/a.qv
    F['flow_60']=(2*a.buy-a.qv).rolling(12).sum()/a.qv.rolling(12).sum()
    F['book_02']=(a.b02-a.a02)/(a.b02+a.a02)
    F['book_1']=(a.b1-a.a1)/(a.b1+a.a1)
    F['book_5']=(a.b5-a.a5)/(a.b5+a.a5)
    F['vol_surge']=a.qv/a.qv.rolling(288,min_periods=100).mean()
    oi=a.oi.ffill()
    F['oi_5']=oi.pct_change(1); F['oi_60']=oi.pct_change(12)
    z=F['oi_5']/F['oi_5'].rolling(288,min_periods=100).std()
    # 爆仓代理：持仓 5 分钟内异常大降（≤ -3 倍标准差）且价格同向大动 → 正数=多单被清（价格跌）
    F['liq_proxy']=np.where((z<=-3)&(F.r_5<0),1.0,np.where((z<=-3)&(F.r_5>0),-1.0,0.0))
    F['funding']=a.funding.ffill(); F['ls']=a.ls; F['top_ls']=a.top_ls; F['taker_ratio']=a.tr
    F['ls_chg']=a.ls.ffill().pct_change(12)
    F['vola']=((a.hi-a.lo)/c).rolling(12).mean()
    for h,k in ((15,3),(60,12),(240,48)): F[f'fwd_{h}']=c.shift(-k)/c-1
    F['month']=pd.to_datetime(F.index,unit='ms').strftime('%Y-%m')
    rows.append(F.astype({c:'float32' for c in F.columns if c not in('inst','month')}))
    print(F.inst.iloc[0],len(F),flush=True)
X=pd.concat(rows); X.index.name='ts'
X.replace([np.inf,-np.inf],np.nan).to_parquet('/home/user/ext/free/an/F.parquet')
print(X.shape)
