"""想办法多赚：持有时间、挂单进出、条件松紧。统一按"最大回撤控制在 15%"来放大仓位，看一年能赚多少。"""
import pandas as pd, numpy as np
F=pd.read_parquet('F2')
def ev(mask,gap):
    E=F[mask.fillna(False).values].sort_values(['inst','ts']); keep=[];last={}
    for i,(inst,ts) in enumerate(zip(E.inst,E.ts)):
        if ts-last.get(inst,-1e15)>=gap*300000: keep.append(i); last[inst]=ts
    return E.iloc[keep]
K={}
def kl(inst):
    if inst not in K:
        d=pd.read_parquet(f'k/{inst}.parquet',columns=['ts','open','high','low','close']).sort_values('ts').drop_duplicates('ts').reset_index(drop=True)
        K[inst]=(d.ts.values,d.open.values,d.low.values,d.close.values)
    return K[inst]
CONDS={'严格（现在的）':lambda: (F.r_60<-.02)&(F.oi_60<-.05)&(F.sf_60>.05),
       '中等':lambda: (F.r_60<-.02)&(F.oi_60<-.04)&(F.sf_60>.02),
       '宽松':lambda: (F.r_60<-.02)&(F.oi_60<-.03)&(F.sf_60>0)}
out=[]
for cn,cf in CONDS.items():
    for hold in (48,96,144,288):
        E=ev(cf(),hold)
        for cost_name,cost in (('吃单',.0012),('挂单',.0005)):
            rows=[]
            for inst,ts,r60 in zip(E.inst,E.ts,E.r_60):
                T,O,L,C=kl(inst); i=np.searchsorted(T,ts)+1
                if i+hold>=len(T): continue
                # 挂单：在信号收盘价挂买单，下一根最低价碰到才成交（碰不到就算没做）
                if cost_name=='挂单':
                    px=C[i-1]
                    if L[i]>px: continue
                    e=px
                else:
                    e=O[i]
                st=e*(1-3*abs(r60)); ex=C[i+hold-1]
                for j in range(i,i+hold):
                    if L[j]<=st: ex=st*(1-.0005); break
                c=cost if not (cost_name=='挂单' and ex<=st) else .0002+.0007   # 止损出场按市价
                rows.append((T[i],T[i+hold-1],ex/e-1-c))
            R=pd.DataFrame(rows,columns=['t0','t1','r']).sort_values('t1')
            if len(R)<50: continue
            yrs=(R.t1.max()-R.t0.min())/86400000/365
            # 每笔仓位 f（占权益比例），找出最大回撤刚好 15% 时的 f
            best=None
            for f in np.arange(0.05,3.01,0.05):
                eq=(1+f*R.r).cumprod(); dd=(eq/eq.cummax()-1).min()
                if dd<-0.15: break
                best=(f,eq.iloc[-1],dd)
            if best is None: continue
            f,fin,dd=best
            pf=R.r[R.r>0].sum()/-R.r[R.r<0].sum()
            y=R.groupby(pd.to_datetime(R.t1,unit='ms').dt.year).r.mean()*1e4
            out.append(dict(条件=cn,持有小时=hold//12,进出=cost_name,笔数=len(R),每月笔数=round(len(R)/yrs/12),胜率=round((R.r>0).mean()*100),
                            每笔基点=round(R.r.mean()*1e4,1),PF=round(pf,2),各年基点=y.round(0).to_dict(),
                            回撤15时每笔仓位=f'{f:.0%}',每年收益=f'{(fin**(1/yrs)-1)*100:.0f}%'))
            print(out[-1],flush=True)
pd.set_option('display.width',250); pd.set_option('display.max_colwidth',60)
print(pd.DataFrame(out).to_string(index=False))
