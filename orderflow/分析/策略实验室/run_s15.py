import json, pandas as pd, runner, data, report
mod = 's15_liqrun'
res, errs, secs = runner.run(mod)
open(f'results/{mod}.md', 'w').write(runner.show(mod, res, errs, secs))
b = data.load('BTC'); d = b.c.groupby(b.index.values // 86_400_000).last()
bull = (d > d.rolling(200).mean()).shift(1)
pf = lambda x: round(x[x > 0].sum() / max(-x[x < 0].sum(), 1e-9), 2)
rows = []
for r in res:
    T = r['T']
    if len(T) == 0:
        continue
    reg = (T.t // 86_400_000).map(bull)
    sp = r['sp']; p = r['p']
    rows.append({**p, '笔数': len(T), '胜率': round((T.ret > 0).mean() * 100), '每笔%': round(T.ret.mean() * 100, 2), 'PF': pf(T.ret),
                 '训练PF': sp['训练2024'].get('PF'), '考试PF': sp['考试2025+'].get('PF'), '近6月PF': sp['最近6个月'].get('PF'), '近6月笔': sp['最近6个月'].get('笔数'),
                 '牛PF': pf(T.ret[reg == True]), '熊PF': pf(T.ret[reg == False]), '熊笔': int((reg == False).sum()),
                 '止损%': round((T.why == '止损').mean() * 100), '过关': report.passed(sp)})
    T.to_parquet(f'results/trades_{mod}_{r["k"]}.parquet')
R = pd.DataFrame(rows)
pd.set_option('display.width', 250); pd.set_option('display.max_rows', 200)
print(R.to_string(index=False))
R.to_csv(f'results/{mod}_summary.csv', index=False)
