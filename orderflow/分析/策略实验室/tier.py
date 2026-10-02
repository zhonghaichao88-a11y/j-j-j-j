"""分档仓位：满足"之前已经跌了一段"的信号用大仓位，其他信号用小仓位"""
import numpy as np, pandas as pd
from data import TRAIN_END
exec(open('filt2.py').read().split("def merge")[0].replace("print(", "(lambda *a, **k: None)("))
def merge(*ts):
    T = pd.concat(ts).sort_values('t_in'); keep, busy = [], {}
    for i, r in enumerate(T.itertuples()):
        if r.t_in >= busy.get(r.coin, 0): keep.append(i); busy[r.coin] = r.t_out
    return T.iloc[keep]
parts = []
for name, (T, F) in out.items():
    T = T.copy(); T['good'] = T.set_index(['coin', 't']).index.isin(F.set_index(['coin', 't']).index); parts.append(T)
D = merge(*parts)
def port(T, sz_good, sz_other, cap=10, start=100.0):
    rows = T.sort_values('t_in').reset_index(drop=True)
    ev = sorted([(a, 0, k) for k, a in enumerate(rows.t_in)] + [(b, 1, k) for k, b in enumerate(rows.t_out)])
    eq, pos, curve = start, {}, []
    for t, typ, k in ev:
        if typ == 0:
            if len(pos) < cap: pos[k] = eq * (sz_good if rows.good[k] else sz_other)
        elif k in pos:
            eq += pos.pop(k) * rows.ret[k]; curve.append((t, eq))
    c = pd.Series([e for _, e in curve], index=pd.to_datetime([t for t, _ in curve], unit='ms'))
    m = c.resample('ME').last().pct_change(); yrs = (c.index[-1] - c.index[0]).days / 365
    return {'结果': round(c.iloc[-1]), '年化%': round(((c.iloc[-1] / start) ** (1 / yrs) - 1) * 100), '最大回撤%': round((c / c.cummax() - 1).min() * 100), '亏钱月': f'{int((m < 0).sum())}/{int(m.notna().sum())}'}
for g, o in ((.10, .10), (.15, .05), (.20, .05), (.15, .03), (.10, .05), (.10, .0)):
    print(f'好信号 {g:.0%} / 其他 {o:.0%}：全期 {port(D, g, o)}  只看考试期 {port(D[D.t >= TRAIN_END], g, o)}')
