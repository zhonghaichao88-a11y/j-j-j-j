"""本金 100U、每笔用当时权益的 10%，两个打法一起跑，按时间顺序滚利（同时持仓时，各自按开仓那一刻的权益算仓位）。"""
import pandas as pd, numpy as np
exec(open('boost_flush.py').read().split("CONDS=")[0])
def trades(mask,hold,maker,stop_x,side_stop):
    E=ev(mask,hold); rows=[]
    for inst,ts,r60 in zip(E.inst,E.ts,E.r_60):
        T,O,L,C=kl(inst); i=np.searchsorted(T,ts)+1
        if i+hold>=len(T): continue
        if maker:
            e=C[i-1]
            if L[i]>e: continue
        else: e=O[i]
        st=e*(1-stop_x*abs(r60)); ex=C[i+hold-1]; c=.0005 if maker else .0012
        for j in range(i,i+hold):
            if L[j]<=st: ex=st*(1-.0005); c=.0009 if maker else .0012; break
        rows.append((T[i],T[i+hold-1],ex/e-1-c))
    return rows
A=trades((F.r_60<-.02)&(F.oi_60<-.03)&(F.sf_60>0),144,True,3,None)
B=trades((F.r_60>.03)&(F.oi_60<-.02),144,False,1,None)
for name,rows in (('只用清洗接盘',A),('清洗接盘 + 轧空追多',A+B)):
    ev_=sorted([(t0,0,k) for k,(t0,t1,r) in enumerate(rows)]+[(t1,1,k) for k,(t0,t1,r) in enumerate(rows)])
    eq=100.0; size={}; curve=[]
    for t,typ,k in ev_:
        if typ==0: size[k]=eq*0.10
        else: eq+=size.pop(k)*rows[k][2]; curve.append((t,eq))
    c=pd.Series([e for _,e in curve],index=pd.to_datetime([t for t,_ in curve],unit='ms'))
    m=c.resample('ME').last()
    dd=(c/c.cummax()-1).min()
    print(f'\n{name}：100U → 27 个月后 {c.iloc[-1]:.0f}U，最大回撤 {dd*100:.0f}%')
    print('  每季度末:', {str(k.to_period("Q")):round(v) for k,v in m.resample('QE').last().items()})
