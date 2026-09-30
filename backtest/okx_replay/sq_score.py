"""压缩释放突破 + 订单流打分调仓：7 个条件每满足一个得 1 分（都用开仓前已知数据，方向已调整）：
OKX Delta>5%、持仓量4h增加、币安主动买卖同向、盘口±2%同向、大户多空比同向、散户多空比不拥挤、资金费率不拥挤。
仓位：按分数给倍数（0=不开）。各批币分别算总赚(份)、每份风险收益、笔数。"""
import numpy as np, pandas as pd
B = pd.read_csv('sq_bn.csv'); B['r'] = B.r.clip(-3, 50)
C = {'Delta>5%': B.delta > .05, '持仓量4h增加': B.oi4 > 0, '币安主动同向': B.taker > 0, '盘口±2%同向': B.dep2 > 0,
     '大户多空比同向': B.top_ls > 0, '散户不拥挤': B.all_ls < 0, '资金费率不拥挤': B.fund <= 0}
B['score'] = sum(v.fillna(False).astype(int) for v in C.values())
print('分数分布：', B.score.value_counts().sort_index().to_dict())
print('\n每个分数的平均收益（每份风险）：')
G = (('old40', '老40'), ('new30', '新30'), ('rest34', '另34'), ('more', '更多币'), (None, '全部'))
rows = []
for s in range(8):
    row = dict(分数=s)
    for g, l in G:
        d = B if g is None else B[B.grp == g]; x = d[d.score == s]
        if not d.empty: row[l] = f'{len(x)}笔 {x.r.mean():+.2f}' if len(x) else '-'
    rows.append(row)
pd.set_option('display.width', 300); print(pd.DataFrame(rows).to_string(index=False))
PLANS = {'不调仓': {s: 1 for s in range(8)},
         '≤2分不开/3分0.5/4分1/≥5分2': {0: 0, 1: 0, 2: 0, 3: .5, 4: 1, 5: 2, 6: 2, 7: 2},
         '≤2分不开/3分1/4分1.5/≥5分2': {0: 0, 1: 0, 2: 0, 3: 1, 4: 1.5, 5: 2, 6: 2, 7: 2},
         '≤3分不开/4分1/≥5分2': {0: 0, 1: 0, 2: 0, 3: 0, 4: 1, 5: 2, 6: 2, 7: 2},
         '按分数线性(分数-2)*0.5，最少0': {s: max(0, (s - 2) * .5) for s in range(8)}}
out = []
for name, m in PLANS.items():
    row = dict(方案=name)
    for g, l in G:
        d = B if g is None else B[B.grp == g]
        if d.empty: continue
        w = d.score.map(m).values; tot = (w * d.r).sum()
        row[l] = f'{tot:+.1f}份 ({tot / max(w.sum(), 1e-9):+.3f}/份, {int((w > 0).sum())}笔)'
    out.append(row)
print('\n调仓方案（总赚多少份风险）：'); print(pd.DataFrame(out).to_string(index=False))
