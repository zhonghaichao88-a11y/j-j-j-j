"""汇总所有批次初筛结果（读各策略目录的 done.json，比 csv 全），按"两年都赚"排名。用法：python rank.py"""
import json, glob, pandas as pd
RUN = '/home/user/ext/ftrun'
rows = []
for f in glob.glob(f'{RUN}/screen*/*/done.json'):
    d = json.load(open(f)); d['stage'] = f.split('/')[-3]
    la = f.replace('done.json', 'lookahead.json')
    try: d['偷看'] = json.load(open(la))['has_bias']
    except Exception: d['偷看'] = ''
    rows.append(d)
df = pd.DataFrame(rows)
ran = df[df.n.notna() & (df.n > 0)] if 'n' in df else df.iloc[:0]
print('总共', len(df), '个；跑出结果', len(ran), '个；报错/没开单', len(df) - len(ran))
g = ran[(ran.pf2024 >= 1.2) & (ran.pf2025 >= 1.2) & (ran.n >= 40)].sort_values('pf', ascending=False)
pd.set_option('display.width', 250); pd.set_option('display.max_rows', 200)
print(f'\n两年 PF 都 ≥1.2 且 ≥40 单：{len(g)} 个')
print(g[['stage', 'cls', 'repo', 'tf', 'n', 'win', 'profit', 'dd', 'pf', 'pf2024', 'n2024', 'pf2025', 'n2025', '偷看']].to_string(index=False))
df.to_csv(f'{RUN}/结果_全部.csv', index=False)
errs = df[df.err.fillna('') != '']
print('\n报错类型：'); print(errs.err.str.extract(r'(No module named \'\w+\'|No space|spot markets|Impossible to load|DTypePromotion|timed out|超时)')[0].fillna('其它').value_counts().to_string())
