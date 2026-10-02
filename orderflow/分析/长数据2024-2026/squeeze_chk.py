import pandas as pd, numpy as np
exec(open('squeeze.py').read().split("pf=lambda")[0])
for name,m in (('涨>3% 持仓降>2% 拿12h',(F.r_60>.03)&(F.oi_60<-.02)),('涨>2% 持仓降>2% 拿12h',(F.r_60>.02)&(F.oi_60<-.02))):
    R=run(m,144).sort_values('t'); r=R.r
    top=r.sort_values(ascending=False)
    print(f'\n{name}: {len(R)}笔 平均{r.mean()*1e4:+.0f} 中位数{r.median()*1e4:+.0f} 胜率{(r>0).mean()*100:.0f}%')
    print(f'  去掉最好的 5 笔后平均 {top.iloc[5:].mean()*1e4:+.0f}；去掉最好的 5% 后平均 {top.iloc[int(len(r)*.05):].mean()*1e4:+.0f}')
    print('  最好的 5 笔:', [(i,round(x*100,1)) for i,x in zip(R.loc[top.index[:5],'inst'],top.iloc[:5])])
    q=R.groupby(pd.to_datetime(R.t,unit='ms').dt.to_period('Q')).r.agg(['count','mean'])
    print('  各季度(笔数,每笔基点):',{str(k):(int(v['count']),int(v['mean']*1e4)) for k,v in q.iterrows()})
    c=R.groupby('inst').r.agg(['count','mean']).sort_values('count',ascending=False)
    print('  笔数最多的币:',{k:(int(v['count']),int(v['mean']*1e4)) for k,v in c.head(8).iterrows()})
    d=R.assign(d=R.t//86400000).groupby('d').r.mean()
    print(f'  按天合并: {len(d)}天 平均{d.mean()*1e4:+.0f} 赚钱天{(d>0).mean()*100:.0f}%')
    # 每笔 5% 仓位的资金曲线
    eq=(1+0.05*r).cumprod(); print(f'  每笔 5% 仓位: 总收益 {(eq.iloc[-1]-1)*100:.0f}% 最大回撤 {(eq/eq.cummax()-1).min()*100:.1f}%')
