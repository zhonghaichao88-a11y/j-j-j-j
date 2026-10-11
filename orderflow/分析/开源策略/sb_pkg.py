"""策略机打包 + 每个细节检查（每次发包都跑这个，不手工挑文件）：
1. 打包：代码库里 stratbot/ 下的全部程序文件（不含你的设置、密钥、日志、状态、缓存），放在"策略机/"文件夹里
2. 包里每个文件和代码库现版本逐字节比；不该带的（.env、sb_config.json、sb_state、sb_trades、sb_log、缓存）一个都不能有；文本扫一遍像密钥的东西
3. 代码库里的程序文件一个不能少
4. 全新文件夹：解压，用全新的 Python 虚拟环境按 requirements.txt 装依赖（和你电脑上第一次运行一样），
   启动程序（不填密钥），等它连上欧易、下完K线、算出信号；sb_config.json 自动生成
5. 真浏览器把网页上每个按钮、每个框点一遍，核对存下来的设置（sb_ui_check.py）
用法：python sb_pkg.py [--no-install]（--no-install = 用已有的环境，跳过第 4 步装依赖）"""
import os, sys, re, json, zipfile, subprocess, tempfile, time, shutil, urllib.request
REPO = '/home/user/j-j-j-j'; SB = f'{REPO}/stratbot'
ZIP = f'{REPO}/策略机_完整包.zip'
BAD = re.compile(r'(^|/)(\.env$|sb_config\.json|sb_state|sb_trades|sb_log|__pycache__|\.venv|.*密钥)')
HERE = os.path.dirname(os.path.abspath(__file__))
problems, notes = [], []
git = lambda *a: subprocess.run(['git', '-c', 'core.quotepath=off', *a], cwd=REPO, capture_output=True, text=True, check=True).stdout
prog = sorted(f[len('stratbot/'):] for f in git('ls-files', 'stratbot').split('\n') if f and not BAD.search(f[len('stratbot/'):]))
dirty = [l for l in git('status', '--porcelain', 'stratbot').split('\n') if l.strip()]
if dirty:
    problems.append(f'stratbot 里有没提交的改动（先提交再打包）：{dirty}')

# 1. 打包
tmpz = ZIP + '.tmp'
with zipfile.ZipFile(tmpz, 'w', zipfile.ZIP_DEFLATED) as z:
    z.writestr(zipfile.ZipInfo('策略机/', time.localtime()[:6]), b'')
    for f in prog:
        z.write(os.path.join(SB, f), '策略机/' + f)
os.replace(tmpz, ZIP)
notes.append(f'完整包 {len(prog)} 个文件：{prog}')

# 2. 3. 逐字节、不该带的、密钥、齐全
z = zipfile.ZipFile(ZIP)
got = {}
for it in z.infolist():
    if it.filename.endswith('/'):
        continue
    if not it.flag_bits & 0x800:
        problems.append(f'{it.filename} 文件名编码不是 UTF-8（Windows 解压会乱码）')
    rel = it.filename[len('策略机/'):]
    got[rel] = z.read(it.filename)
for rel, data in got.items():
    if BAD.search(rel):
        problems.append(f'带了不该带的：{rel}')
    if open(os.path.join(SB, rel), 'rb').read() != data:
        problems.append(f'{rel} 和代码库不一样')
    if rel.endswith(('.py', '.md', '.json', '.html', '.bat', '.txt', '.example')):
        t = data.decode('utf-8', 'ignore')
        for m in re.finditer(r'(?i)(api[_-]?key|secret|passphrase)\s*[=:]\s*["\']?([A-Za-z0-9+/\-]{16,})', t):
            problems.append(f'{rel} 里像密钥：{m.group(0)[:40]}…')
missing = set(prog) - set(got)
if missing:
    problems.append(f'少了：{sorted(missing)}')
for must in ('START_策略机.bat', 'launch_sb.py', 'sb_app.py', 'sb_bot.py', 'sb_engine.py', 'sb_loader.py', 'sb_data.py', 'sb_live.py',
             'sb_notify.py', 'static/index.html', 'requirements.txt', '.env.example', 'sb_config.default.json', 'sb_backtest.json',
             'strategies/ElliotV8_original_ichiv3.py', '策略机_说明.md'):
    if must not in got:
        problems.append(f'缺关键文件：{must}')
bat = got.get('START_策略机.bat', b'')
if b'\r\n' not in bat:
    problems.append('START_策略机.bat 不是 Windows 换行（CRLF）')

# 4. 全新文件夹 + 全新环境
work = tempfile.mkdtemp(prefix='sbpkg_', dir='/tmp/claude-0/-home-user-j-j-j-j/e4ba4643-83ef-564d-bada-b9eebb5039c7/scratchpad')
z.extractall(work)
app = os.path.join(work, '策略机')
if '--no-install' in sys.argv:
    py = '/home/user/ext/ftvenv/bin/python'
else:
    subprocess.run([sys.executable if sys.executable.endswith('python3') else 'python3', '-m', 'venv', os.path.join(app, '.venv')], check=True)
    py = os.path.join(app, '.venv', 'bin', 'python')
    r = subprocess.run([py, '-m', 'pip', 'install', '--disable-pip-version-check', '-q', '-r', 'requirements.txt'], cwd=app,
                       capture_output=True, text=True)
    if r.returncode:
        problems.append('按 requirements.txt 装依赖失败：' + (r.stderr or r.stdout)[-800:])
    else:
        notes.append('全新环境按 requirements.txt 装依赖：成功')
r = subprocess.run([py, '-c', 'import sb_app, sb_bot, sb_engine, sb_loader, sb_data, sb_live, sb_notify, launch_sb; '
                    'import sb_loader as L; s=L.Strategy("ElliotV8_original_ichiv3"); assert not s.unsupported; print("ok")'],
                   cwd=app, capture_output=True, text=True)
if 'ok' not in r.stdout:
    problems.append('全新文件夹导入模块失败：' + r.stderr[-800:])
shutil.copy(os.path.join(app, '.env.example'), os.path.join(app, '.env'))      # 和 bat 一样：第一次复制 .env
port = 8031
proc = subprocess.Popen([py, 'sb_app.py'], cwd=app, env=dict(os.environ, SB_PORT=str(port)), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
try:
    st, t0 = None, time.time()
    while time.time() - t0 < 600:
        try:
            st = json.load(urllib.request.build_opener(urllib.request.ProxyHandler({})).open(f'http://127.0.0.1:{port}/api/status', timeout=3))
            if st['ready'] and st['last_candle']:
                break
        except Exception:
            pass
        if proc.poll() is not None:
            break
        time.sleep(3)
    if not st or not st.get('ready'):
        problems.append(f'全新文件夹启动后 10 分钟没就绪：{st and st.get("progress")}')
    else:
        notes.append(f'全新文件夹启动：{round(time.time() - t0)} 秒就绪，{st["coins"]} 个币，{st["logs"][:1]}')
        if not os.path.exists(os.path.join(app, 'sb_config.json')):
            problems.append('没自动生成 sb_config.json')
        if any(s.get('error') for s in st['strategies']):
            problems.append(f'策略加载出错：{st["strategies"]}')
        if st['mode'] != 'paper':
            problems.append('全新启动不是模拟盘')
        # 5. 真浏览器
        shot = os.path.join(HERE, '策略机_网页截图.png')
        r = subprocess.run(['python3', os.path.join(HERE, 'sb_ui_check.py'), f'http://127.0.0.1:{port}/', app, shot],
                           capture_output=True, text=True, timeout=300)
        try:
            u = json.loads(r.stdout.strip().split('\n')[-1])
            problems += ['网页：' + x for x in u['bad']]
            problems += ['网页报错：' + x for x in u['errs']]
            notes += u['notes']
            notes.append('网页逐项操作：' + ('全部通过' if not u['bad'] and not u['errs'] else '有问题'))
        except Exception:
            problems.append('网页检查没跑起来：' + (r.stderr or r.stdout)[-800:])
finally:
    proc.terminate()
    try:
        out = proc.communicate(timeout=10)[0].decode('utf-8', 'ignore')
    except Exception:
        out = ''
    tb = [l for l in out.split('\n') if 'Traceback' in l or 'ERROR' in l]
    if tb:
        problems.append('程序运行时有报错：' + ' | '.join(tb[:5]))
print('\n'.join(['== 说明'] + notes + ['== 问题（' + str(len(problems)) + '）'] + problems))
json.dump({'problems': problems, 'notes': notes, 'zip': ZIP, 'n_files': len(prog)}, open(os.path.join(HERE, 'sb_pkg_result.json'), 'w'),
          ensure_ascii=False, indent=1)
if '--keep' not in sys.argv:
    shutil.rmtree(work, ignore_errors=True)
