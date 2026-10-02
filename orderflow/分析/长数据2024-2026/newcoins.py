import pandas as pd, numpy as np
exec(open('compound.py').read().split("A=trades(")[0])
old=set(l.split()[0] for l in open('syms.txt') if l.split()[0] not in set(x.split()[0] for x in open('syms_new.txt')))
for name,(m,h,mk,sx) in {'严格清洗 12h 挂单':((F.r_60<-.02)&(F.oi_60<-.05)&(F.sf_60>.05),144,True,3),
                         '宽松清洗 12h 挂单':((F.r_60<-.02)&(F.oi_60<-.03)&(F.sf_60>0),144,True,3),
                         '轧空追多 3%/2% 12h':((F.r_60>.03)&(F.oi_60<-.02),144,False,1)}.items():
    for grp,mask in (('原来46个币',F.inst.isin(old)),('新加的币（没用来定过参数）',~F.inst.isin(old))):
        rows=trades(m&mask,h,mk,sx,None); r=np.array([x[2] for x in rows])
        if len(r)<20: print(name,grp,'太少'); continue
        y=pd.Series(r,index=pd.to_datetime([x[0] for x in rows],unit='ms').year).groupby(level=0).mean()*1e4
        print(f'{name} | {grp}: {len(r)}笔 胜率{(r>0).mean()*100:.0f}% 每笔{r.mean()*1e4:+.0f}基点 中位数{np.median(r)*1e4:+.0f} PF{r[r>0].sum()/-r[r<0].sum():.2f} 各年{y.round(0).to_dict()}')
