"""压缩释放突破 + 订单流调仓：按突破K Delta 决定仓位倍数，各批币分别算总赚(份)与每份风险收益；另算组合(最多10仓,基础每笔1%)。"""
import numpy as np, pandas as pd
T = pd.read_csv('sq_flow.csv'); T['r'] = T.r.clip(-3, 50)
S = {'不调仓': lambda d: np.ones(len(d)),
     '反向半仓/普通1/同向>5%就1.5': lambda d: np.where(d.delta < 0, .5, np.where(d.delta > .05, 1.5, 1)),
     '反向不做/普通1/同向>5%就2': lambda d: np.where(d.delta < 0, 0, np.where(d.delta > .05, 2, 1)),
     '只做同向>5%(1倍)': lambda d: np.where(d.delta > .05, 1, 0)}
G = (('old40', '老40'), ('new30', '新30'), ('rest34', '另34'), ('more', '更多币'), (None, '全部'))
rows = []
for name, fn in S.items():
    row = dict(方案=name)
    for g, l in G:
        d = T if g is None else T[T.grp == g]
        if d.empty: continue
        w = fn(d); row[l] = f'{(w * d.r).sum():+.1f}份 ({(w * d.r).sum() / max(w.sum(), 1e-9):+.3f}/份, {int((w > 0).sum())}笔)'
    rows.append(row)
pd.set_option('display.width', 300); pd.set_option('display.max_colwidth', 60)
print(pd.DataFrame(rows).to_string(index=False))
# 按半年分段看“反向不做/同向加倍”是否每段都比不调仓好
T['half'] = pd.to_datetime(T.opened, unit='ms').dt.to_period('Q').astype(str)
w0 = S['不调仓'](T); w1 = S['反向不做/普通1/同向>5%就2'](T)
Q = T.assign(a=w0 * T.r, b=w1 * T.r).groupby('half')[['a', 'b']].sum().round(1)
Q.columns = ['不调仓(份)', '反向不做同向加倍(份)']
print('\n按季度：'); print(Q.to_string())
