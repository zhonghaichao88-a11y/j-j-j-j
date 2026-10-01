"""启动订单流：和 V7 一样，黑窗口里按提示填 API（只第一次问，存到 .env），服务就绪后自动打开网页。"""
from __future__ import annotations

import getpass
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".env"
PORT = int(os.environ.get("OF_PORT", "8010"))
URL = f"http://127.0.0.1:{PORT}"


def read_env() -> dict:
    out = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def write_env(updates: dict):
    """只改要改的那几行，其它内容（注释、别的设置）原样保留"""
    lines = ENV.read_text(encoding="utf-8-sig").splitlines() if ENV.exists() else []
    done = set()
    for i, line in enumerate(lines):
        k = line.split("=", 1)[0].strip() if "=" in line and not line.strip().startswith("#") else None
        if k in updates:
            lines[i] = f"{k}={updates[k]}"
            done.add(k)
    lines += [f"{k}={v}" for k, v in updates.items() if k not in done]
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ask_keys():
    env = read_env()
    if env.get("OKX_API_KEY"):
        print("已读取 .env 里的欧易 API（要改的话用记事本打开 .env，或者删掉那几行再启动）")
        return
    print("=" * 60)
    print("第一次启动：填欧易 API（只问这一次，保存在本文件夹的 .env 里）")
    print("建议用子账户的 API，只给“交易”权限，不要给“提币”权限。")
    print("直接按回车跳过 = 只用模拟盘，不填也能看盘。")
    print("=" * 60)
    key = input("API Key: ").strip()
    if not key:
        print("已跳过，先用模拟盘。")
        return
    secret = getpass.getpass("Secret Key（输入时不显示，正常）: ").strip()
    pw = getpass.getpass("Passphrase（建 API 时设的密码，输入时不显示）: ").strip()
    upd = {"OKX_API_KEY": key, "OKX_API_SECRET": secret, "OKX_API_PASSPHRASE": pw}
    if not env.get("PROXY_URL"):
        proxy = input("代理地址（和 V7 的 PROXY_URL 一样，例如 http://127.0.0.1:7890；不用代理直接回车）: ").strip()
        if proxy:
            upd["PROXY_URL"] = proxy
    live = input("要不要打开实盘功能？打开后网页上才有“切换到实盘”按钮，切换时还要再确认一次 (y/N): ").strip().lower()
    upd["OF_ALLOW_LIVE"] = "1" if live == "y" else "0"
    write_env(upd)
    print("已保存到 .env")


def wait_ready(proc, timeout=180) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 本机检查不走代理
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            return False
        try:
            with opener.open(URL + "/api/ready", timeout=2) as r:
                if json.load(r).get("ready"):
                    return True
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    return False


def main():
    os.chdir(ROOT)
    if not ENV.exists() and (ROOT / ".env.example").exists():
        shutil.copy(ROOT / ".env.example", ENV)
    ask_keys()
    proc = subprocess.Popen([sys.executable, str(ROOT / "of_app.py")], cwd=ROOT,
                            env=dict(os.environ, OF_NO_BROWSER="1"))
    try:
        print("正在启动，等网页就绪……", flush=True)
        if wait_ready(proc):
            print("已就绪：" + URL, flush=True)
            try:
                if not webbrowser.open(URL, new=2) and os.name == "nt":
                    os.startfile(URL)
            except Exception as e:  # noqa: BLE001
                print(f"浏览器没自动打开（{e}），请手动访问 {URL}")
        elif proc.poll() is not None:
            print("启动失败，看上面的报错，截图发我。")
            return proc.returncode or 1
        else:
            print("还没就绪，等一会儿手动打开 " + URL)
        return proc.wait()
    except KeyboardInterrupt:
        print("正在关闭……")
        if proc.poll() is None:
            proc.terminate()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
