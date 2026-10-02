"""轧空后追多：急涨 + 持仓量下降（空单被强平）→ 做多。
训练 2024（挑参数）→ 考试 2025-01~2026-03（原样检验）。对照：只看急涨不看持仓量。
进场：下一根开盘（吃单）；止损：进场价下方"这次涨幅"那么远；到时间平仓；成本 0.12%。同一个币持有期内只做一次。"""
import pandas as pd, numpy as np, itertools
exec(open('boost_flush.py').read().split("CONDS=")[0])
CUT=1735689600000
def run(mask,hold):
    E=ev(mask,hold); rows=[]
    for inst,ts,r60 in zip(E.inst,E.ts,E.r_60):
        T,O,L,C=kl(inst); i=np.searchsorted(T,ts)+1
        if i+hold>=len(T): continue
        e=O[i]; st=e*(1-abs(r60)); ex=C[i+hold-1]
        for j in range(i,i+hold):
            if L[j]<=st: ex=st*(1-.0005); break
        rows.append((inst,T[i],ex/e-1-.0012))
    return pd.DataFrame(rows,columns=['inst','t','r'])
pf=lambda x: x[x>0].sum()/-x[x<0].sum() if (x<0).any() else np.inf
res=[]
for up,oi,spot,hold in itertools.product((.02,.03,.05),(None,-.02,-.03,-.05),('不看','在卖','在买'),(12,48,144)):
    m=F.r_60>up
    if oi is not None: m&=F.oi_60<oi
    if spot=='在卖': m&=F.sf_60<0
    if spot=='在买': m&=F.sf_60>0
    R=run(m,hold)
    a,b=R[R.t<CUT],R[R.t>=CUT]
    if len(a)<30 or len(b)<20: continue
    res.append(dict(涨超=f'{up:.0%}',持仓=('不看' if oi is None else f'降>{-oi:.0%}'),现货=spot,拿小时=hold//12,
                    训练笔数=len(a),训练每笔=round(a.r.mean()*1e4,1),训练PF=round(pf(a.r),2),
                    考试笔数=len(b),考试每笔=round(b.r.mean()*1e4,1),考试PF=round(pf(b.r),2),
                    考试赚钱的币=f"{(b.groupby('inst').r.mean()>0).sum()}/{b.inst.nunique()}"))
R=pd.DataFrame(res); R.to_csv('squeeze.csv',index=False)
pd.set_option('display.width',250)
ok=R[R.训练笔数>=50].sort_values('训练PF',ascending=False)
print('=== 训练期（2024）最好的 12 组 → 考试期成绩 ===')
print(ok.head(12).to_string(index=False))
print('\n=== 对照：不看持仓量（普通追涨） ===')
print(R[R.持仓=='不看'].sort_values('训练PF',ascending=False).head(6).to_string(index=False))
print('\n训练期 PF>1 的：',(ok.训练PF>1).sum(),'组；其中考试期也 PF>1：',((ok.训练PF>1)&(ok.考试PF>1)).sum(),'组；训练和考试 PF 相关性',round(ok.训练PF.corr(ok.考试PF),2))
