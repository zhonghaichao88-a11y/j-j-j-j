"""方案一：研究 vs 系统回放 的差距拆分。逐笔按 (币, 信号K线时间, 方向) 对齐。"""
import json, numpy as np, pandas as pd
MS=900000; RISK_MIN=0.0172; COST=0.002
def load(rs, sy):
    r=pd.DataFrame(json.load(open(rs))); r=r[r.complete]
    r=r[(r.h4==1)&(r.btc==1)&(r.label=='3买')&(r.s2_risk>=RISK_MIN)].copy()
    r['R']=r.s2_trail2atr_h192-r.s2_costR
    s=pd.DataFrame(json.load(open(sy))['trades']); s=s[s.kind=='T3'].copy()
    s['ts']=s.opened_ms-MS; s['sd']=np.where(s.side=='long',1,-1)
    s['gross']=s.sd*(s.exit-s.entry)/s.entry; s['cost_sys']=s.gross-s.ret
    s['R02']=(s.gross-COST)/s.risk_pct
    return r,s
def one_per_coin(r, bars_col='trail2atr_h192_bars'):
    keep=[]; busy={}
    for i,x in r.sort_values('ts').iterrows():
        if busy.get(x.inst,0)>x.ts: continue
        keep.append(i); busy[x.inst]=x.ts+(x[bars_col]+1)*MS
    return r.loc[keep]
def report(tag, r, s):
    r1=one_per_coin(r)
    m=r.merge(s,left_on=['inst','ts','side'],right_on=['symbol','ts','sd'],how='inner',suffixes=('','_s'))
    ro=r.merge(s[['symbol','ts','sd']],left_on=['inst','ts','side'],right_on=['symbol','ts','sd'],how='left',indicator=True)
    r_only=ro[ro._merge=='left_only']
    so=s.merge(r[['inst','ts','side']],left_on=['symbol','ts','sd'],right_on=['inst','ts','side'],how='left',indicator=True)
    s_only=so[so._merge=='left_only']
    print(f'\n=== {tag} ===')
    print(f'研究全部信号      {len(r):5d} 笔  {r.R.mean():+.3f}R')
    print(f'研究 同币只持一笔 {len(r1):5d} 笔  {r1.R.mean():+.3f}R')
    print(f'系统回放          {len(s):5d} 笔  {s.r.mean():+.3f}R   (系统成本折0.2%: {s.R02.mean():+.3f}R, 系统平均成本 {s.cost_sys.mean()*100:.3f}%)')
    print(f'两边都有(同一笔) {len(m):5d} 笔  研究 {m.R.mean():+.3f}R  系统 {m.r.mean():+.3f}R  系统@0.2% {m.R02.mean():+.3f}R')
    print(f'只在研究里       {len(r_only):5d} 笔  研究 {r.set_index(["inst","ts","side"]).loc[list(zip(r_only.inst,r_only.ts,r_only.side))].R.mean():+.3f}R')
    print(f'只在系统里       {len(s_only):5d} 笔  系统 {s_only.r.mean():+.3f}R')
    return r,s,m,r_only,s_only
if __name__=='__main__':
    ra,sa=load('research_15m_cx76.json','result_scheme1.json')
    split=ra.ts.min()+(ra.ts.max()-ra.ts.min())//2
    import pickle
    out={}
    out['A']=report('A 26年4–6月',ra[ra.ts<split],sa[sa.ts<split])
    out['B']=report('B 26年7–9月',ra[ra.ts>=split],sa[sa.ts>=split])
    rc,sc=load('research_old_cx76.json','result_scheme1_old.json')
    out['C']=report('C 25年9月–26年3月',rc,sc)
    pickle.dump(out,open('decomp.pkl','wb'))
