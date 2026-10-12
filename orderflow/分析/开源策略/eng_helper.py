"""帮总流程提前做"策略机引擎按年测"（从候选名单末尾往前，和总流程两头一起跑）。
规则和 pipeline.py 一样：先测 2022，不到 1.1 停；过了再按 2026、2025、2024、2023、2021 测，哪年不到 1.1 就停。
结果写进 /home/user/ext/ftrun/eng/<类名>/<年>.json，总流程走到时直接用。每测一个之前等内存够（可用 > 5G）。"""
import os, sys, json, time, subprocess
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); PY = '/home/user/ext/ftvenv/bin/python'
RUN = '/home/user/ext/ftrun'; ENG = f'{RUN}/eng'
line = [x for x in open(f'{RUN}/pipeline.log') if '复测候选' in x][-1]
names = line.split(':', 2)[-1].split()
df = pd.read_csv(f'{RUN}/结果_全部.csv')
bad = lambda r: str(r.get('偷看')) == 'True' or 'lookahead' in str(r.cls).lower() or 'lookahead' in str(r.file).lower()
df = df[(df.pf2024 >= 1.2) & (df.pf2025 >= 1.2) & (df.n >= 40)]
df = df[~df.apply(bad, axis=1)].drop_duplicates('cls')            # 和 pipeline.py 选同一份（同名策略取同一个）
sid = {r.cls: f'{r.stage}/{r.id}' for r in df.itertuples()}


def mem_ok():
    for l in open('/proc/meminfo'):
        if l.startswith('MemAvailable'):
            return int(l.split()[1]) > 5 * 1024 * 1024
    return True


def sfile(s):
    d = json.load(open(f'{RUN}/{s}/done.json'))
    return f'{RUN}/{s}/s/' + os.path.basename(d['file'])


def pf(c, y):
    f = f'{ENG}/{c}/{y}.json'
    return json.load(open(f)).get('pf') if os.path.exists(f) else None


def run(c, y):
    if os.path.exists(f'{ENG}/{c}/{y}.json') or os.path.exists(f'{ENG}/{c}/unsupported.json'):
        return
    while not mem_ok():
        time.sleep(30)
    subprocess.run([PY, 'engine_years.py', sfile(sid[c]), c, str(y)], cwd=HERE)


for c in reversed(names):
    if c not in sid:
        print('找不到', c, flush=True); continue
    run(c, 2022)
    if os.path.exists(f'{ENG}/{c}/unsupported.json') or (pf(c, 2022) or 0) < 1.1:
        continue
    for y in (2026, 2025, 2024, 2023, 2021):
        run(c, y)
        if (pf(c, y) or 0) < 1.1:
            break
    print(c, {y: pf(c, y) for y in (2021, 2022, 2023, 2024, 2025, 2026)}, flush=True)
print('ENG HELPER DONE', flush=True)
