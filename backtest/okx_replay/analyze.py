import json,numpy as np,pandas as pd,datetime,sys
df=pd.DataFrame(json.load(open('research_15m.json')));df=df[df.complete]
SPLIT=int(datetime.datetime(2026,7,1).timestamp()*1000)
tr=df[df.ts<SPLIT];te=df[df.ts>=SPLIT]
EX=['tp2_h48','tp3_h96','trail2atr_h192','hold96']
def net(d,s,e):return d[f'{s}_{e}']-d[f'{s}_costR']
def fmt(x):return f'{x.mean():+.3f}' if len(x) else '  n/a'
if __name__=='__main__':
    print('训练段',len(tr),'测试段',len(te))
    print('\n基线（全部信号，训练段）：扣成本前 / 扣成本后')
    for s in ('s1','s2'):
        print(' ',s,' '.join(f'{e}: {fmt(tr[f"{s}_{e}"])} / {fmt(net(tr,s,e))}' for e in EX))
    s,e='s2','trail2atr_h192'
    print(f'\n单因素（训练段，{s}+{e}，扣成本后均值，括号内笔数）')
    feats={'label':tr.label,'side':tr.side,'trend200':tr.trend200,'trend50':tr.trend50,'h1':tr.h1,'h1s':tr.h1s,'h4':tr.h4,'h4s':tr.h4s,'btc':tr.btc,
           'lag':pd.cut(tr.lag,[-1,4,7,10,15,100]),'move_atr':pd.qcut(tr.move_atr,5),'atr_pct':pd.qcut(tr.atr_pct,5),
           'ret24':pd.qcut(tr.ret24,5),'volr':pd.qcut(tr.volr,5),'hour':pd.cut(tr.hour,[-1,5,11,17,23]),'risk':pd.qcut(tr.s2_risk,5)}
    y=net(tr,s,e);g=tr[f'{s}_{e}']
    for k,v in feats.items():
        grp=pd.DataFrame(dict(v=v,y=y,g=g)).groupby('v',observed=True)
        print(f'  {k:9s}',' | '.join(f'{i}: {r.y.mean():+.3f} (前{r.g.mean():+.3f},{len(r)})' for i,r in grp))
