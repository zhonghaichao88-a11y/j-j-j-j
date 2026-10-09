"""打包检查（每个细节）：
1. 两个包里每个文件和代码库里的现版本逐字节比（不一样就报）
2. 不该带的：.env、密钥、个人状态（of_state*、of_trades、of_log）、你自己的设置 of_config.json、研究数据（分析/、回测结果/）、缓存
3. 完整包要齐：代码库里程序要用的文件（orderflow/ 下除研究目录）一个不能少
4. 覆盖包要齐：从完整包那次以后改过的程序文件都要在覆盖包里
5. 包里所有文本扫一遍有没有像密钥的东西
6. 全新文件夹：解压完整包，导入所有模块；启动网页服务（不填密钥），每个打法勾选 / 取消、每个参数框保存后读回来
用法：python pkg_audit.py"""
import os, sys, re, io, json, zipfile, subprocess, tempfile, time, shutil, socket
REPO = '/home/user/j-j-j-j'; OF = f'{REPO}/orderflow'
FULL, OVER = f'{REPO}/订单流_完整包.zip', f'{REPO}/订单流_覆盖文件.zip'
BAD = re.compile(r'(^|/)(\.env$|of_state|of_trades|of_log|of_config\.json$|分析/|回测结果/|__pycache__|\.venv|fp_data|data_rec|发给Claude|.*密钥)')
problems = []; notes = []


def name(it):
    return it.filename if it.flag_bits & 0x800 else it.filename.encode('cp437').decode('utf-8')


def entries(zp, prefix):
    z = zipfile.ZipFile(zp)
    return {name(it)[len(prefix):]: z.read(it.filename) for it in z.infolist() if not name(it).endswith('/')}


full = entries(FULL, '订单流/'); over = entries(OVER, '')
for tag, E in (('完整包', full), ('覆盖包', over)):
    for rel, data in E.items():
        if BAD.search(rel):
            problems.append(f'{tag} 带了不该带的：{rel}')
        p = f'{OF}/{rel}'
        if not os.path.exists(p):
            problems.append(f'{tag} 里的 {rel} 代码库里没有（多余或过时）')
        elif open(p, 'rb').read() != data:
            problems.append(f'{tag} 里的 {rel} 和代码库现版本不一样（旧了）')
        if rel.endswith(('.py', '.md', '.json', '.html', '.bat', '.txt', '.example')):
            t = data.decode('utf-8', 'ignore')
            for m in re.finditer(r'(?i)(api[_-]?key|secret|passphrase)\s*[=:]\s*["\']?([A-Za-z0-9+/\-]{16,})', t):
                problems.append(f'{tag} {rel} 里像密钥：{m.group(0)[:40]}…')
tracked = subprocess.run(['git', 'ls-files', 'orderflow'], cwd=REPO, capture_output=True, text=True).stdout.split('\n')
prog = sorted(f[len('orderflow/'):] for f in tracked if f and not BAD.search(f[len('orderflow/'):]))
for f in prog:
    if f not in full:
        problems.append(f'完整包缺：{f}')
base = subprocess.run(['git', 'log', '-1', '--format=%H', '--', '订单流_完整包.zip'], cwd=REPO, capture_output=True, text=True).stdout.strip()
first = subprocess.run(['git', 'log', '--format=%H', '--diff-filter=A', '--', '订单流_完整包.zip'], cwd=REPO, capture_output=True, text=True).stdout.split()[-1]
changed = subprocess.run(['git', 'diff', '--name-only', first, 'HEAD', '--', 'orderflow'], cwd=REPO, capture_output=True, text=True).stdout.split('\n')
for f in changed:
    rel = f[len('orderflow/'):]
    if f and rel in prog and rel not in over:
        problems.append(f'覆盖包缺（完整包第一版以后改过）：{rel}')
notes.append(f'完整包 {len(full)} 个文件，覆盖包 {len(over)} 个文件，代码库程序文件 {len(prog)} 个')

# 6. 全新文件夹启动
tmp = tempfile.mkdtemp(prefix='of_fresh_')
zipfile.ZipFile(FULL).extractall(tmp)
for it in zipfile.ZipFile(FULL).infolist():                  # 中文文件名按 UTF-8 解出来
    n = name(it)
    if n != it.filename:
        src = os.path.join(tmp, it.filename); dst = os.path.join(tmp, n)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(src) and not os.path.isdir(src): shutil.move(src, dst)
app_dir = os.path.join(tmp, '订单流')
PY = '/home/user/ext/ftvenv/bin/python'
r = subprocess.run([PY, '-c', 'import of_core, of_engine, of_playbook, of_feed, of_xfeed, of_live, of_notify, of_nfi; print("ok")'], cwd=app_dir, capture_output=True, text=True)
if 'ok' not in r.stdout:
    problems.append('全新文件夹导入模块失败：' + r.stderr[-300:])
shutil.copy(os.path.join(app_dir, '.env.example'), os.path.join(app_dir, '.env'))
with socket.socket() as s:
    s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
env = dict(os.environ, OF_PORT=str(port), PROXY_URL=os.environ.get('HTTPS_PROXY', ''))
srv = subprocess.Popen([PY, 'of_app.py'], cwd=app_dir, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
import urllib.request
def get(p):
    return json.loads(urllib.request.urlopen(f'http://127.0.0.1:{port}{p}', timeout=20).read())
def post(p, body):
    rq = urllib.request.Request(f'http://127.0.0.1:{port}{p}', data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    return urllib.request.urlopen(rq, timeout=20).read()
ok = False
for _ in range(90):
    try:
        get('/api/ready'); ok = True; break
    except Exception:
        time.sleep(1)
if not ok:
    problems.append('全新文件夹：网页服务 90 秒没起来')
else:
    sys.path.insert(0, app_dir)
    import importlib; E = importlib.import_module('of_engine')
    page = urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=20).read().decode('utf-8')
    kinds = list(E.ALL_NAMES)
    miss = [k for k in list(E.COMBO_NAMES) + list(E.PB_NAMES) if k not in page]
    if miss: problems.append(f'网页上找不到这些打法的勾选：{miss}')
    for k in kinds:
        post('/api/cfg', {'enabled': [k]}); c = json.load(open(os.path.join(app_dir, 'of_config.json'), encoding='utf-8'))
        if c.get('enabled') != [k]: problems.append(f'勾选 {k} 保存后读回来不对：{c.get("enabled")}')
    post('/api/cfg', {'enabled': []})
    for mode in E.TRAP_MODES:
        post('/api/cfg', {'trap': {'mode': mode}}); c = json.load(open(os.path.join(app_dir, 'of_config.json'), encoding='utf-8'))
        if (c.get('trap') or {}).get('mode') != mode: problems.append(f'多头摊平做空切到 {mode} 保存不对')
    for key, d in (('flush', E.FLUSH), ('squeeze', E.SQUEEZE), ('momo', E.MOMO), ('nfi_dip', E.NFI_DIP), ('vn_dip', E.VN_DIP)):
        for p, v in d.items():
            if isinstance(v, (int, float)):
                nv = v * 1.5 if v else 1
                post('/api/cfg', {key: {p: nv}}); c = json.load(open(os.path.join(app_dir, 'of_config.json'), encoding='utf-8'))
                got = (c.get(key) or {}).get(p)
                if got is None or abs(float(got) - nv) > 1e-9: problems.append(f'参数 {key}.{p} 保存后读回来不对：存 {nv} 读到 {got}')
                post('/api/cfg', {key: {p: v}})
    for i, ln in enumerate(page.split('\n')):
        if re.search(r'id="(nd|vd|fl|sq|mo)_', ln):
            pass
    notes.append(f'全新文件夹启动正常，{len(kinds)} 个打法逐个勾选保存、多头摊平做空 {len(E.TRAP_MODES)} 种做法、五组参数逐个保存都检查了')
srv.terminate()
try:
    out = srv.communicate(timeout=10)[0]
except Exception:
    out = ''
if 'Traceback' in (out or ''):
    problems.append('服务日志里有报错：' + out[out.index('Traceback'):][:500])
shutil.rmtree(tmp, ignore_errors=True)
print('\n'.join(notes))
print('问题：' if problems else '没有发现问题')
for p in problems: print(' -', p)
