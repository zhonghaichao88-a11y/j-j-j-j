"""用 100 个币的 15 分钟数据检验第四版（次级别换成 15 分钟）。分三段时间；"新币" = 不在 5m 两年那 40 个币里。"""
import json, numpy as np, pandas as pd
import yw3_eval as E
old40 = set(json.load(open('universe_5m40.json')))
ra = pd.DataFrame(json.load(open('research_15m_cx76.json'))); split = ra.ts.min() + (ra.ts.max() - ra.ts.min()) // 2
rows = []
for fn, suf, periods in (('yuanwen4_15m.json', '15m', lambda d: [('A 26年4–6月', d.start < split), ('B 26年7–9月', d.start >= split)]),
                         ('yuanwen4_15m_old.json', '15m_old', lambda d: [('C 25年9月–26年3月', d.start > 0)])):
    r = json.load(open(fn)); dly = E.daily(suf, 900000)
    for name, v in r.items():
        d = pd.DataFrame(v)
        if d.empty: continue
        d = E.features(d, dly)
        for ptag, pm in periods(d):
            for coins, cm in (('全部币', np.ones(len(d), bool)), ('新币', ~d.inst.isin(old40).values)):
                for fname, fm in (('不加主观', np.ones(len(d), bool)), ('+大盘(宽度)', (d.breadth >= 0.5).values)):
                    x = d[np.asarray(pm) & cm & fm]
                    if len(x) == 0: continue
                    used = x.used.sum()
                    rows.append(dict(规则=name, 币=coins, 主观=fname, 段=ptag, 持仓=len(x), 扣成本后=round(x.pnl.sum() / used, 3),
                                     去掉最好5笔=round((x.pnl.sum() - x.pnl.nlargest(5).sum()) / used, 3) if len(x) > 5 else None,
                                     胜率=f'{(x.pnl > 0).mean() * 100:.0f}%'))
R = pd.DataFrame(rows); R.to_csv('yuanwen4_val_summary.csv', index=False)
pd.set_option('display.width', 250); pd.set_option('display.max_rows', 300)
print(R.to_string(index=False))
