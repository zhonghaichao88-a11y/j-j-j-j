"""
首次配置向导
检测 .env 是否配置, 未配置则交互式引导用户输入 API 凭证
"""
import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import os
import re
import sys


def is_configured() -> bool:
    """检查 .env 是否已配置真实凭证"""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return False
    with open(env_path, "r", encoding="utf-8") as f:
        content = f.read()
    placeholders = ["your_api_key_here", "your_api_secret_here", "your_passphrase_here"]
    return not any(p in content for p in placeholders)


def run_setup():
    """运行配置向导"""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    example_path = os.path.join(os.path.dirname(__file__), ".env.example")

    # 如果 .env 不存在, 从模板复制；模板不存在则创建默认配置
    if not os.path.exists(env_path):
        if os.path.exists(example_path):
            import shutil
            shutil.copy(example_path, env_path)
        else:
            # .env.example 不存在时，自动创建默认 .env
            with open(env_path, 'w', encoding='utf-8') as f:
                f.write("""# OKX API 配置
OKX_API_KEY=
OKX_API_SECRET=
OKX_API_PASSPHRASE=
OKX_DEMO_MODE=1

# 交易配置
TRADING_SYMBOL=BTC-USDT
TRADING_TYPE=swap
DEFAULT_LEVERAGE=3

# ALPHA-X 配置
ALPHA_LIVE_ALLOWED=0
ALPHA_LIVE_API_KEY=888888

# 风控
DRY_RUN=true
RISK_PCT=0.003
MAX_POSITIONS=3
""")
            print("[setup] .env.example 未找到，已自动创建默认 .env")

    print("=" * 50)
    print("  首次使用 - 欧易 API 凭证配置")
    print("=" * 50)
    print()
    print("请在欧易 App -> 个人中心 -> API 与连接凭证")
    print("创建 API Key (权限勾: 只读 + 交易, 不要勾提现)")
    print()
    print("创建后你会得到三个值, 依次粘贴到下面:")
    print("  (粘贴后按回车, 输入时不会显示星号, 直接粘贴即可)")
    print()

    api_key = ""
    while not api_key:
        api_key = input("  1. API Key: ").strip()
        if not api_key:
            print("    [提示] 不能为空, 请重新输入")

    secret = ""
    while not secret:
        secret = input("  2. Secret Key: ").strip()
        if not secret:
            print("    [提示] 不能为空, 请重新输入")

    passphrase = ""
    while not passphrase:
        passphrase = input("  3. 密码短语(Passphrase): ").strip()
        if not passphrase:
            print("    [提示] 不能为空, 请重新输入")

    # 写入 .env
    with open(env_path, "r", encoding="utf-8") as f:
        content = f.read()

    content = re.sub(r"OKX_API_KEY=.*", f"OKX_API_KEY={api_key}", content)
    content = re.sub(r"OKX_API_SECRET=.*", f"OKX_API_SECRET={secret}", content)
    content = re.sub(r"OKX_API_PASSPHRASE=.*", f"OKX_API_PASSPHRASE={passphrase}", content)

    with open(env_path, "w", encoding="utf-8") as f:
        f.write(content)

    print()
    print("[OK] 配置已保存到 .env")
    print()
    print("当前配置:")
    print(f"  API Key:  {api_key[:8]}...{api_key[-4:] if len(api_key)>12 else ''}")
    print(f"  Secret:   {secret[:8]}...{secret[-4:] if len(secret)>12 else ''}")
    print(f"  密码短语: {'*' * len(passphrase)}")
    print()


def force_real_mode():
    """强制实盘模式 + 真实下单"""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        content = f.read()
    changed = False
    if "OKX_DEMO_MODE=1" in content:
        content = content.replace("OKX_DEMO_MODE=1", "OKX_DEMO_MODE=0")
        changed = True
    if "DRY_RUN=true" in content:
        content = content.replace("DRY_RUN=true", "DRY_RUN=false")
        changed = True
    if changed:
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(content)
        print("[OK] 已强制切换为实盘真实交易模式")


if __name__ == "__main__":
    if is_configured():
        print("[OK] API 凭证已配置, 跳过配置向导")
    else:
        run_setup()
    force_real_mode()
