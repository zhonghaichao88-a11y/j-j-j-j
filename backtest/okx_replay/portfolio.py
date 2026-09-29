import json,numpy as np,pandas as pd,sys
MS=900000
def load(path):
    df=pd.DataFrame(json.load(open(path)));return df[df.complete].copy()
RISK_MIN=None
def base_rule(d,risk_min):return (d.h4==1)&(d.btc==1)&(d.label=='3买')&(d.s2_risk>=risk_min)
def sim(d,exit_='hold96',cap=4,dir_cap=None,risk=0.01,cost=0.002):
    """按信号时间先到先得；持仓按真实平仓根数占用；同一币持仓期间不重复开。收益按平仓时计入。"""
    d=d.sort_values('ts');open_=[];trades=[]
    for r in d.itertuples():
        t=r.ts;open_=[o for o in open_ if o[0]>t]
        if len(open_)>=cap:continue
        if dir_cap and sum(1 for o in open_ if o[1]==r.side)>=dir_cap:continue
        if any(o[2]==r.inst for o in open_):continue
        end=t+getattr(r,exit_+'_bars')*MS
        R=getattr(r,'s2_'+exit_)-cost/r.s2_risk
        open_.append((end,r.side,r.inst));trades.append((end,R))
    if not trades:return dict(n=0,ret=0,dd=0,mpos=0,mn=0,avg=0)
    tr=sorted(trades);eq=np.cumsum([x[1] for x in tr])*risk
    dd=float(np.max(np.maximum.accumulate(np.r_[0,eq])-np.r_[0,eq]))
    m=pd.Series([x[1] for x in tr],index=pd.to_datetime([x[0] for x in tr],unit='ms')).resample('ME').sum()*risk
    return dict(n=len(tr),avg=float(np.mean([x[1] for x in tr])),ret=float(eq[-1]),dd=dd,mpos=int((m>0).sum()),mn=len(m),worst_m=float(m.min()))
def fmt(k,r):return f'  {k:34s} {r["n"]:4d}笔 平均{r["avg"]:+.3f}R 收益{r["ret"]*100:+6.1f}% 最大回撤{r["dd"]*100:5.1f}% 收益/回撤{(r["ret"]/r["dd"] if r["dd"] else 0):+.2f} 盈利月{r["mpos"]}/{r["mn"]} 最差月{r.get("worst_m",0)*100:+.1f}%'
if __name__=='__main__':
    old=pd.DataFrame(json.load(open('research_15m.json')));import datetime
    SPLIT=int(datetime.datetime(2026,7,1).timestamp()*1000);RISK_MIN=float(old[old.ts<SPLIT].s2_risk.quantile(.4))
    print('止损下限(沿用上次前半段40%%分位) = %.4f'%RISK_MIN)
    df=load('research_15m_v2.json');b=df[base_rule(df,RISK_MIN)]
    print('基础规则信号数',len(b))
    print('\n[出场方式] 最多4笔,每笔风险1%')
    for e in ('tp2_h48','trail2atr_h192','hold48','hold96','hold144','hold96_be','trail3atr_h96'):print(fmt(e,sim(b,e)))
    print('\n[持仓上限] hold96')
    for cap,dc in ((4,None),(4,2),(8,None),(8,3),(8,4),(12,4)):print(fmt(f'最多{cap}笔 同向≤{dc}',sim(b,'hold96',cap,dc,risk=0.04/cap)))
    print('\n[行情强度] hold96,最多4笔')
    for k,m in (('全部',b.index==b.index),('BTC 4h斜率同向',b.btc_h4s==1),('BTC偏离EMA200>1%',b.btc_dist>0.01),('BTC偏离>2%',b.btc_dist>0.02),
                ('币离4h均线<3ATR',b.h4_dist<3),('币离4h均线<6ATR',b.h4_dist<6),('BTC 4h斜率同向+币<6ATR',(b.btc_h4s==1)&(b.h4_dist<6))):
        print(fmt(k,sim(b[m],'hold96')))
