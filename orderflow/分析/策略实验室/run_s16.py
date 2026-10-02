import pandas as pd, numpy as np, runner, report
mod = 's16_bearshort'
res, errs, secs = runner.run(mod)
print('用时', round(secs), '出错', errs[:3])
pf = report.pf
rows = []
CUT, RECENT = 1767225600000, 1775001600000            # 2026-01-01 / 2026-04-01
for r in res:
    T = r['T']
    if len(T) == 0:
        continue
    tr, te, rc = T[T.t < CUT], T[T.t >= CUT], T[T.t >= RECENT]
    old, new = T[~T.coin.isin(report.NEW)], T[T.coin.isin(report.NEW)]
    q = pd.to_datetime(T.t, unit='ms').dt.to_period('Q')
    byq = T.groupby(q).ret.mean()
    rows.append({**r['p'], '笔数': len(T), '胜率': round((T.ret > 0).mean() * 100), '每笔%': round(T.ret.mean() * 100, 2), 'PF': round(pf(T.ret), 2),
                 '前段笔': len(tr), '前段PF': round(pf(tr.ret), 2), '2026笔': len(te), '2026PF': round(pf(te.ret), 2), '近6月PF': round(pf(rc.ret), 2),
                 '老币PF': round(pf(old.ret), 2), '新币PF': round(pf(new.ret), 2), '赚钱季度': f'{(byq > 0).sum()}/{len(byq)}',
                 '止损%': round((T.why == '止损').mean() * 100)})
    T.to_parquet(f'results/trades_{mod}_{r["k"]}.parquet')
R = pd.DataFrame(rows)
pd.set_option('display.width', 250)
print(R.to_string(index=False))
R.to_csv(f'results/{mod}_summary.csv', index=False)
