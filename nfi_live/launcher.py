"""双击 START_NFI.bat 后真正干活的地方（.bat 里只放英文，避免 Windows 黑窗口把中文当成命令报"不是内部或外部命令"）。
1 准备运行环境（第一次装 freqtrade）→ 2 检查欧易账户 → 3 生成设置和选币 → 4 启动实盘看门程序（会自动打开网页）"""
import getpass, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
WIN = os.name == "nt"
VPY = os.path.join(HERE, ".venv", "Scripts" if WIN else "bin", "python.exe" if WIN else "python")
VFT = os.path.join(HERE, ".venv", "Scripts" if WIN else "bin", "freqtrade.exe" if WIN else "freqtrade")
MARK = os.path.join(HERE, ".venv", "installed-2026.9.txt")


def step(msg):
    print("\n" + msg, flush=True)


def run(cmd):
    return subprocess.call(cmd, cwd=HERE)


def ask_keys():
    """第一次启动：直接在黑窗口里粘贴欧易 API（右键或 Ctrl+V 粘贴），存成「欧易子账户密钥.txt」，以后不用再填"""
    print("\n第一次启动，要填欧易「子账户」的 API（在欧易 App / 网页 → 子账户 → API 管理 里创建）。")
    print("创建时权限只勾「读取」和「交易」，千万不要勾「提现」。")
    print("下面每一项粘贴后按回车（在黑窗口里点右键或按 Ctrl+V 就是粘贴）。直接回车不填就退出。")
    print("填完别截图发给别人：Secret Key 和密码泄露了，别人就能用你的账户下单。\n")
    vals = {}
    for key, name in (("API_KEY", "API Key"), ("SECRET_KEY", "Secret Key（密钥）"), ("PASSPHRASE", "Passphrase（创建 API 时自己设的密码）")):
        if key == "API_KEY":
            v = input(f"{name}：")
        else:                                   # 密钥和密码输入时不显示在屏幕上，免得截图泄露（粘贴后看不到字是正常的）
            v = getpass.getpass(f"{name}（粘贴后屏幕上不显示，直接回车）：")
        v = v.strip().strip('"').strip("'")
        if key != "PASSPHRASE":
            v = v.replace(" ", "")              # Key / Secret 里不会有空格，复制时多带的去掉；密码原样保留
        if not v:
            print("没有填，已退出。下次双击启动再填。")
            return False
        vals[key] = v
    if len(vals["API_KEY"]) < 20 or len(vals["SECRET_KEY"]) < 20:
        print("API Key 或 Secret Key 太短了，可能没复制全。已退出，下次双击启动再填。")
        return False
    with open(os.path.join(HERE, "欧易子账户密钥.txt"), "w", encoding="utf-8") as f:
        f.write("# 欧易子账户 API（启动时在黑窗口里填的）。要换 API 就删掉这个文件，下次启动会重新问\n")
        for k, v in vals.items():
            f.write(f"{k}={v}\n")
    print("\n已保存到「欧易子账户密钥.txt」（只在你电脑上）。下面马上用它连欧易检查一下对不对。")
    return True


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
    sys.path.insert(0, HERE)
    from common import key_file
    if not key_file() and not ask_keys():
        return 1
    step("[1/3] 检查欧易账户…")
    if run([VPY, "check_account.py"]):
        return 1
    step("[2/3] 生成设置、选币…")
    if run([VPY, "make_config.py"]):
        return 1
    step("[3/3] 启动实盘，会自动打开中文状态网页 http://127.0.0.1:8090")
    print("要停止：在这个窗口按 Ctrl+C，等出现「已停止」再关窗口（这样会先撤掉没成交的挂单）")
    p = subprocess.Popen([VPY, "nfi_run.py"], cwd=HERE)
    while True:                                 # 按 Ctrl+C 时等看门程序把撤单、停机做完，不抢先退出
        try:
            return p.wait()
        except KeyboardInterrupt:
            continue


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
