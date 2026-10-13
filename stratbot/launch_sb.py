"""启动策略机：黑窗口里按提示填 API（只第一次问，存到 .env），服务就绪后自动打开网页。"""
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
PORT = int(os.environ.get("SB_PORT", "8020"))
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


def ask_proxy():
    env = read_env()
    if env.get("PROXY_URL") or env.get("SB_PROXY_ASKED") == "1":
        return
    print("=" * 60)
    proxy = input("代理地址（和订单流 / V7 的 PROXY_URL 一样，例如 http://127.0.0.1:7890；不用代理直接回车）: ").strip()
    write_env({"PROXY_URL": proxy, "SB_PROXY_ASKED": "1"})


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
    live = input("要不要打开实盘功能？打开后网页上“切换到实盘”才能用，切换时还要再确认一次 (y/N): ").strip().lower()
    upd["SB_ALLOW_LIVE"] = "1" if live == "y" else "0"
    write_env(upd)
    print("已保存到 .env")


def wait_ready(proc) -> bool:
    """等网页就绪；期间把进度（下载到第几个币）打在黑窗口里，不让人以为卡死了。程序还活着就一直等"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 本机检查不走代理
    t0, last, warned, shown, t_shown = time.monotonic(), None, False, -1, 0.0
    while proc.poll() is None:
        try:
            with opener.open(URL + "/api/status", timeout=3) as r:
                st = json.load(r)
            if st.get("ready"):
                return True
            pg = st.get("progress") or {}
            stage = pg.get("stage", "")
            if stage and stage != last:                              # 换了一步：整句打一行
                print(time.strftime("%H:%M:%S ") + stage, flush=True)
                last, shown, t_shown = stage, -1, 0.0
            if pg.get("total") and pg.get("done", 0) != shown and time.monotonic() - t_shown >= 5:   # 进度：最多 5 秒一行
                print(f"         已完成 {pg.get('done', 0)}/{pg['total']}", flush=True)
                shown, t_shown = pg.get("done", 0), time.monotonic()
        except (OSError, ValueError):
            pass
        if not warned and time.monotonic() - t0 > 900:
            print("已经等了 15 分钟还没好：多半是连不上欧易（检查 .env 里的 PROXY_URL / 代理软件是否开着）。程序还在重试。", flush=True)
            warned = True
        time.sleep(1)
    return False


def already_running() -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(URL + "/api/status", timeout=2) as r:
            return "ready" in json.load(r)
    except (OSError, ValueError):
        return False


def main():
    os.chdir(ROOT)
    if not ENV.exists() and (ROOT / ".env.example").exists():
        shutil.copy(ROOT / ".env.example", ENV)
    if already_running():
        print("策略机已经在运行了（可能开过一个黑窗口），直接打开网页：" + URL, flush=True)
        webbrowser.open(URL, new=2)
        time.sleep(8)
        return 0
    ask_proxy()
    ask_keys()
    proc = subprocess.Popen([sys.executable, str(ROOT / "sb_app.py")], cwd=ROOT)
    try:
        print("正在启动，第一次要下载各个币的历史K线，大约 2~3 分钟，下面会显示进度……", flush=True)
        if wait_ready(proc):
            print("=" * 60, flush=True)
            print("已就绪，网页地址：" + URL + "（浏览器没自动打开就手动复制这个地址）", flush=True)
            print("这个黑窗口别关：关了程序就停了。下面每 5 分钟显示一次运行状态。", flush=True)
            print("=" * 60, flush=True)
            try:
                if not webbrowser.open(URL, new=2) and os.name == "nt":
                    os.startfile(URL)
            except Exception as e:  # noqa: BLE001
                print(f"浏览器没自动打开（{e}），请手动访问 {URL}")
        else:
            print("启动失败，看上面的报错，截图发我。", flush=True)
            return proc.returncode or 1
        rc = proc.wait()
        if rc:
            print(f"程序停了（错误码 {rc}），看上面的报错，截图发我。", flush=True)
        return rc
    except KeyboardInterrupt:
        print("正在关闭……")
        if proc.poll() is None:
            proc.terminate()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
