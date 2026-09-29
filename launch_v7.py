"""启动服务，确认本次服务就绪后自动打开 V7 画图页面。"""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import uuid
import webbrowser


def wait_ready(process, url, launch_id, timeout=180, opener=None):
    # 本机就绪检查不经过系统代理，且必须匹配本次启动的服务。
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with opener.open(url + '/api/ready', timeout=2) as response:
                payload = json.load(response)
            if payload.get('launch_id') == launch_id and payload.get('ready') is True:
                return True
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    return False


def main():
    root = Path(__file__).resolve().parent
    os.chdir(root)
    from config import config
    port = int(config.api.port)
    host = str(config.api.host)
    if host in ('0.0.0.0', '::', ''):
        host = '127.0.0.1'
    if ':' in host and not host.startswith('['):
        host = '[' + host + ']'
    url = f'http://{host}:{port}/tv'
    launch_id = uuid.uuid4().hex
    env = dict(os.environ, ALPHA_V7_LAUNCH_ID=launch_id)
    process = subprocess.Popen([sys.executable, str(root / 'api_server.py')], cwd=root, env=env)
    try:
        print('正在等待画图页面就绪……', flush=True)
        if wait_ready(process, url, launch_id):
            print('画图页面已就绪：' + url, flush=True)
            try:
                opened = webbrowser.open(url, new=2)
                if not opened and os.name == 'nt':
                    os.startfile(url)
                elif not opened:
                    print('浏览器未自动打开，请手动访问：' + url, flush=True)
            except Exception as exc:
                print(f'无法自动打开浏览器：{exc}；请访问 {url}', flush=True)
        elif process.poll() is not None:
            print('服务启动失败，请查看上方错误；未打开无效页面。', flush=True)
            return process.returncode or 1
        else:
            print('服务仍未就绪，请查看黑窗口。就绪后可手动访问：' + url, flush=True)
        return process.wait()
    except KeyboardInterrupt:
        print('正在关闭网页服务……', flush=True)
        if process.poll() is None:
            process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
