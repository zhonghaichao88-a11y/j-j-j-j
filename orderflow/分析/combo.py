import pandas as pd, numpy as np
F=pd.read_pickle('F.pkl').reset_index().rename(columns={'index':'ts'})
F['ts']=F['ts'] if 'ts' in F else F.iloc[:,0]
days=sorted(F.day.unique())
# 每分钟在所有币之间排名（横截面），合成分数：等权，不调参
def cs_rank(col): return F.groupby('ts')[col].rank(pct=True)
F['s_rev']=1-cs_rank('r_60')
F['s_liq']=cs_rank('liq_net_5')
F['s_oi']=1-cs_rank('oi_5')
F['s_fund']=1-cs_rank('funding')
F['score']=F[['s_rev','s_liq','s_oi','s_fund']].mean(axis=1,skipna=True)
for h in (15,60):
    y=f'fwd_{h}'
    F[y+'_x']=F[y]-F.groupby('ts')[y].transform('mean')   # 扣掉大盘同期涨跌（只看相对强弱）
    for i,d in enumerate(days):
        s=F[(F.day==d)][['score',y,y+'_x']].dropna()
        q=pd.qcut(s.score.rank(method='first'),5,labels=False)
        m=s.groupby(q)[y+'_x'].mean()*1e4
        print(f'第{i+1}天 未来{h}分钟 相对收益(基点) 按分数从低到高5组:', ' '.join(f'{v:+.1f}' for v in m), f' IC={s.score.rank().corr(s[y].rank()):.3f}')
# 简单多空：每 60 分钟调仓一次，做多分数最高 5 个币、做空最低 5 个，拿 60 分钟
for cost in (0.0012,0.0004):
    for i,d in enumerate(days):
        s=F[(F.day==d)&(F.ts%3600000==0)].dropna(subset=['score','fwd_60'])
        pnl=[]
        for t,g in s.groupby('ts'):
            if len(g)<20: continue
            g=g.sort_values('score')
            pnl.append(g.fwd_60.tail(5).mean()-g.fwd_60.head(5).mean()-2*cost)
        pnl=np.array(pnl)
        print(f'成本{cost*100:.2f}%/单边 第{i+1}天: {len(pnl)}次调仓 平均每次 {pnl.mean()*100:+.3f}% 赚的次数 {(pnl>0).mean()*100:.0f}% 合计 {pnl.sum()*100:+.2f}%')
