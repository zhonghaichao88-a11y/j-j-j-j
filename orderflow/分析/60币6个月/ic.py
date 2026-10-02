"""每个特征：按月看预测力（6个月里有几个月方向一致），最高10%-最低10% 的未来收益差（基点）。"""
import pandas as pd, numpy as np
F=pd.read_parquet('/home/user/ext/free/an/F.parquet')
feats=['r_5','r_15','r_60','r_240','flow_5','flow_60','book_02','book_1','book_5','vol_surge','oi_5','oi_60','funding','ls','top_ls','taker_ratio','ls_chg','vola']
months=sorted(F.month.unique())
out=[]
for h in (15,60,240):
    y=f'fwd_{h}'
    # 扣掉同一时刻所有币的平均涨跌（只看相对强弱，去掉大盘方向）
    F[y+'x']=F[y]-F.groupby(level=0)[y].transform('mean')
    for f in feats:
        s=F[[f,y+'x','month']].dropna()
        if len(s)<10000: continue
        ics=[s[s.month==m][f].rank().corr(s[s.month==m][y+'x'].rank()) for m in months]
        q=pd.qcut(s[f].rank(method='first'),10,labels=False)
        m=s.groupby(q)[y+'x'].mean()*1e4
        sg=np.sign(np.nanmean(ics))
        out.append({'特征':f,'周期分钟':h,'平均IC':np.nanmean(ics),'方向一致月数':int(sum(np.sign(i)==sg for i in ics if not np.isnan(i))),
                    '月数':len([i for i in ics if not np.isnan(i)]),'顶10%-底10%(基点)':m.iloc[-1]-m.iloc[0]})
R=pd.DataFrame(out)
pd.set_option('display.width',200); pd.set_option('display.max_rows',300)
print(R.round(3).sort_values(['周期分钟','平均IC'],key=lambda c: c.abs() if c.name=='平均IC' else c).to_string(index=False))
