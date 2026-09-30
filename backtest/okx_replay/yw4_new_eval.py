"""新币检验汇总：30 个没测过的加密币、最近一年、方案二原样配置。大盘宽度用新旧两批币（约 70 个）的日线一起算。"""
import json, os, numpy as np, pandas as pd
import yw3_eval as E
D = 86400000
def daily_all():
    out = {}
    for suf, uni in (('5m2y', json.load(open('universe_5m40.json'))), ('5mnew', json.load(open('universe_new30.json')))):
        for inst in uni:
            p = f'{inst}_{suf}.npz'
            if not os.path.exists(p): continue
            z = np.load(p); ts = z['ts']; c = z['close']; b = ts // D
            last = np.flatnonzero(np.r_[b[1:] != b[:-1], True]); full = (ts[last] + 300000) % D == 0
            dts = b[last][full] * D; dc = c[last][full]
            if len(dc) < 60: continue
            e = np.empty(len(dc)); e[0] = dc[0]
            for i in range(1, len(dc)): e[i] = e[i - 1] + 2 / 51 * (dc[i] - e[i - 1])
            out[inst] = (dts, dc, e)
    return out
r = json.load(open('yuanwen4_5mnew.json')); dly = daily_all()
allst = pd.concat([pd.DataFrame(v) for v in r.values() if v]); mid = allst.start.min() + (allst.start.max() - allst.start.min()) // 2
rows = []
for name, v in r.items():
    d = pd.DataFrame(v)
    if d.empty: continue
    d = E.features(d, dly)
    for ptag, pm in (('前半年', d.start < mid), ('后半年', d.start >= mid), ('全年', d.start > 0)):
        for fname, fm in (('不加主观', np.ones(len(d), bool)), ('+大盘(宽度)', (d.breadth >= 0.5).values)):
            x = d[np.asarray(pm) & fm]
            if len(x) == 0: continue
            used = x.used.sum(); ret, mdd, n = E.portfolio(x)
            rows.append(dict(规则=name, 主观=fname, 段=ptag, 持仓=len(x), 扣成本后=round(x.pnl.sum() / used, 3),
                             去掉最好5笔=round((x.pnl.sum() - x.pnl.nlargest(5).sum()) / used, 3) if len(x) > 5 else None,
                             胜率=f'{(x.pnl > 0).mean() * 100:.0f}%', 组合收益=f'{ret * 100:+.0f}%', 组合回撤=f'{mdd * 100:.0f}%'))
R = pd.DataFrame(rows); R.to_csv('yuanwen4_new30_summary.csv', index=False)
pd.set_option('display.width', 250); print(R.to_string(index=False))
