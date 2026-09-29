"""稳健性：每个组合去掉最好 5 笔后的平均；统计两年都为正的组合，并对“事先选中”的组合做组合层面模拟。"""
import itertools,numpy as np,pandas as pd
exec(open('grid_eval.py').read().split('results = []')[0])   # 复用分组定义
def ex5(y):return np.sort(y)[:-5].mean() if len(y)>10 else np.nan
rows=[]
for s in STRUCT:
    sm=((df.pen==s[0])&(df.macd==s[1])&(df.ratio==s[2])&(df.seg==s[3])).values
    for lay,(bk,bks),(sk,sks) in itertools.product(LAYERS,BIGK.items(),SMALL.items()):
        if lay=='三层':m=df.big.isin(bks)&df.mid.isin(('T1','T1P'))
        elif lay=='大级别+小级别':m=df.big.isin(bks)
        else:m=df.mid.isin(bks)
        bm=sm&m.values&df.small.isin(sks).values
        for (rn,rv),(bn,bv),(sn,sv) in itertools.product(RS.items(),BR.items(),SIDE.items()):
            mm=bm.copy()
            if rv is not None:mm&=(df.rs>=rv).values
            if bv is not None:mm&=(df.breadth>=bv).values
            if sv is not None:mm&=(df.side==sv).values
            for sb,ex in itertools.product(STOPS,EXITS):
                col=f's{sb}_{ex}';y=(df[col]-df[col+'_c']).values;ok=mm&np.isfinite(y)
                a=y[ok&tr_mask];b=y[ok&~tr_mask]
                if len(a)<60 or len(b)<30:continue
                rows.append((s,lay,bk,sk,rn,bn,sn,sb,ex,len(a),ex5(a),np.median(a),len(b),ex5(b),np.median(b)))
R=pd.DataFrame(rows,columns=['结构','层数','大级别点','小级别','选币','宽度','方向','止损','出场','n1','ex1','med1','n2','ex2','med2'])
print(f'组合数 {len(R)}（MACD回0轴对结果没有影响，已合并）')
print(f'去掉最好5笔后：第一年为正 {np.mean(R.ex1>0)*100:.1f}%，第二年为正 {np.mean(R.ex2>0)*100:.1f}%，两年都为正 {np.mean((R.ex1>0)&(R.ex2>0))*100:.2f}%（{int(((R.ex1>0)&(R.ex2>0)).sum())} 个）')
print(f'中位数：第一年为正 {np.mean(R.med1>0)*100:.2f}%，第二年为正 {np.mean(R.med2>0)*100:.2f}%')
both=R[(R.ex1>0)&(R.ex2>0)].sort_values('ex1',ascending=False)
with pd.option_context('display.width',250,'display.max_columns',30):print(both.head(15).round(3).to_string(index=False))
