import pandas as pd, numpy as np
F=pd.read_pickle('F.pkl')
feats=['r_1','r_5','r_15','r_60','flow_1','flow_5','flow_15','imb','imb_5','depth_ratio','vol_surge','oi_5','oi_15','funding','liq_net_5','liq_tot_5','spread','vola_15']
days=sorted(F.day.unique())
rows=[]
for h in (5,15,60):
    y=f'fwd_{h}'
    for f in feats:
        r={'特征':f,'周期':h}
        for i,d in enumerate(days):
            s=F[F.day==d][[f,y]].dropna()
            r[f'IC_第{i+1}天']=s[f].rank().corr(s[y].rank()) if len(s)>500 else np.nan
        s=F[[f,y]].dropna()
        q=pd.qcut(s[f].rank(method='first'),10,labels=False)
        m=s.groupby(q)[y].mean()*1e4
        r['IC_全部']=s[f].rank().corr(s[y].rank())
        r['最高10%-最低10%(基点)']=m.iloc[-1]-m.iloc[0]
        r['最高10%']=m.iloc[-1]; r['最低10%']=m.iloc[0]
        rows.append(r)
R=pd.DataFrame(rows)
pd.set_option('display.width',250); pd.set_option('display.max_rows',200)
print(R.round(3).to_string(index=False))
