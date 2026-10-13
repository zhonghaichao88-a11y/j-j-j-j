"""一键启动的第二步（START_策略机.bat 找到 Python 后调用它）：
建运行环境 → 装依赖（requirements.txt 没变就跳过）→ 交给 launch_sb.py 填 API、启动、打开网页。
中文提示都在这里打印：.bat 里只放英文，免得 cmd 把中文当成命令（"不是内部或外部命令"）。"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
VPY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
STAMP = VENV / "sb_requirements.sha1"          # 上次装好的 requirements.txt 指纹：没变就不再装，启动快
NEED = ("fastapi", "uvicorn", "httpx", "websockets", "ccxt", "pandas", "numpy", "talib", "technical", "pandas_ta")


def say(msg=""):
    print(msg, flush=True)


def console_setup():
    """Windows 黑窗口：关掉"快速编辑"（鼠标点一下窗口程序就会卡住不动，要按回车才继续），标题写清楚"""
    if os.name != "nt":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-10)                  # 输入句柄
        mode = ctypes.c_uint32()
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            k.SetConsoleMode(h, (mode.value & ~0x0040) | 0x0080)    # 去掉 QUICK_EDIT，保留 EXTENDED_FLAGS
        k.SetConsoleTitleW("策略机 —— 运行中请别关这个窗口（关了程序就停了）")
    except Exception:  # noqa: BLE001
        pass


def run(cmd) -> int:
    return subprocess.call([str(c) for c in cmd], cwd=ROOT)


def deps_ok() -> bool:
    code = "import importlib, sys; [importlib.import_module(m) for m in %r]" % (NEED,)
    return subprocess.call([str(VPY), "-c", code], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0


def main() -> int:
    os.chdir(ROOT)
    console_setup()
    say("=" * 60)
    say("  策略机 一键启动")
    say(f"  文件夹：{ROOT}")
    say("=" * 60)
    say(f"[1/4] Python {sys.version.split()[0]}：可以用")

    if not VPY.exists():
        say("[2/4] 第一次运行：创建运行环境（.venv），大约 1 分钟 ...")
        if run([sys.executable, "-m", "venv", VENV]) or not VPY.exists():
            say("创建运行环境失败。常见原因：文件夹在网盘/只读位置，或杀毒软件拦截。把整个文件夹放到 D:\\策略机 这类普通位置再试。")
            return 1
    else:
        say("[2/4] 运行环境已有，跳过")

    req = (ROOT / "requirements.txt").read_bytes()
    want = hashlib.sha1(req).hexdigest()
    have = STAMP.read_text().strip() if STAMP.exists() else ""
    if have == want and deps_ok():
        say("[3/4] 依赖已装好，跳过")
    else:
        say("[3/4] 安装依赖（第一次要几分钟，下面会滚动显示进度，别关窗口）...")
        if run([VPY, "-m", "pip", "install", "--disable-pip-version-check", "-r", "requirements.txt"]):
            say("")
            say("依赖没装上。常见原因：网络连不上 pypi（开代理后再双击一次），或 Python 版本太新还没有对应的安装包（建议用 Python 3.11 / 3.12）。")
            return 1
        if not deps_ok():
            say("依赖装完了但导入失败，把上面的报错截图发我。")
            return 1
        STAMP.write_text(want)
        say("依赖装好了")

    env = ROOT / ".env"
    if not env.exists() and (ROOT / ".env.example").exists():
        env.write_bytes((ROOT / ".env.example").read_bytes())
    say("[4/4] 启动策略机，准备好后会自动打开网页 ...")
    say("")
    try:
        return run([VPY, ROOT / "launch_sb.py"])
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
