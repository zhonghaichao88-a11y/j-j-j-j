import pandas as pd,glob,sys,os
pat=sys.argv[1]
fs=[f for f in glob.glob(pat) if os.path.getsize(f)>5 and not any(x in f for x in ('_NQ_','_ES_'))]
t=pd.concat([pd.read_csv(f).assign(sym=f.split('_')[1]) for f in fs],ignore_index=True)
w=lambda x:(x=='TP').mean()
print(len(fs),"个币 合计",len(t),"笔 胜率",round(w(t.res)*100,1),"% 平均R毛",round(t.R.mean(),3),"扣费",round(t.R_net.mean(),3),"止损中位%",round(t.risk_pct.median(),2))
t['yr']=pd.to_datetime(t.day).dt.year
for k in ('yr','level','tf'):
    print(t.groupby(k).agg(n=('R','size'),win=('res',w),R=('R','mean'),Rn=('R_net','mean')).round(3))
print("顺前日K线方向:",t.groupby(t.side==t.bias_pd).agg(n=('R','size'),win=('res',w),R=('R','mean'),Rn=('R_net','mean')).round(3))
print("出场",t.res.value_counts().to_dict())
