"""打包（每次都用这个，不手工挑文件）：
完整包 = 代码库里程序要用的全部文件（不含研究数据、你的设置、密钥、日志、缓存），放在"订单流/"文件夹里；
覆盖包 = 同样这些程序文件，不带外层文件夹（不管装的是哪个旧版本，解压覆盖到程序文件夹后都是最新的；包里没有 .env 和 of_config.json，你的密钥和设置不动）。"""
import os, re, subprocess, zipfile, time
REPO = '/home/user/j-j-j-j'; OF = f'{REPO}/orderflow'
BAD = re.compile(r'(^|/)(\.env$|of_state|of_trades|of_log|of_config\.json$|分析/|回测结果/|__pycache__|\.venv|fp_data|data_rec|发给Claude|.*密钥)')
git = lambda *a: subprocess.run(['git', '-c', 'core.quotepath=off', *a], cwd=REPO, capture_output=True, text=True, check=True).stdout
prog = sorted(f[len('orderflow/'):] for f in git('ls-files', 'orderflow').split('\n') if f and not BAD.search(f[len('orderflow/'):]))
over = list(prog)


def write(zp, files, prefix):
    tmp = zp + '.tmp'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        if prefix:
            z.writestr(zipfile.ZipInfo(prefix, time.localtime()[:6]), b'')
        for f in files:
            z.write(os.path.join(OF, f), prefix + f)
    os.replace(tmp, zp)


write(f'{REPO}/订单流_完整包.zip', prog, '订单流/')
write(f'{REPO}/订单流_覆盖文件.zip', over, '')
print('完整包', len(prog), '个文件；覆盖包', len(over), '个：', over)
