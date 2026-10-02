"""极端情况事件：每个事件只算一次（同一个币 1 小时内不重复），按月看稳定性，扣成本前后。"""
import pandas as pd, numpy as np
F=pd.read_parquet('/home/user/ext/free/an/F.parquet').reset_index()
def events(mask, side, name, h=60):
    E=F[mask].copy(); E['side']=side if np.isscalar(side) else side[mask]
    E=E.sort_values(['inst','ts'])
    keep=[];last={}
    for i,(inst,ts) in enumerate(zip(E.inst,E.ts)):
        if ts-last.get(inst,-10**15)>=3600000: keep.append(i); last[inst]=ts
    E=E.iloc[keep]
    r=(E[f'fwd_{h}']*E.side).dropna()
    bym=(E.assign(r=E[f'fwd_{h}']*E.side).groupby('month').r.mean()*1e4).round(1)
    good=(bym>12).sum()
    print(f'{name}（{h}分钟）: {len(r)}次 平均{r.mean()*1e4:+.1f}基点 胜率{(r>0).mean()*100:.0f}% 扣吃单成本后{(r.mean()-0.0012)*1e4:+.1f}基点 | 各月:', dict(bym), f'| 超过成本的月数 {good}/{len(bym)}')
q=lambda c,p: F[c].quantile(p)
for h in (60,240):
    events(F.liq_proxy==1, 1, '爆仓代理：多单被清后做多', h)
    events(F.liq_proxy==-1, -1, '爆仓代理：空单被清后做空', h)
    events(F.r_60<=q('r_60',.005), 1, '1小时暴跌后做多', h)
    events(F.r_60>=q('r_60',.995), -1, '1小时暴涨后做空', h)
    events(F.funding<=q('funding',.02), 1, '资金费率极低做多', h)
    events(F.funding>=q('funding',.98), -1, '资金费率极高做空', h)
    events(F.book_1>=q('book_1',.99), 1, '盘口买单极厚做多', h)
    events(F.book_1<=q('book_1',.01), -1, '盘口卖单极厚做空', h)
    events((F.vol_surge>=5)&(F.r_5<-0.01), 1, '放量急跌后做多', h)
    events((F.vol_surge>=5)&(F.r_5>0.01), -1, '放量急涨后做空', h)
    print()
