import pandas as pd, numpy as np, sys, glob, os
sys.path.insert(0,'/home/user/j-j-j-j/orderflow/分析/策略实验室'); sys.path.insert(0,'/home/user/j-j-j-j/分析/策略实验室/TradingView热门策略')
import strats, tvsim, report as R, pine as P, runner
D='/home/user/ext/ft/data/okx/futures/'
def load(f):
    d=pd.read_feather(f); d=d.rename(columns={'open':'o','high':'h','low':'l','close':'c','volume':'v'})
    d['t']=d.date.values.astype('datetime64[ms]').astype(np.int64); return d[['t','o','h','l','c','v']].reset_index(drop=True)
binance=set(l.split()[0] for l in open('/home/user/ext/long/syms.txt'))
def st1h(df):
    k=P.sma(P.stoch(df.c,df.h,df.l,50),25)
    return dict(le=strats.B(P.cross_over(k,50)), lx=strats.B(P.cross_under(k,60)), long_only=True)
rows=[]
for c in runner.D.coins():
    d=runner.frame(c,60).reset_index(names='t'); r=tvsim.run(d.set_index('t'),**st1h(d))
    if len(r): rows.append(pd.DataFrame({'coin':c,'ret':r[:,3],'bars':r[:,5]}))
B1=pd.concat(rows); print('币安 1h 换算版 PF',round(R.pf(B1.ret),2),len(B1),'持仓中位小时',B1.bars.median())
rows=[]
for f in sorted(glob.glob(D+'*-1h-futures.feather')):
    coin=os.path.basename(f).split('_')[0]
    if coin in binance: continue
    d=load(f)
    if len(d)<1000: continue
    r=tvsim.run(d.set_index('t'),**st1h(d))
    if len(r): rows.append(pd.DataFrame({'coin':coin,'ret':r[:,3],'bars':r[:,5]}))
T=pd.concat(rows); T=T[np.isfinite(T.ret)]
cs=T.groupby('coin').ret.sum().sort_values(ascending=False)
print('欧易新币', T.coin.nunique(),'个',len(T),'笔 PF',round(R.pf(T.ret),2),'去掉最赚5币',round(R.pf(T[~T.coin.isin(cs.index[:5])].ret),2), cs.index[:5].tolist())
rng=np.random.default_rng(0); rr=[]
for coin,g in T.groupby('coin'):
    d=load(D+f'{coin}_USDT_USDT-1h-futures.feather'); O=d.o.values; n=len(O)
    for b in g.bars.values.astype(int):
        for _ in range(5):
            i=rng.integers(300,max(301,n-b-2)); j=min(i+b,n-1)
            rr.append(O[j]/O[i]-1-0.0014)
print('随机做多(同币同持仓) PF',round(R.pf(np.array(rr)),2),'每笔基点',round(np.mean(rr)*1e4,1),'| 策略每笔基点',round(T.ret.mean()*1e4,1))
