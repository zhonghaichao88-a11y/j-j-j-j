import pandas as pd, numpy as np
F=pd.read_pickle('F.pkl'); days=sorted(F.day.unique())
def study(name, mask_long, mask_short):
    for h in (15,60):
        y=f'fwd_{h}'
        out=[]
        for i,d in enumerate(days):
            D=F[F.day==d]
            l=D[mask_long(D)][y].dropna(); s=-D[mask_short(D)][y].dropna()
            r=pd.concat([l,s])
            out.append(f'第{i+1}天 {len(r)}次 平均{r.mean()*1e4:+.1f}基点 赢{(r>0).mean()*100:.0f}%')
        print(f'{name} 未来{h}分钟: ' + ' | '.join(out))
q=lambda c,p: F[c].quantile(p)
ln_hi=q('liq_net_5',0.995); ln_lo=q('liq_net_5',0.005)
study('多单大爆→做多/空单大爆→做空', lambda D:D.liq_net_5>=ln_hi, lambda D:D.liq_net_5<=ln_lo)
r_hi=q('r_60',0.99); r_lo=q('r_60',0.01)
study('1小时暴涨→做空/暴跌→做多', lambda D:D.r_60<=r_lo, lambda D:D.r_60>=r_hi)
f_hi=q('funding',0.95); f_lo=q('funding',0.05)
study('资金费率最低5%→做多/最高5%→做空', lambda D:D.funding<=f_lo, lambda D:D.funding>=f_hi)
o_hi=q('oi_5',0.99); o_lo=q('oi_5',0.01)
study('持仓5分钟大降→做多/大增→做空', lambda D:D.oi_5<=o_lo, lambda D:D.oi_5>=o_hi)
i_hi=q('imb_5',0.99); i_lo=q('imb_5',0.01)
study('盘口买单厚→做多/卖单厚→做空', lambda D:D.imb_5>=i_hi, lambda D:D.imb_5<=i_lo)
print('参考：吃单来回成本约 12 基点（0.12%），挂单约 4 基点')
