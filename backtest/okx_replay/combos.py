from analyze import *
tr=tr.assign(r24=tr.ret24*tr.side);te=te.assign(r24=te.ret24*te.side)
q_risk=tr.s2_risk.quantile(.4)
rules={
 '全部':lambda d:d.index==d.index,
 '4h顺势':lambda d:d.h4==1,
 '4h顺势+BTC顺势':lambda d:(d.h4==1)&(d.btc==1),
 '4h+1h+BTC顺势':lambda d:(d.h4==1)&(d.h1==1)&(d.btc==1),
 '4h+BTC顺势+只做3买卖':lambda d:(d.h4==1)&(d.btc==1)&(d.label=='3买'),
 '4h+BTC顺势+止损≥%.1f%%'%(q_risk*100):lambda d:(d.h4==1)&(d.btc==1)&(d.s2_risk>=q_risk),
 '4h+BTC顺势+3买卖+止损宽':lambda d:(d.h4==1)&(d.btc==1)&(d.label=='3买')&(d.s2_risk>=q_risk),
}
def show(d,title):
    print(title)
    for n,f in rules.items():
        x=d[f(d)]
        cells=' '.join(f'{e}:{fmt(x["s2_"+e])}/{fmt(net(x,"s2",e))}' for e in EX)
        side=' 多%s 空%s'%(fmt(net(x[x.side==1],'s2','trail2atr_h192')),fmt(net(x[x.side==-1],'s2','trail2atr_h192')))
        print(f'  {n:24s} {len(x):5d}笔 {cells} |{side}')
show(tr,'训练段（前/后=扣成本前/后）')
