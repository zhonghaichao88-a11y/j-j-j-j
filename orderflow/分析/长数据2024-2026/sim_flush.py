"""组合层面模拟：每笔固定用权益的 10% 开仓（不加杠杆时的风险），下一根开盘进场，拿 4 小时，紧急止损 = 3 倍那次跌幅。"""
import pandas as pd, numpy as np
F=pd.read_parquet('F2.parquet').reset_index()
def ev(mask,gap=48):
    E=F[mask.fillna(False).values].sort_values(['inst','ts']); keep=[];last={}
    for i,(inst,ts) in enumerate(zip(E.inst,E.ts)):
        if ts-last.get(inst,-1e15)>=gap*300000: keep.append(i); last[inst]=ts
    return E.iloc[keep]
K={}
def kl(inst):
    if inst not in K:
        d=pd.read_parquet(f'k/{inst}.parquet',columns=['ts','open','high','low','close']).sort_values('ts').drop_duplicates('ts').reset_index(drop=True)
        K[inst]=(d.ts.values,d.open.values,d.low.values,d.close.values)
    return K[inst]
for name,m in (('A 跌>2% 持仓降>3% 现货在买',(F.r_60<-.02)&(F.oi_60<-.03)&(F.sf_60>0)),
               ('B 跌>2% 持仓降>5% 现货主动>0.05',(F.r_60<-.02)&(F.oi_60<-.05)&(F.sf_60>.05))):
    E=ev(m); rows=[]
    for inst,ts,r60 in zip(E.inst,E.ts,E.r_60):
        T,O,L,C=kl(inst); i=np.searchsorted(T,ts)+1
        if i+48>=len(T): continue
        e=O[i]; st=e*(1-3*abs(r60)); ex=C[i+47]; why='到时间'
        for j in range(i,i+48):
            if L[j]<=st: ex=st*(1-.0005); why='止损'; break
        rows.append((T[i],T[i+47],ex/e-1-.0012,why))
    R=pd.DataFrame(rows,columns=['t0','t1','r','why']).sort_values('t1')
    # 同时持仓数
    ev_=sorted([(a,1) for a in R.t0]+[(b,-1) for b in R.t1]); cur=mx=0
    for _,x in ev_: cur+=x; mx=max(mx,cur)
    eq=(1+0.10*R.r).cumprod(); dd=(eq/eq.cummax()-1).min()
    months=(R.t1.max()-R.t0.min())/86400000/30.4
    pf=R.r[R.r>0].sum()/-R.r[R.r<0].sum()
    print(f'{name}: {len(R)}笔（每月约{len(R)/months:.0f}笔） 胜率{(R.r>0).mean()*100:.0f}% 每笔{R.r.mean()*1e4:+.1f}基点 PF{pf:.2f} '
          f'止损{(R.why=="止损").mean()*100:.0f}% 最差一笔{R.r.min()*100:.1f}% | 每笔用10%仓位: 27个月总收益{(eq.iloc[-1]-1)*100:.0f}% 最大回撤{dd*100:.1f}% 最多同时{mx}单')
    y=R.groupby(pd.to_datetime(R.t1,unit='ms').dt.year).r.agg(['count','mean'])
    print('   按年:', {k:(int(v['count']),round(v['mean']*1e4,1)) for k,v in y.iterrows()})
