"""汇总回放结果。用法: python3 summarize.py 配置名 [配置名...]"""
import json,sys,collections,datetime,os
import numpy as np
DATA=os.path.dirname(os.path.abspath(__file__));CAT=json.load(open(os.path.join(DATA,'categories.json')))
def line(x):
    x=np.asarray(x,float)
    if not len(x):return '0笔'
    return f'{len(x)}笔 胜率{np.mean(x>0)*100:.0f}% 平均{x.mean():+.3f}R 合计{x.sum():+.1f}R'
def portfolio(trades,cap=4,risk=0.01):
    """按开仓时间排队，同时最多 cap 笔；每笔亏1R=权益的 risk；不复利。"""
    ev=sorted(trades,key=lambda t:t['opened_ms']);open_=[];eq=0;curve=[];taken=0
    for t in ev:
        open_=[c for c in open_ if c>t['opened_ms']]
        if len(open_)>=cap:continue
        open_.append(t['closed_ms']);taken+=1;eq+=t['r']*risk;curve.append(eq)
    c=np.array(curve) if curve else np.zeros(1);dd=float(np.max(np.maximum.accumulate(c)-c))
    return taken,eq,dd
for name in sys.argv[1:]:
    d=json.load(open(os.path.join(DATA,f'result_{name}.json')));T=d['trades']
    crypto=[t for t in T if CAT.get(t['symbol'])=='1'];other=[t for t in T if CAT.get(t['symbol'])!='1']
    st=d['stats'];errs={k:v['error'] for k,v in st.items() if 'error' in v}
    sig=sum(v.get('signals',0) for v in st.values());rej=collections.Counter()
    for v in st.values():rej.update(v.get('reasons',{}))
    days=[(t['closed_ms']-t['opened_ms'])/3.6e6 for t in crypto]
    print(f'\n######## {name}  {d["cfg"]}')
    print(f'确认信号 {sig}，开仓 {len(T)}；主要拒绝 {dict(rej.most_common(4))}' + (f'；出错 {errs}' if errs else ''))
    print(f'加密币({len({t["symbol"] for t in crypto})}个): {line([t["r"] for t in crypto])}；平均持仓 {np.mean(days) if days else 0:.1f} 小时')
    print(f'股票/商品({len({t["symbol"] for t in other})}个): {line([t["r"] for t in other])}')
    by=collections.defaultdict(list)
    for t in crypto:by[t['label'].replace('卖','买')].append(t['r'])
    print('  按买卖点(加密币,买卖合并):',' | '.join(f'{k}:{line(v)}' for k,v in sorted(by.items())))
    by=collections.defaultdict(list)
    for t in crypto:by[t['reason'][:6] if not t['reason'].startswith('缠论反向') else '缠论反向'].append(t['r'])
    print('  按出场原因:',' | '.join(f'{k}:{len(v)}笔 平均{np.mean(v):+.2f}R' for k,v in sorted(by.items(),key=lambda x:-len(x[1]))))
    by=collections.defaultdict(list)
    for t in crypto:by[datetime.datetime.utcfromtimestamp(t['opened_ms']/1000).strftime('%Y-%m')].append(t['r'])
    print('  按月份:',' | '.join(f'{k}:{len(v)}笔 {np.sum(v):+.1f}R' for k,v in sorted(by.items())))
    if crypto:
        mid=np.median([t['opened_ms'] for t in crypto])
        print('  前半段:',line([t['r'] for t in crypto if t['opened_ms']<mid]),' 后半段:',line([t['r'] for t in crypto if t['opened_ms']>=mid]))
        per=collections.defaultdict(float)
        for t in crypto:per[t['symbol']]+=t['r']
        v=np.array(list(per.values()));print(f'  盈利币 {np.sum(v>0)}/{len(v)}；单币合计中位数 {np.median(v):+.1f}R；最差 {min(per.items(),key=lambda x:x[1])}')
        long_=[t['r'] for t in crypto if t['side']=='long'];short=[t['r'] for t in crypto if t['side']=='short']
        print('  做多:',line(long_),' 做空:',line(short))
        k,eq,dd=portfolio(crypto);print(f'  组合(最多同时4笔,每笔风险1%权益,不复利): 实际成交{k}笔 收益{eq*100:+.1f}% 最大回撤{dd*100:.1f}%')
