"""实盘程序的 MomoTracker 和回测公式对比：同一份币安 5 分钟数据，信号时间是否一样"""
import sys, math, numpy as np, pandas as pd
sys.path.insert(0, '/home/user/j-j-j-j/orderflow'); sys.path.insert(0, '/home/user/ext/long/lab')
import of_engine as E, data, s19_momo as S
p = {'a': 0.2, 'v': 3, 'sd': 0.15, 'hold': 288}
tot = same = only_bt = only_live = 0
for c in sys.argv[1:]:
    df = S.prep(data.load(c))
    m = (df.r24h > p['a']) & (df.vsurge >= p['v']) & (df.r60 > 0)
    bt = set(df.index.values[m.fillna(False).values])
    tr = E.MomoTracker(); live = set()
    for t, cl, qv in zip(df.index.values, df.c.values, df.qv.values):
        if not (cl > 0) or math.isnan(qv):
            continue
        tr.add(int(t), float(cl), float(qv))
        r24, r60, vx = tr.state(int(t))
        if r24 > p['a'] and vx >= p['v'] and r60 > 0:
            live.add(int(t))
    # 回测这边加冷却（同一个币 24 小时只做一次），两边同样处理
    def cool(s):
        out, last = [], -10**15
        for t in sorted(s):
            if t - last >= 86_400_000:
                out.append(t); last = t
        return set(out)
    b, l = cool(bt), cool(live)
    tot += len(b); same += len(b & l); only_bt += len(b - l); only_live += len(l - b)
    print(c, '回测', len(b), '实盘', len(l), '一样', len(b & l))
print('合计 回测', tot, '一样', same, '只有回测', only_bt, '只有实盘', only_live)
