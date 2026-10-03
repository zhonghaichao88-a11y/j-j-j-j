@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title NFI 实盘（关掉这个窗口 = 停止）
echo ============================================================
echo  NFI X7 实盘   只做多 / 逐仓 3 倍 / 每单最多补 3 次
echo ============================================================
python -c "import sys; assert sys.version_info >= (3,11)" 2>nul
if errorlevel 1 (
  echo 需要 Python 3.11 或更高版本：https://www.python.org/downloads/  安装时勾选 Add python.exe to PATH
  goto fail
)
if not exist ".venv\Scripts\python.exe" (
  echo 第一次运行：创建运行环境...
  python -m venv .venv
  if errorlevel 1 goto fail
)
if not exist ".venv\installed-2026.9.txt" (
  echo 第一次运行：安装 freqtrade，大约 5~10 分钟，请耐心等...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install freqtrade==2026.9
  if errorlevel 1 goto fail
  ".venv\Scripts\freqtrade.exe" install-ui
  echo ok> ".venv\installed-2026.9.txt"
)
if not exist "欧易子账户密钥.txt" (
  echo.
  echo 还没有「欧易子账户密钥.txt」：把「欧易子账户密钥_示例.txt」复制一份改名，填好三行再双击启动。
  goto fail
)
echo.
echo [1/3] 检查欧易账户...
".venv\Scripts\python.exe" check_account.py
if errorlevel 1 goto fail
echo.
echo [2/3] 生成设置、选币...
".venv\Scripts\python.exe" make_config.py
if errorlevel 1 goto fail
echo.
echo [3/3] 启动实盘。浏览器打开上面的网页地址可以看持仓、盈亏。关掉这个窗口就停止。
echo.
".venv\Scripts\python.exe" nfi_run.py
if errorlevel 1 goto fail
exit /b 0
:fail
echo.
echo 没有启动成功，看上面的提示。需要帮忙就截图发我。
pause
exit /b 1
