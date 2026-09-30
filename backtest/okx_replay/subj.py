"""方案一 + 主观规则（强势选币、市场宽度、做差价、只做多）。基于盘中移动止损修正后的回放，只算加密币。"""
import json, sys, numpy as np, pandas as pd
MS=900000; D=86400000
cats=json.load(open('categories.json')); CRYPTO=[i for i,c in cats.items() if c=='1']
def daily(suf):
    out={}
    for inst in CRYPTO:
        try: z=np.load(f'{inst}_{suf}.npz')
        except FileNotFoundError: continue
        ts=z['ts']; c=z['close']; b=ts//D
        last=np.flatnonzero(np.r_[b[1:]!=b[:-1],True]); full=(ts[last]+MS)%D==0
        dts=b[last][full]*D; dc=c[last][full]
        if len(dc)<60: continue
        e=np.empty(len(dc)); e[0]=dc[0]
        for i in range(1,len(dc)): e[i]=e[i-1]+2/51*(dc[i]-e[i-1])
        out[inst]=(dts,dc,e)
    return out
def features(s,dly):
    rs=[];br=[]
    for x in s.itertuples():
        t=x.opened_ms; rets={}; above=[]
        for inst,(ts,c,e) in dly.items():
            k=int(np.searchsorted(ts+D,t,side='right'))-1
            if k>=30: rets[inst]=c[k]/c[k-30]-1; above.append(c[k]>e[k])
        d=1 if x.side=='long' else -1
        if x.symbol in rets and len(rets)>=10:
            v=sorted(rets.values()); rank=v.index(rets[x.symbol])/(len(v)-1)
            rs.append(rank if d==1 else 1-rank); b=float(np.mean(above)); br.append(b if d==1 else 1-b)
        else: rs.append(np.nan); br.append(np.nan)
    return s.assign(rs=rs,breadth=br)
def trades(fn):
    s=pd.DataFrame(json.load(open(fn))['trades']); s=s[(s.kind=='T3')&s.symbol.isin(CRYPTO)&(s.reason!='数据结束')]
    return s.copy()
if __name__=='__main__':
    ra=pd.DataFrame(json.load(open('research_15m_cx76.json'))); split=ra.ts.min()+(ra.ts.max()-ra.ts.min())//2+MS
    import os
    res=pd.read_pickle("subj.pkl") if os.path.exists("subj.pkl") else {}
    for tag,suf,o in ([] if res else (("AB","15m",""),("C","15m_old","_old"))):
        dly=daily(suf)
        for var in ('ib','t'):
            s=features(trades(f'result_scheme1{o}_{var}.json'),dly)
            if tag=='AB':
                res[('A',var)]=s[s.opened_ms<split]; res[('B',var)]=s[s.opened_ms>=split]
            else: res[('C',var)]=s
    pd.to_pickle(res,'subj.pkl')
    F={'方案一（基准）':lambda d:d.index==d.index,'+强势币前50%':lambda d:d.rs>=0.5,'+强势币前30%':lambda d:d.rs>=0.7,
       '+市场宽度>50%':lambda d:d.breadth>=0.5,'+市场宽度>60%':lambda d:d.breadth>=0.6,'+强势50%+宽度50%':lambda d:(d.rs>=0.5)&(d.breadth>=0.5),
       '+只做多':lambda d:d.side=='long'}
    for var,vn in (('ib','不做差价'),('t','做差价')):
        print(f'\n—— {vn} ——')
        for name,f in F.items():
            cells=[]
            for k in 'ABC':
                d=res[(k,var)]; y=d[np.asarray(f(d),bool)].r
                cells.append(f'{y.mean():+.3f}({len(y)})±{1.96*y.std()/np.sqrt(max(len(y),1)):.3f}')
            print(f'{name:18s} '+'   '.join(cells))
