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
    """只认真正在跑的 python batch_bt/fulltest/lookahead 进程（不认命令行里碰巧带这几个字的 shell）"""
    p = subprocess.run(['ps', '-eo', 'pid=,args='], capture_output=True, text=True)
    return [l.split()[0] for l in p.stdout.splitlines() if len(l.split()) > 2 and l.split()[1] == PY
            and l.split()[2] in ('batch_bt.py', 'fulltest.py', 'lookahead.py')]


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

for st, idx in STAGES:                           # 老写法（ticker_interval，没写 timeframe）报"Timeframe needs to be set"的：配置里补上周期再跑一次
    mark = f'{RUN}/{st}/_tffix_done'
    if os.path.exists(mark): continue
    ids = []
    for f in glob.glob(f'{RUN}/{st}/*/done.json'):
        try:
            d = json.load(open(f))
        except ValueError:
            continue
        if 'Timeframe needs' in (d.get('err') or '') and not d.get('tf_fix'): ids.append(d['id'])
    if ids:
        ix = f'{SRC}/_retry_{st}.json' if os.path.exists(f'{SRC}/_retry_{st}.json') else f'{SRC}/{idx}'
        open(f'{RUN}/{st}/_tffix_ids.txt', 'w').write('\n'.join(ids))
        log('老写法补周期重跑', st, len(ids), '个')
        sh(['batch_bt.py', st, '20240101-20260101', '20'], {'INDEX': ix, 'ONLY': f'{RUN}/{st}/_tffix_ids.txt', 'NPROC': '2', 'TFFIX': '1'})
    open(mark, 'w').write('ok')

for st, idx in STAGES:                           # "Impossible to load"（类名后多空格、没 import logging/IStrategy）：复制时已修正，再跑一次
    mark = f'{RUN}/{st}/_loadfix_done'
    if os.path.exists(mark): continue
    ids = []
    for f in glob.glob(f'{RUN}/{st}/*/done.json'):
        try:
            d = json.load(open(f))
        except ValueError:
            continue
        if 'Impossible to load' in (d.get('err') or '') and not d.get('load_fix'): ids.append(d['id'])
    if ids:
        ix = f'{SRC}/_retry_{st}.json' if os.path.exists(f'{SRC}/_retry_{st}.json') else f'{SRC}/{idx}'
        open(f'{RUN}/{st}/_loadfix_ids.txt', 'w').write('\n'.join(ids))
        log('加载失败修正后重跑', st, len(ids), '个')
        sh(['batch_bt.py', st, '20240101-20260101', '20'], {'INDEX': ix, 'ONLY': f'{RUN}/{st}/_loadfix_ids.txt', 'NPROC': '2', 'LOADFIX': '1'})
    open(mark, 'w').write('ok')

sh(['rank.py'])
df = pd.read_csv(f'{RUN}/结果_全部.csv')
bad = lambda r: str(r.get('偷看')) == 'True' or 'lookahead' in str(r.cls).lower() or 'lookahead' in str(r.file).lower()
cand = df[(df.pf2024 >= 1.2) & (df.pf2025 >= 1.2) & (df.n >= 40)]
cand = cand[~cand.apply(bad, axis=1)].drop_duplicates('cls').sort_values('pf', ascending=False)
# 1) 换了名字的同一个策略（单数、两年盈亏比都一样）只留一个
cand = cand.assign(_k=cand.n.astype(str) + '_' + cand.pf2024.round(2).astype(str) + '_' + cand.pf2025.round(2).astype(str)).drop_duplicates('_k')
# 2) 明显不可能的（两年收益上万倍、盈亏比上百、几百单胜率 97% 以上）= 偷看未来或算错，直接剔除
absurd = (cand.profit > 10000) | (cand.pf > 50) | ((cand.win >= 0.97) & (cand.n >= 200))
log('候选', len(cand), '个；结果离谱直接剔除', int(absurd.sum()), '个:', ' '.join(cand[absurd].cls))
cand = cand[~absurd]
# 3) 剩下的先查偷看未来（freqtrade lookahead-analysis，10 个币、2024 一年），查出有问题的剔除
for r in cand.itertuples():
    laf = f'{RUN}/{r.stage}/{r.id}/lookahead.json'
    if not os.path.exists(laf):
        sh(['lookahead.py', r.stage, r.id], {'LA_PAIRS': '10', 'LA_TR': '20240101-20250101'})
def la(r):
    try:
        return json.load(open(f'{RUN}/{r.stage}/{r.id}/lookahead.json'))['has_bias']
    except Exception:  # noqa: BLE001
        return '?'
cand = cand.assign(_la=[la(r) for r in cand.itertuples()])
log('偷看未来检测：有问题', int((cand._la == True).sum()), '个:', ' '.join(cand[cand._la == True].cls))  # noqa: E712
cand = cand[cand._la != True]  # noqa: E712
ids = [f'{r.stage}/{r.id}' for r in cand.itertuples()]
log('复测候选', len(ids), '个:', ' '.join(cand.cls))
ENG = '/home/user/ext/ftrun/eng'


def sfile(sid):
    """策略文件（初筛时复制进来的那份；文件名是原文件名，不一定等于类名）"""
    d = json.load(open(f'{RUN}/{sid}/done.json'))
    return f'{RUN}/{sid}/s/' + os.path.basename(d['file'])


def eng(sid, years):
    """策略机引擎（和 freqtrade 逐单对过账）在欧易 98 个币上按年跑。返回 {年: 结果} 或 'unsupported'"""
    c = sid.split('/')[1].split('_', 1)[1]
    sh(['engine_years.py', sfile(sid), c, ','.join(map(str, years))])
    if os.path.exists(f'{ENG}/{c}/unsupported.json'):
        return 'unsupported'
    return {y: json.load(open(f'{ENG}/{c}/{y}.json')) for y in years if os.path.exists(f'{ENG}/{c}/{y}.json')}


out = []
for sid in ids:
    c = sid.split('/')[1].split('_', 1)[1]; row = {'id': sid, 'cls': c}
    r = eng(sid, [2022])                          # 先看 2022 熊市（2 分钟一个），亏的不往下测
    if r == 'unsupported':
        row['结论'] = '策略机暂时跑不了：' + ', '.join(json.load(open(f'{ENG}/{c}/unsupported.json'))['unsupported'])
        out.append(row); continue
    row['pf2022'] = r.get(2022, {}).get('pf')
    if (row['pf2022'] or 0) < 1.1:
        row['结论'] = '2022 熊市不行'; out.append(row); continue
    r = {2022: r[2022]} if 2022 in r else {}
    for y in (2026, 2025, 2024, 2023, 2021):     # 一年一年测，最近的年份先测（最容易不行），有一年不到 1.1 就停（结论一样，省时间）
        ry = eng(sid, [y])
        if ry == 'unsupported' or y not in ry:
            break
        r[y] = ry[y]
        if (r[y]['pf'] or 0) < 1.1:
            break
    for y, v in r.items():
        row[f'pf{y}'], row[f'n{y}'] = v['pf'], v['n']
    pfs = [v['pf'] for v in r.values()]
    if len(pfs) < 6 or min(pfs) < 1.1:
        row['结论'] = '98 个币有年份不到 1.1'; out.append(row); continue
    sh(['allcoins_bn.py', c], {'STRAT_FILE': sfile(sid)})
    try:
        sm = pd.read_csv(f'/home/user/ext/ftrun/allbn_{c}/summary.csv')
        sm = sm[sm['移动止盈少'] == '0.0%']
        for _, x in sm.iterrows():
            row[f'全币pf{x["年"]}'] = x['盈亏比']
        row['结论'] = '全部币每年都 ≥1.1，可以加进策略机' if sm['盈亏比'].min() >= 1.1 else '全部币有年份不到 1.1'
    except Exception as e:  # noqa: BLE001
        row['结论'] = f'全部币测试没跑完：{e}'
    out.append(row)
    pd.DataFrame(out).to_csv(f'{RUN}/结果_候选.csv', index=False)
res = pd.DataFrame(out); res.to_csv(f'{RUN}/结果_候选.csv', index=False)
log('候选结论：', '; '.join(f"{r['cls']}={r['结论']}" for r in out))
sh(['rank.py']); log('PIPELINE DONE')
