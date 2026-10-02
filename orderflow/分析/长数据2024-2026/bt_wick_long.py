"""挂单抓插针，长数据检验（2024-01 ~ 2026-03，5 分钟K线，不看未来）。
参数在 6 个月数据上定好，这里原样拿来，不再调。
挂单价 = 13 根前（65 分钟前）的收盘价 ×(1 + 阈值×倍数)；阈值 = 过去 30 天 1 小时跌幅 0.5% 分位。
最低价严格低于挂单价才成交；成交那根如果也碰到止损，按止损算（坏的情况）。"""
import pandas as pd, numpy as np, glob, os
MK,TK,SL=0.0002,0.0007,0.0005
CONF=[(1.3,0.5,1.0,48),(1.3,1.0,1.0,48),(1.6,0.5,1.0,48),(1.6,1.0,1.0,48),(1.6,0.5,0.5,48)]
def coin(f,mult,tp_f,sl_f,hold):
    d=pd.read_parquet(f).sort_values('ts').drop_duplicates('ts').reset_index(drop=True)
    T,H,L,C=d.ts.values,d.high.values,d.low.values,d.close.values
    r12=pd.Series(C).pct_change(12)
    thr=r12.rolling(8640,min_periods=2000).quantile(0.005).shift(1).values
    ref=np.r_[np.full(13,np.nan),C[:-13]]
    lim=ref*(1+thr*mult)
    out=[];busy=-1
    for i in np.where(L<lim)[0]:
        if i<=busy: continue
        px=lim[i]; drop=-thr[i]*mult
        tp=px*(1+drop*tp_f); st=px*(1-drop*sl_f)
        end=min(i+hold,len(C)-1); ex=None; j=i
        if L[i]<=st: ex=st*(1-SL); cost=MK+TK
        else:
            for j in range(i+1,end+1):
                if L[j]<=st: ex=st*(1-SL); cost=MK+TK; break
                if H[j]>tp: ex=tp; cost=MK+MK; break
        if ex is None: ex=C[end]; cost=MK+TK; j=end
        out.append((T[i],ex/px-1-cost,drop*sl_f)); busy=max(i+12,j)
    return out
files=sorted(glob.glob('k/*.parquet'))
for mult,tp_f,sl_f,hold in CONF:
    rows=[]
    for f in files:
        for t,r,risk in coin(f,mult,tp_f,sl_f,hold): rows.append((os.path.basename(f)[:-8],t,r,risk))
    R=pd.DataFrame(rows,columns=['inst','ts','ret','risk'])
    R['m']=pd.to_datetime(R.ts,unit='ms').dt.strftime('%Y-%m'); R['q']=pd.to_datetime(R.ts,unit='ms').dt.to_period('Q').astype(str)
    pf=lambda x: x[x>0].sum()/-x[x<0].sum()
    bym=R.groupby('m').ret.mean()
    day=(R.ret/R.risk*0.005).groupby(R.ts//86400000).sum(); eq=day.cumsum()
    print(f'\n挂单倍数{mult} 止盈{tp_f} 止损{sl_f} 持有{hold*5}分钟: {len(R)}笔 胜率{(R.ret>0).mean()*100:.0f}% 每笔{R.ret.mean()*1e4:+.1f}基点 PF{pf(R.ret):.2f} '
          f'赚钱月{(bym>0).sum()}/{len(bym)} 每笔风险0.5%总收益{eq.iloc[-1]*100:.0f}% 最大回撤{(eq-eq.cummax()).min()*100:.0f}%')
    print('  按季度 每笔基点/PF/笔数:', {q:(round(g.mean()*1e4,1),round(pf(g),2),len(g)) for q,g in R.groupby('q').ret})
