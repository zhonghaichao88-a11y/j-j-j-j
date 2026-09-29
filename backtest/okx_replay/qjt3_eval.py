"""严格递归 + 三层区间套 + 主观规则：第一年按事先规则挑选，第二年只检验被选中的那一个。
选择指标：扣成本后截尾平均（去掉最好、最差各 1%），第一年至少 60 笔。"""
import json,itertools,numpy as np,pandas as pd
from scipy import stats
df=pd.DataFrame(json.load(open('qjt3.json')))
split=df.ts.min()+(df.ts.max()-df.ts.min())//2
tr,te=df[df.ts<split],df[df.ts>=split]
print(f'入场 {len(df)} 笔，{df.inst.nunique()} 个币；分段 {pd.to_datetime(split,unit="ms").date()}；第一年 {len(tr)} / 第二年 {len(te)}')
ENTRY=['L2+L1+L0','L2+L0','L1+L0']
EXIT=['X1','X2','X3','X1_P','X1_T','X1_PT']
FILT={'无':lambda d:d.index==d.index,'强势选币':lambda d:d.rs>=0.5,'市场宽度':lambda d:d.breadth>=0.5,'强势+宽度':lambda d:(d.rs>=0.5)&(d.breadth>=0.5)}
def m(d,en,ex,fi):
    x=d[d[en]&FILT[fi](d)];y=x[ex]-x[ex+'_cost'];return x,y
def desc(y):
    if len(y)<5:return f'{len(y):4d}笔'
    return f'{len(y):4d}笔 平均{y.mean():+.3f} 截尾{stats.trim_mean(y,0.01):+.3f} 中位{y.median():+.3f} 胜率{(y>0).mean()*100:3.0f}%'
best=None;lines=[]
for en,ex,fi in itertools.product(ENTRY,EXIT,FILT):
    _,a=m(tr,en,ex,fi);_,b=m(te,en,ex,fi)
    if len(a)>=60:
        s=stats.trim_mean(a,0.01)
        if best is None or s>best[0]:best=(s,en,ex,fi)
    lines.append(f'  {en:9s} {ex:6s} {fi:5s} | 第一年 {desc(a)} | 第二年 {desc(b)}')
print('\n'.join(lines))
print('\n第一年按事先规则选中：',best)
if best:
    _,en,ex,fi=best;x,y=m(te,en,ex,fi)
    print('【第二年检验】',desc(y),f'95%区间 ±{1.96*y.std()/np.sqrt(len(y)):.3f}；去掉最好5笔平均 {np.sort(y)[:-5].mean():+.3f}；平均持有 {x[ex+"_h"].mean():.0f} 小时')
