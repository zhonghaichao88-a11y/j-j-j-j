"""挂单抓插针（不看未来）：
每分钟把限价买单挂在"60 分钟前的价格 ×(1 + 阈值×倍数)"，阈值 = 这个币过去 30 天 1 小时跌幅的 0.5% 分位（只用过去数据）。
最低价严格低于挂单价才算成交（保守），成交价就是挂单价，挂单手续费 0.02%。
出场：止盈挂单（0.02%）；止损、到时间用市价（0.05% + 滑点 0.02%，止损再多滑 0.05%）。同一个币成交后 1 小时内不再挂。"""
import pandas as pd, numpy as np, sys
F=pd.read_parquet('F.parquet').reset_index()[['inst','ts','r_60']].sort_values(['inst','ts'])
F['thr']=F.groupby('inst')['r_60'].transform(lambda s: s.rolling(8640,min_periods=2000).quantile(0.005).shift(1))
MK,TK,SL=0.0002,0.0007,0.0005
def coin(inst,Fi,mult,tp_f,sl_f,hold):
    d=pd.read_parquet(f'/home/user/ext/free/coins/{inst}.parquet',columns=['ts','open','high','low','close']).sort_values('ts').drop_duplicates('ts').reset_index(drop=True)
    T,H,L,C=d.ts.values,d.high.values,d.low.values,d.close.values
    thr=pd.Series(Fi.thr.values,index=Fi.ts.values).reindex(T-(T%300000)).values   # 当前 5 分钟段用的阈值（算的时候已经往后挪了一段）
    ref=np.r_[np.full(61,np.nan),C[:-61]]                                            # 61 分钟前的收盘价
    lim=ref*(1+thr*mult)
    cand=np.where(L<lim)[0]
    out=[];busy=-1
    for i in cand:
        if i<=busy: continue
        px=lim[i]; drop=-thr[i]*mult
        tp=px*(1+drop*tp_f); st=px*(1-drop*sl_f)
        end=min(i+hold,len(C)-1); ex=None; j=i
        # 成交那根：只认收盘是否已经跌破止损（不知道先后，按坏的算）
        if L[i]<=st: ex=st*(1-SL); cost=MK+TK
        else:
            for j in range(i+1,end+1):
                if L[j]<=st: ex=st*(1-SL); cost=MK+TK; break
                if H[j]>tp: ex=tp; cost=MK+MK; break
        if ex is None: ex=C[end]; cost=MK+TK; j=end
        out.append((T[i],ex/px-1-cost,drop*sl_f)); busy=max(i+60,j)
    return out
res=[]
for mult in (1.0,1.3,1.6):
  for tp_f,sl_f in ((0.3,1.0),(0.5,1.0),(0.5,0.5),(1.0,1.0)):
    for hold in (60,240):
        rows=[]
        for inst,Fi in F.groupby('inst'):
            for t,r,risk in coin(inst,Fi,mult,tp_f,sl_f,hold): rows.append((t,r,risk))
        R=pd.DataFrame(rows,columns=['ts','ret','risk'])
        R['month']=pd.to_datetime(R.ts,unit='ms').dt.strftime('%Y-%m')
        pf=R.ret[R.ret>0].sum()/-R.ret[R.ret<0].sum()
        bym=R.groupby('month').ret.mean()
        day=(R.ret/R.risk*0.005).groupby(R.ts//86400000).sum(); eq=day.cumsum()
        res.append(dict(挂单倍数=mult,止盈=tp_f,止损=sl_f,持有=hold,笔数=len(R),胜率=round((R.ret>0).mean()*100),
                        每笔基点=round(R.ret.mean()*1e4,1),PF=round(pf,2),赚钱月=f'{(bym>0).sum()}/{len(bym)}',
                        风险05总收益=round(eq.iloc[-1]*100,1),最大回撤=round((eq-eq.cummax()).min()*100,1)))
        print(res[-1],flush=True)
pd.set_option('display.width',250)
print(pd.DataFrame(res).to_string(index=False))
