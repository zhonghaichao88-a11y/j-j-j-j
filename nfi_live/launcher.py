"""双击 START_NFI.bat 后真正干活的地方（.bat 里只放英文，避免 Windows 黑窗口把中文当成命令报"不是内部或外部命令"）。
1 准备运行环境（第一次装 freqtrade）→ 2 检查欧易账户 → 3 生成设置和选币 → 4 启动实盘看门程序（会自动打开网页）"""
import os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
WIN = os.name == "nt"
VPY = os.path.join(HERE, ".venv", "Scripts" if WIN else "bin", "python.exe" if WIN else "python")
VFT = os.path.join(HERE, ".venv", "Scripts" if WIN else "bin", "freqtrade.exe" if WIN else "freqtrade")
MARK = os.path.join(HERE, ".venv", "installed-2026.9.txt")


def step(msg):
    print("\n" + msg, flush=True)


def run(cmd):
    return subprocess.call(cmd, cwd=HERE)


def main():
    print("=" * 60)
    print(" NFI X7 实盘   只做多 / 逐仓 3 倍 / 每单最多补 3 次")
    print(" 关掉这个窗口 = 停止（已开的仓位留在交易所，下次启动接着管）")
    print("=" * 60)
    if sys.version_info < (3, 11):
        print(f"需要 Python 3.11 或更高版本，现在是 {sys.version.split()[0]}。到 https://www.python.org/downloads/ 装新版，安装时勾选 Add python.exe to PATH")
        return 1
    if not os.path.exists(VPY):
        step("第一次运行：创建运行环境…")
        if run([sys.executable, "-m", "venv", ".venv"]):
            print("创建运行环境失败")
            return 1
    if not os.path.exists(MARK):
        step("第一次运行：安装 freqtrade，大约 5~10 分钟，下面会滚动显示进度，别关窗口…")
        run([VPY, "-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "pip"])
        if run([VPY, "-m", "pip", "install", "--disable-pip-version-check", "freqtrade==2026.9"]):
            print("安装 freqtrade 失败（多半是网络问题），再双击一次重试；还不行就截图发我")
            return 1
        if run([VFT, "install-ui"]):
            print("⚠ freqtrade 自带的英文网页没装上（不影响交易，中文状态网页照常能用），下次启动会再试")
        else:
            open(MARK, "w").write("ok\n")
    if not os.path.exists(os.path.join(HERE, "欧易子账户密钥.txt")):
        print("\n还没有「欧易子账户密钥.txt」：把「欧易子账户密钥_示例.txt」复制一份改名，填好三行再双击启动。")
        return 1
    step("[1/3] 检查欧易账户…")
    if run([VPY, "check_account.py"]):
        return 1
    step("[2/3] 生成设置、选币…")
    if run([VPY, "make_config.py"]):
        return 1
    step("[3/3] 启动实盘，会自动打开中文状态网页 http://127.0.0.1:8090")
    try:
        return run([VPY, "nfi_run.py"])
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    try:
        rc = main()
    except KeyboardInterrupt:
        rc = 0
    except Exception as e:  # noqa: BLE001
        print(f"出错：{e}")
        rc = 1
    if rc:
        print("\n没有启动成功，看上面的提示。需要帮忙就截图发我。")
    sys.exit(rc)
