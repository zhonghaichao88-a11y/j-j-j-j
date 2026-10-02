"""核对"多头清洗 + 现货接盘 → 多"：
1 对比：同期任何时候买入 / 只看清洗不看现货
2 阈值上下浮动，看是不是只在某个数字上才有效
3 下一根开盘进场 + 止损，按真实下单回测（5 分钟K线，同一根里先算止损）
4 按币、按季度、同一小时多个币合并成一笔"""
import pandas as pd, numpy as np
F=pd.read_parquet('F2.parquet').reset_index()
F['year']=np.where(F.ts<1735689600000,'2024','2025-26')
def ev(mask,gap=48):
    E=F[mask.fillna(False).values].sort_values(['inst','ts']); keep=[];last={}
    for i,(inst,ts) in enumerate(zip(E.inst,E.ts)):
        if ts-last.get(inst,-1e15)>=gap*300000: keep.append(i); last[inst]=ts
    return E.iloc[keep]
print('对比：任何时候买入持有 4 小时，平均 %+.1f 基点'%(F.fwd_240.mean()*1e4))
print('\n阈值浮动（持有 4 小时，扣吃单 0.12% 后，基点 / 赚钱季度 / 2024 / 2025-26 / 次数）:')
for r in (-.015,-.02,-.03):
  for o in (-.02,-.03,-.05):
    for sf in (0,.05,.1):
        E=ev((F.r_60<r)&(F.oi_60<o)&(F.sf_60>sf)); net=E.fwd_240-.0012
        if len(E)<50: continue
        q=net.groupby(E.q).mean(); y=net.groupby(E.year).mean()
        print(f'  1h跌>{-r:.1%} 持仓降>{-o:.0%} 现货主动>{sf:.2f}: {net.mean()*1e4:+6.1f}  {(q>0).sum()}/{len(q)}  {y.get("2024",np.nan)*1e4:+6.1f} {y.get("2025-26",np.nan)*1e4:+6.1f}  {len(E)}')
E=ev((F.r_60<-.02)&(F.oi_60<-.03)&(F.sf_60>0))
print('\n按币（持有 4 小时，扣成本后基点，次数）：')
bc=E.groupby('inst').apply(lambda g: pd.Series({'n':len(g),'net':(g.fwd_240.mean()-.0012)*1e4}))
print('  赚钱的币', (bc.net>0).sum(), '/', len(bc)); print(bc.sort_values('n',ascending=False).head(12).round(1).T.to_string())
h=(E.assign(h=E.ts//3600000).groupby('h').fwd_240.mean()-.0012)
print('\n同一小时合并：%d 笔，平均 %+.1f 基点，胜率 %.0f%%'%(len(h),h.mean()*1e4,(h>0).mean()*100))
# 真实下单：下一根开盘进场，止损 = 进场价 - k×(过去1小时跌幅)，持有 4 小时
print('\n带止损的回测（下一根开盘进场；止损按这次下跌幅度的倍数；同一根K线先算止损；扣 0.12%）:')
K={}
for inst in E.inst.unique():
    d=pd.read_parquet(f'k/{inst}.parquet',columns=['ts','open','high','low','close']).sort_values('ts').drop_duplicates('ts').reset_index(drop=True)
    K[inst]=(d.ts.values,d.open.values,d.high.values,d.low.values,d.close.values)
for sk in (0.5,1.0,2.0,None):
    rs=[]
    for inst,ts,r60,q,y in zip(E.inst,E.ts,E.r_60,E.q,E.year):
        T,O,H,L,C=K[inst]; i=np.searchsorted(T,ts)+1
        if i+48>=len(T): continue
        e=O[i]; st=e*(1-sk*abs(r60)) if sk else -1; ex=C[i+47]
        for j in range(i,i+48):
            if L[j]<=st: ex=st*(1-.0005); break
        rs.append((q,y,ex/e-1-.0012))
    R=pd.DataFrame(rs,columns=['q','y','r']); qq=R.groupby('q').r.mean(); yy=R.groupby('y').r.mean()
    pf=R.r[R.r>0].sum()/-R.r[R.r<0].sum()
    print(f'  止损 {sk if sk else "不设"}倍跌幅: {len(R)}笔 胜率{(R.r>0).mean()*100:.0f}% 每笔{R.r.mean()*1e4:+.1f}基点 PF{pf:.2f} 赚钱季度{(qq>0).sum()}/{len(qq)} 2024 {yy["2024"]*1e4:+.1f} 2025-26 {yy["2025-26"]*1e4:+.1f}')
    print('    各季度:', (qq*1e4).round(0).to_dict())
