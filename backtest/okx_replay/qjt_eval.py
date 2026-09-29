"""区间套结果评估：第一年挑选（事先定好的规则），第二年检验。"""
import json,sys,itertools,numpy as np,pandas as pd
from portfolio import fmt
lev=sys.argv[1];df=pd.DataFrame(json.load(open(f'qjt_{lev}.json')))
split=df.ts.min()+(df.ts.max()-df.ts.min())//2
print(f'[{lev}] 入场 {len(df)} 笔，{df.inst.nunique()} 个币；分段点 {pd.to_datetime(split,unit="ms").date()}')
tr,te=df[df.ts<split],df[df.ts>=split]
KINDS={'1类(趋势+盘整背驰)':['T1','T1P'],'只要趋势背驰1类':['T1'],'3类':['T3'],'全部':['T1','T1P','T3']}
def net(x,e):return x[f'{e}_R']-x.costR
grid=[]
for (kn,ks),za,e in itertools.product(KINDS.items(),(False,True),('E1','E2','E3')):
    m=lambda d,ks=ks,za=za:d.kind.isin(ks)&((d.zero_axis) if za else True)
    grid.append((kn+(' +MACD回0轴' if za else ''),e,m))
def row(d,m,e):
    x=d[m(d)];y=net(x,e)
    return len(x),(x[f'{e}_R'].mean() if len(x) else np.nan),(y.mean() if len(x) else np.nan),(1.96*y.std()/np.sqrt(len(y)) if len(y)>1 else np.nan),(x[f'{e}_h'].mean() if len(x) else np.nan)
print('\n组合                         出场  | 第一年: 笔数 扣前 扣后 | 第二年: 笔数 扣前 扣后 (95%区间) 平均持有小时')
best=None
for name,e,m in grid:
    a=row(tr,m,e);b=row(te,m,e)
    if a[0]>=100 and (best is None or a[2]>best[0]):best=(a[2],name,e)
    print(f'  {name:26s} {e} | {a[0]:4d} {a[1]:+.3f} {a[2]:+.3f} | {b[0]:4d} {b[1]:+.3f} {b[2]:+.3f} (±{b[3]:.3f}) {b[4]:.0f}h')
print('\n第一年事先规则选中：',best)
