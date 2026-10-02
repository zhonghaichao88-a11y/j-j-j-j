"""核对"暴跌后做多"：阈值只用过去数据、下一根进场、同一时间多个币一起跌只算一次、对比平时买入。"""
import pandas as pd, numpy as np
F=pd.read_parquet('F.parquet').reset_index().sort_values(['inst','ts'])
F['c_next']=np.nan
for h,k in ((60,12),(240,48)):
    F[f'base_{h}']=F[f'fwd_{h}']
# 下一根进场：从下一根收盘算到 h 分钟后
g=F.groupby('inst')
for h,k in ((60,12),(240,48)):
    lvl=(1+F[f'fwd_{h}'])           # c[t+k]/c[t]
    nxt=g['fwd_15'].shift(0)        # placeholder
    r5n=g['r_5'].shift(-1)          # c[t+1]/c[t]-1
    F[f'nx_{h}']=lvl/(1+r5n)-1      # c[t+k]/c[t+1]-1  （少拿一根，偏保守）
# 每个币用过去 30 天的 0.5% 分位做阈值
F['thr']=g['r_60'].transform(lambda s: s.rolling(8640,min_periods=2000).quantile(0.005).shift(1))
F['vs']=F['vol_surge']
def show(name,E,h):
    E=E.sort_values('ts'); keep=[];last={}
    for i,(inst,ts) in enumerate(zip(E.inst,E.ts)):
        if ts-last.get(inst,-1e15)>=3600000: keep.append(i); last[inst]=ts
    E=E.iloc[keep]
    r=E[f'nx_{h}'].dropna()
    # 同一小时多个币一起跌 → 合成一笔（平均）
    cl=E.assign(r=E[f'nx_{h}'],hr=E.ts//3600000).dropna(subset=['r']).groupby('hr').r.mean()
    bym=(E.assign(r=E[f'nx_{h}']).groupby('month').r.mean()*1e4).round(0).to_dict()
    print(f'{name} {h}分钟: {len(r)}次 均值{r.mean()*1e4:+.0f} 中位数{r.median()*1e4:+.0f} 胜率{(r>0).mean()*100:.0f}% '
          f'| 按小时合并{len(cl)}笔 均值{cl.mean()*1e4:+.0f} 中位数{cl.median()*1e4:+.0f} | 各月{bym}')
for h in (60,240):
    base=F[f'nx_{h}'].dropna()
    print(f'对比：任何时候买入持有 {h}分钟 平均{base.mean()*1e4:+.1f}基点')
    show('暴跌后做多(过去阈值)',F[F.r_60<=F.thr],h)
    show('放量急跌后做多',F[(F.vol_surge>=5)&(F.r_5<-0.01)],h)
    E=F[F.r_60<=F.thr]
    print('  最多的币:',E.inst.value_counts().head(5).to_dict())
