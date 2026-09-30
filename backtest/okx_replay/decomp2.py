import json,pickle,numpy as np,pandas as pd
from decomp import *
out=pickle.load(open('decomp.pkl','rb'))
full={'A':pd.DataFrame(json.load(open('research_15m_cx76.json'))),'C':pd.DataFrame(json.load(open('research_old_cx76.json')))}
full['B']=full['A']
for k,(r,s,m,r_only,s_only) in out.items():
    F=full[k].set_index(['inst','ts','side'])
    why=[]
    for x in s_only.itertuples():
        key=(x.symbol,x.ts,x.sd)
        if key not in F.index: why.append('研究里没有这个信号'); continue
        y=F.loc[key]; y=y.iloc[0] if isinstance(y,pd.DataFrame) else y
        if not y.complete: why.append('数据末尾未满192根'); continue
        w=[]
        if y.h4!=1: w.append('4h均线方向不同')
        if y.btc!=1: w.append('BTC方向不同')
        if y.s2_risk<RISK_MIN: w.append('止损距离不同')
        why.append('+'.join(w) or '其他')
    s_only=s_only.assign(why=why)
    print(f'\n[{k}] 只在系统里：'); print(s_only.groupby('why').r.agg(['count','mean']).round(3).to_string())
    # 只在研究里：系统当时是否在持有该币
    busy=[]
    for x in r_only.itertuples():
        t=s[(s.symbol==x.inst)&(s.opened_ms-MS<x.ts)&(s.closed_ms>x.ts)]
        busy.append('系统当时已持有该币' if len(t) else '系统没开')
    rr=r.set_index(['inst','ts','side']).loc[list(zip(r_only.inst,r_only.ts,r_only.side))].assign(why=busy)
    print(f'[{k}] 只在研究里：'); print(rr.groupby('why').R.agg(['count','mean']).round(3).to_string())
    # 同一笔：出场差异
    m=m.assign(dR=m.r-m.R, rk=(m.risk_pct/m.s2_risk-1))
    print(f'[{k}] 同一笔：止损距离差(系统/研究-1)中位 {m.rk.median():+.4f}，|差|>1% 的 {np.mean(abs(m.rk)>0.01)*100:.0f}%')
    m['same_bars']=None
    print(m.groupby('reason').agg(n=('r','size'),研究=('R','mean'),系统=('r','mean'),差=('dR','mean')).round(3).to_string())
