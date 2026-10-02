"""暴跌后做多：带止损的逐笔回测（1分钟K线，不看未来）。
信号：这个币过去 1 小时跌幅 ≤ 它过去 30 天的 0.5% 分位（阈值只用过去数据），同一个币 1 小时内只做一次。
进场：信号K线收盘后再等 1 分钟，用那根 1 分钟K线的开盘价（比收盘价晚一步，偏保守）。
出场：止损（按进场前 1 小时平均波动的倍数）、可选止盈、到时间平仓。成本 0.12%（双边吃单+滑点）。"""
import pandas as pd, numpy as np, sys
COST=0.0012
F=pd.read_parquet('F.parquet').reset_index().sort_values(['inst','ts'])
g=F.groupby('inst')
F['thr']=g['r_60'].transform(lambda s: s.rolling(8640,min_periods=2000).quantile(0.005).shift(1))
E=F[F.r_60<=F.thr][['inst','ts','vola','month']]
keep=[];last={}
for i,(inst,ts) in enumerate(zip(E.inst,E.ts)):
    if ts-last.get(inst,-1e15)>=3600000: keep.append(i); last[inst]=ts
E=E.iloc[keep]
M={}
for inst in E.inst.unique():
    d=pd.read_parquet(f'/home/user/ext/free/coins/{inst}.parquet',columns=['ts','open','high','low','close']).sort_values('ts').drop_duplicates('ts')
    M[inst]=(d.ts.values,d.open.values,d.high.values,d.low.values,d.close.values)
def run(stop_k,tp_r,hold_min):
    out=[]
    for inst,ts,vola,month in E.itertuples(index=False):
        T,O,H,L,C=M[inst]
        i=np.searchsorted(T,ts+300000+60000)
        if i>=len(T) or T[i]!=ts+360000 or np.isnan(vola): continue
        px=O[i]; sd=stop_k*vola*px; stop=px-sd; tp=px+tp_r*sd if tp_r else np.inf
        j_end=min(i+hold_min,len(T)-1); ex=C[j_end]
        for j in range(i,j_end+1):
            if L[j]<=stop: ex=stop*(1-0.0005); break     # 止损多滑 0.05%
            if H[j]>=tp: ex=tp; break
        r=(ex/px-1)-COST
        out.append((ts,month,r,r/(sd/px)))
    return pd.DataFrame(out,columns=['ts','month','ret','R'])
res=[]
for sk in (1,2,3,5):
    for tp in (0,2,3):
        for hm in (60,240):
            R=run(sk,tp,hm)
            pf=R.ret[R.ret>0].sum()/-R.ret[R.ret<0].sum()
            bym=R.groupby('month').ret.mean()*1e4
            day=R.assign(d=R.ts//86400000).groupby('d').R.sum()*0.005   # 每笔风险 0.5%
            eq=day.cumsum(); dd=(eq-eq.cummax()).min()
            res.append(dict(止损倍数=sk,止盈R=tp or '无',持有分钟=hm,笔数=len(R),胜率=round((R.ret>0).mean()*100),
                            每笔平均基点=round(R.ret.mean()*1e4,1),PF=round(pf,2),赚钱月数=f'{(bym>0).sum()}/{len(bym)}',
                            最差月基点=round(bym.min(),1),每笔风险0点5的总收益pct=round(eq.iloc[-1]*100,1),最大回撤pct=round(dd*100,1)))
pd.set_option('display.width',250)
print(pd.DataFrame(res).to_string(index=False))
