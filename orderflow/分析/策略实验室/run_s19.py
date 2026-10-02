import json, pandas as pd, runner, report
mod = 's19_momo'
res, errs, secs = runner.run(mod)
txt = runner.show(mod, res, errs, secs)
open(f'results/{mod}.md', 'w').write(txt)
print(txt)
for r in res:
    r['T'].to_parquet(f'results/trades_{mod}_{r["k"]}.parquet')
