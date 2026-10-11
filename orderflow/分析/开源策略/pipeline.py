"""开源策略总流程（断了重跑即可，跑完的自动跳过）：
1 三批初筛（20 个大币，2024~2025）→ 2 报错的修好后重跑一次 → 3 排名挑候选（两年 PF 都 ≥1.2、≥40 单、没查出偷看未来）
→ 4 候选用全部 137 个币按年跑 2021~2026 → 5 全币表现好的再查偷看未来 → 6 出汇总表 结果_全币.csv
用法：nohup python pipeline.py >> /home/user/ext/ftrun/pipeline.log 2>&1 &"""
import os, sys, json, glob, subprocess, time
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); PY = '/home/user/ext/ftvenv/bin/python'
RUN = '/home/user/ext/ftrun'; SRC = '/home/user/ext/srcs'
STAGES = [('screen', '_index.json'), ('screen2', '_index_new.json'), ('screen3', '_index_3.json')]
NO_FIX = ('tensorflow', 'xgboost', 'optuna', 'torch', 'keras', '写大量文件')       # 要装大型机器学习库的不重跑
log = lambda *a: print(time.strftime('%m-%d %H:%M'), *a, flush=True)


def busy():
    p = subprocess.run(['pgrep', '-f', 'batch_bt.py|fulltest.py|lookahead.py'], capture_output=True, text=True)
    return [x for x in p.stdout.split() if int(x) != os.getpid()]


def sh(args, env=None):
    subprocess.run([PY] + args, cwd=HERE, env={**os.environ, **(env or {})})


while busy(): time.sleep(30)                    # 先等手动起的那几个跑完，免得同一个策略两边同时跑
for st, idx in STAGES:
    log('初筛', st); sh(['batch_bt.py', st, '20240101-20260101', '20'], {'INDEX': f'{SRC}/{idx}', 'NPROC': '2'})   # 剩下的多是之前报错的，2 个一起跑免得内存不够

for st, idx in STAGES:                           # 报错重跑（每批只做一次）
    mark = f'{RUN}/{st}/_retry_done'
    if os.path.exists(mark): continue
    ids, short = [], set()
    for f in glob.glob(f'{RUN}/{st}/*/done.json'):
        try:
            d = json.load(open(f))
        except ValueError:                       # 写到一半的（磁盘满/被杀）：删掉重跑
            os.remove(f); continue
        e = d.get('err') or ''
        if not e or d.get('retried') or any(x in e for x in NO_FIX): continue
        if 'spot markets' in e: short.add(d['id'])
        ids.append(d['id'])                      # 不删结果文件：batch_bt 用 RETRY=1 重跑一次，记 retried
    rows = json.load(open(f'{SRC}/{idx}'))
    for r in rows:
        if r['h'] + '_' + r['cls'] in short: r['short'] = True
    json.dump(rows, open(f'{SRC}/_retry_{st}.json', 'w'), ensure_ascii=False)
    open(f'{RUN}/{st}/_retry_ids.txt', 'w').write('\n'.join(ids))
    log('报错重跑', st, len(ids), '个（其中改合约模式', len(short), '个）')
    # 重跑只开 2 个：一个 freqtrade 回测要 4~5G 内存，4 个一起跑会被系统杀掉（第一遍就有一些是这样失败的）
    sh(['batch_bt.py', st, '20240101-20260101', '20'], {'INDEX': f'{SRC}/_retry_{st}.json', 'ONLY': f'{RUN}/{st}/_retry_ids.txt', 'NPROC': '2',
                                                        'RETRY': '1'})
    open(mark, 'w').write('ok')

sh(['rank.py'])
df = pd.read_csv(f'{RUN}/结果_全部.csv')
bad = lambda r: str(r.get('偷看')) == 'True' or 'lookahead' in str(r.cls).lower() or 'lookahead' in str(r.file).lower()
cand = df[(df.pf2024 >= 1.2) & (df.pf2025 >= 1.2) & (df.n >= 40)]
cand = cand[~cand.apply(bad, axis=1)].drop_duplicates('cls').sort_values('pf', ascending=False)
ids = [f'{r.stage}/{r.id}' for r in cand.itertuples()]
log('全币复测候选', len(ids), '个:', ' '.join(cand.cls))
for sid in ids: sh(['fulltest.py', sid])

out = []
for sid in ids:
    wd = f'{RUN}/full/{sid.split("/")[-1]}'; row = {'id': sid, 'cls': sid.split('_', 1)[1]}
    for y in range(2021, 2027):
        try: r = json.load(open(f'{wd}/{y}.json'))
        except Exception: continue
        for k in ('n', 'win', 'pf', 'profit', 'dd'): row[f'{k}{y}'] = r.get(k)
    pfs = [row.get(f'pf{y}') for y in range(2021, 2027) if row.get(f'pf{y}') is not None]
    row['赚钱年数'] = sum(p > 1 for p in pfs); row['年数'] = len(pfs); out.append(row)
res = pd.DataFrame(out); res.to_csv(f'{RUN}/结果_全币.csv', index=False)
good = res[(res['赚钱年数'] >= res['年数'] - 1) & (res['年数'] >= 5)] if len(res) else res
log('全币 6 年里最多亏 1 年的', len(good), '个:', ' '.join(good.cls) if len(good) else '')
for sid in good.id:                              # 最后查偷看未来（10 个币、两年，单够多才判得出）
    if os.path.exists(f'{RUN}/{sid}/lookahead.json') and json.load(open(f'{RUN}/{sid}/lookahead.json'))['has_bias'] in (True, False): continue
    st, i = sid.split('/'); sh(['lookahead.py', st, i], {'LA_PAIRS': '10', 'LA_TR': '20240101-20260101'})
sh(['rank.py']); log('PIPELINE DONE')
