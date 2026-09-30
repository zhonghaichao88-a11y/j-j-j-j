import json, sys, numpy as np, pandas as pd
def summ(fn, splits):
    res = json.load(open(fn))
    rows = []
    for name, v in res.items():
        d = pd.DataFrame(v)
        if d.empty: continue
        d['gross_R'] = d.gross
        for tag, m in splits(d):
            x = d[m]
            if x.empty: continue
            used = x.used.sum(); per = x.pnl / x.used
            rows.append(dict(变体=name, 段=tag, 持仓=len(x), 用风险R=round(used, 1), 扣成本后=round(x.pnl.sum() / used, 3),
                             扣成本前=round(x.gross.sum() / used, 3), 去掉最好5笔=round((x.pnl.sum() - x.pnl.nlargest(5).sum()) / used, 3),
                             胜率=f'{(x.pnl > 0).mean() * 100:.0f}%', 止损占比=f'{(x.exit == "止损").mean() * 100:.0f}%'))
    return pd.DataFrame(rows)
if __name__ == '__main__':
    ra = pd.DataFrame(json.load(open('research_15m_cx76.json'))); split = ra.ts.min() + (ra.ts.max() - ra.ts.min()) // 2
    pd.set_option('display.width', 250)
    out = []
    out.append(summ('yuanwen_15m.json', lambda d: [('A 26年4–6月', d.start < split), ('B 26年7–9月', d.start >= split)]))
    out.append(summ('yuanwen_15m_old.json', lambda d: [('C 25年9月–26年3月', d.start == d.start)]))
    for fn, tag in (('yuanwen_5m.json', '5m 近60天'),):
        try: out.append(summ(fn, lambda d: [(tag, d.start == d.start)]))
        except FileNotFoundError: pass
    try:
        d0 = pd.DataFrame(json.load(open('yuanwen_5m2y.json'))['二层']); mid = d0.start.min() + (d0.start.max() - d0.start.min()) // 2
        out.append(summ('yuanwen_5m2y.json', lambda d: [('5m 两年·第一年', d.start < mid), ('5m 两年·第二年', d.start >= mid)]))
    except FileNotFoundError: pass
    R = pd.concat(out)
    print(R.to_string(index=False))
    R.to_csv('yuanwen_summary.csv', index=False)
