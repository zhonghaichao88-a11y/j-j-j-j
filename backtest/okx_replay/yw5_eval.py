"""方案二的 8 项改法：两批币（原来 40 个两年；新 30 个一年）分别统计，全部加大盘宽度过滤（与方案二一致）。
第 8 项"挂单"＝方案二原样，只把往返成本从 0.2% 降到 0.1%。"""
import json, os, numpy as np, pandas as pd
import yw3_eval as E
from yw4_new_eval import daily_all
dly = daily_all()
rows = []
def stats(x, tag, name, cost_half=False):
    if len(x) == 0: return
    pnl = x.gross - (x.gross - x.pnl) / 2 if cost_half else x.pnl
    used = x.used.sum(); xx = x.assign(pnl=pnl); ret, mdd, n = E.portfolio(xx)
    rows.append(dict(改法=name, 数据=tag, 持仓=len(x), 每1R=round(pnl.sum() / used, 3),
                     去掉最好5笔=round((pnl.sum() - pnl.nlargest(5).sum()) / used, 3) if len(x) > 5 else None,
                     胜率=f'{(pnl > 0).mean() * 100:.0f}%', 组合收益=f'{ret * 100:+.0f}%', 回撤=f'{mdd * 100:.0f}%'))
for fn, label in (('yuanwen5_5m.json', '原40币'), ('yuanwen5_5mnew.json', '新30币')):
    r = json.load(open(fn))
    allst = pd.concat([pd.DataFrame(v) for v in r.values() if v]); mid = allst.start.min() + (allst.start.max() - allst.start.min()) // 2
    for name, v in r.items():
        if name == '同级别分解_30m': continue
        d = pd.DataFrame(v)
        if d.empty: rows.append(dict(改法=name, 数据=label, 持仓=0)); continue
        d = E.features(d, dly); d = d[d.breadth >= 0.5]
        halves = (('前一年', d.start < mid), ('后一年', d.start >= mid)) if label == '原40币' else (('前半年', d.start < mid), ('后半年', d.start >= mid))
        stats(d, label + '·全部', name)
        for t, m in halves: stats(d[m.values], label + '·' + t, name)
        if name == '方案二':
            stats(d, label + '·全部', '8_挂单(成本0.1%)', True)
            for t, m in halves: stats(d[m.values], label + '·' + t, '8_挂单(成本0.1%)', True)
R = pd.DataFrame(rows); R.to_csv('yuanwen5_summary.csv', index=False)
pd.set_option('display.width', 250); pd.set_option('display.max_rows', 300)
print(R.to_string(index=False))
