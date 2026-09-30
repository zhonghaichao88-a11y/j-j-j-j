"""第四版（按周期分级别）的汇总：两年按时间对半分；主观筛选与组合同 yw3_eval。"""
import json, os, sys, pandas as pd
import yw3_eval as E
out = []
dly = E.daily('5m2y', 300000)
for mode in ('5m', '1m'):
    fn = f'yuanwen4_{mode}.json'
    if not os.path.exists(fn): continue
    r = json.load(open(fn))
    allst = pd.concat([pd.DataFrame(v) for v in r.values() if v])
    mid = allst.start.min() + (allst.start.max() - allst.start.min()) // 2
    t = E.table(r, lambda d: [('第一年', d.start < mid), ('第二年', d.start >= mid)], dly)
    t.insert(0, '数据', mode); out.append(t)
R = pd.concat(out); R.to_csv('yuanwen4_summary.csv', index=False)
pd.set_option('display.width', 250); pd.set_option('display.max_rows', 500)
keep = ['不加主观', '+大盘(宽度)', '+强势(相对强度)', '+持仓量流入', '+资金费率不拥挤', '全部主观(大盘+强势+持仓量流入)']
print(R[R.主观.isin(keep)].to_string(index=False))
