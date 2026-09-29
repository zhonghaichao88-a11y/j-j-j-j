"""全新检验段（2025-09 至 2026-03）：规则与阈值完全沿用之前，不做任何调整。"""
import json,numpy as np,pandas as pd
from portfolio import sim,fmt,base_rule
RISK_MIN=0.0172
df=pd.DataFrame(json.load(open('research_old.json')));df=df[df.complete]
print('检验段信号',len(df),'时间',pd.to_datetime(df.ts.min(),unit='ms').date(),'~',pd.to_datetime(df.ts.max(),unit='ms').date(),'币数',df.inst.nunique())
EX=['tp2_h48','trail2atr_h192','hold48','hold96','hold144']
def row(x,title):
    cells=' '.join(f'{e}:{x["s2_"+e].mean():+.3f}/{(x["s2_"+e]-x.s2_costR).mean():+.3f}' for e in EX)
    print(f'  {title:26s} {len(x):5d}笔 {cells}')
print('每笔平均（扣成本前/后）')
row(df,'全部信号')
row(df[df.label=='3买'],'只做3买卖')
b=df[base_rule(df,RISK_MIN)];row(b,'规则: 4h+BTC顺势+3买卖+宽止损')
y=b.s2_hold96-b.s2_costR
print(f'  规则 hold96 扣成本后 95%区间 ±{1.96*y.std()/np.sqrt(len(y)):.3f}；做多 {y[b.side==1].mean():+.3f} 做空 {y[b.side==-1].mean():+.3f}')
m=pd.to_datetime(b.ts,unit='ms').dt.strftime('%y-%m');print('  按月(hold96):',' '.join(f'{k}:{v.mean():+.3f}({len(v)})' for k,v in y.groupby(m)))
print('组合')
for e in ('hold96','trail2atr_h192','tp2_h48'):print(fmt(f'{e} 最多4笔',sim(b,e)))
print(fmt('hold96 最多8笔 同向≤3',sim(b,'hold96',8,3,risk=0.005)))
