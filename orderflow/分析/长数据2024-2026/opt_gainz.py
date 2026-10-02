"""GainzAlgo 风格指标调参：只用 2024 年挑参数（训练），2025-01 ~ 2026-03 原样检验（考试，训练时没碰过）。"""
import pandas as pd, numpy as np, glob, itertools
from bt_gainz import resample, rsi, COST
CUT = 1735689600000      # 2025-01-01
files = sorted(glob.glob('k/*.parquet'))
base = {f: pd.read_parquet(f, columns=['ts','open','high','low','close']).sort_values('ts').drop_duplicates('ts').reset_index(drop=True) for f in files}

def prep(d):
    o,h,l,c=d.open,d.high,d.low,d.close
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    return dict(o=o,h=h,l=l,c=c,atr=tr.rolling(14).mean(),r=rsi(c),body=(c-o).abs(),ema=c.ewm(span=200).mean())

def sig(P,rlo,ak,tn,htf,side):
    o,h,l,c,atr,r,body,ema=[P[k] for k in ('o','h','l','c','atr','r','body','ema')]
    big=((h-l)>=ak*atr)&(body>=0.5*(h-l))
    L=(c.shift()<o.shift())&(c>o)&(c>=o.shift())&(o<=c.shift())&big&(r.rolling(5).min()<rlo)&(r>r.shift())&(c.shift(1)<c.shift(1+tn))
    S=(c.shift()>o.shift())&(c<o)&(c<=o.shift())&(o>=c.shift())&big&(r.rolling(5).max()>100-rlo)&(r<r.shift())&(c.shift(1)>c.shift(1+tn))
    if htf=='with': L&=c>ema; S&=c<ema
    if side=='long': S&=False
    return L.fillna(False).values,S.fillna(False).values

def trades(P,L,S,rr,hold=48):
    O,H,Lo,C=P['o'].values,P['h'].values,P['l'].values,P['c'].values
    out=[];busy=-1
    for i in np.where(L|S)[0]:
        if i<=busy or i+1>=len(O): continue
        s=1 if L[i] else -1; e=O[i+1]; st=Lo[i] if s==1 else H[i]; risk=(e-st)*s
        if risk<=0: continue
        tp=e+s*rr*risk; ex=None
        for j in range(i+1,min(i+1+hold,len(O))):
            if (Lo[j]<=st) if s==1 else (H[j]>=st): ex=st; break
            if (H[j]>=tp) if s==1 else (Lo[j]<=tp): ex=tp; break
        if ex is None: ex=C[j]
        out.append((i,s*(ex/e-1)-COST)); busy=j
    return out

pf=lambda x: x[x>0].sum()/-x[x<0].sum() if (x<0).any() else np.inf
res=[]
for n,tfn in ((12,'1小时'),(48,'4小时')):
    data={f:resample(d,n) for f,d in base.items()}; preps={f:prep(d) for f,d in data.items()}
    for rlo,ak,tn,rr,htf,side in itertools.product((30,35),(0.8,1.2),(10,20),(1.5,2.5),('none','with'),('both','long')):
        tr_in,tr_out=[],[]
        for f,P in preps.items():
            L,S=sig(P,rlo,ak,tn,htf,side); T=data[f].ts.values
            for i,r in trades(P,L,S,rr):
                (tr_in if T[i]<CUT else tr_out).append((T[i],r))
        a=np.array([r for _,r in tr_in]); b=np.array([r for _,r in tr_out])
        qb=pd.Series(b,index=pd.to_datetime([t for t,_ in tr_out],unit='ms').to_period('Q')).groupby(level=0).mean() if len(b) else pd.Series()
        res.append(dict(周期=tfn,RSI=rlo,波动=ak,前跌根数=tn,止盈R=rr,顺大趋势=htf,方向=side,训练笔数=len(a),训练每笔=round(a.mean()*1e4,1) if len(a) else np.nan,训练PF=round(pf(a),2) if len(a) else np.nan,
                        考试笔数=len(b),考试每笔=round(b.mean()*1e4,1) if len(b) else np.nan,考试PF=round(pf(b),2) if len(b) else np.nan,考试赚钱季度=f'{(qb>0).sum()}/{len(qb)}'))
        print(res[-1],flush=True)
R=pd.DataFrame(res); R.to_csv('opt_gainz.csv',index=False)
pd.set_option('display.width',250)
ok=R[R.训练笔数>=150].sort_values('训练PF',ascending=False)
print('\n=== 训练期（2024）最好的 10 组，以及它们在考试期的成绩 ===')
print(ok.head(10).to_string(index=False))
print('\n训练期 PF>1 的组合：',(ok.训练PF>1).sum(),'个；其中考试期也 PF>1：',((ok.训练PF>1)&(ok.考试PF>1)).sum(),'个')
print('训练期 PF 和考试期 PF 的相关性：',round(ok.训练PF.corr(ok.考试PF),2))
