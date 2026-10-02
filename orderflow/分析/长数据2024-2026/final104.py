import pandas as pd, numpy as np
exec(open('compound.py').read().split("A=trades(")[0])
rules={'宽松 拿12h 挂单（程序现在用的）':((F.r_60<-.02)&(F.oi_60<-.03)&(F.sf_60>0),144,True,3),
       '严格 拿12h 挂单':((F.r_60<-.02)&(F.oi_60<-.05)&(F.sf_60>.05),144,True,3),
       '轧空追多 涨>3% 持仓降>2% 12h':((F.r_60>.03)&(F.oi_60<-.02),144,False,1)}
T={}
for k,(m,h,mk,sx) in rules.items():
    rows=trades(m,h,mk,sx,None); T[k]=rows
    r=np.array([x[2] for x in rows]); yrs=27/12
    y=pd.Series(r,index=pd.to_datetime([x[0] for x in rows],unit='ms').year).groupby(level=0).mean()*1e4
    print(f'{k}: {len(r)}笔 每天{len(r)/(yrs*365):.1f}笔 胜率{(r>0).mean()*100:.0f}% 每笔{r.mean()*1e4:+.0f}基点 PF{r[r>0].sum()/-r[r<0].sum():.2f} 各年{y.round(0).to_dict()}')
def sim(rows,f,cap=10):
    ev_=sorted([(t0,0,k) for k,(t0,t1,r) in enumerate(rows)]+[(t1,1,k) for k,(t0,t1,r) in enumerate(rows)])
    eq=100.0; size={}; curve=[]; skip=0
    for t,typ,k in ev_:
        if typ==0:
            if len(size)>=cap: skip+=1; continue
            size[k]=eq*f
        elif k in size: eq+=size.pop(k)*rows[k][2]; curve.append((t,eq))
    c=pd.Series([e for _,e in curve],index=pd.to_datetime([t for t,_ in curve],unit='ms'))
    m=c.resample('ME').last().pct_change()
    return c.iloc[-1],(c/c.cummax()-1).min(),(m<0).sum(),m.notna().sum(),skip
print('\n100U 起，滚利，最多同时 10 单：')
for k in rules:
    for f in (0.05,0.10):
        fin,dd,neg,nm,skip=sim(T[k],f)
        print(f'  {k} 每笔{f:.0%}: 27个月后 {fin:.0f}U 最大回撤 {dd*100:.0f}% 亏钱月 {neg}/{nm} 因满仓跳过 {skip}笔')
for f in (0.05,0.10):
    fin,dd,neg,nm,skip=sim(T['宽松 拿12h 挂单（程序现在用的）']+T['轧空追多 涨>3% 持仓降>2% 12h'],f)
    print(f'  宽松清洗 + 轧空追多 每笔{f:.0%}: 27个月后 {fin:.0f}U 最大回撤 {dd*100:.0f}% 亏钱月 {neg}/{nm}')
    fin,dd,neg,nm,skip=sim(T['严格 拿12h 挂单']+T['轧空追多 涨>3% 持仓降>2% 12h'],f)
    print(f'  严格清洗 + 轧空追多 每笔{f:.0%}: 27个月后 {fin:.0f}U 最大回撤 {dd*100:.0f}% 亏钱月 {neg}/{nm}')
