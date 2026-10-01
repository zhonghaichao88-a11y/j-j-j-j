@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
python -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11+ required'"
if errorlevel 1 goto fail
if not exist .venv\Scripts\python.exe python -m venv .venv
if errorlevel 1 goto fail
if not exist .venv\installed.txt (
  .venv\Scripts\python.exe -m pip install --upgrade pip
  .venv\Scripts\python.exe -m pip install freqtrade==2026.9
  if errorlevel 1 goto fail
  .venv\Scripts\freqtrade.exe install-ui
  echo ok> .venv\installed.txt
)
.venv\Scripts\python.exe make_config.py
if errorlevel 1 goto fail
echo 模拟盘启动中（不会下真单）。浏览器打开上面的网页地址看持仓。关掉这个窗口就停止。
.venv\Scripts\freqtrade.exe trade --userdir user_data --config user_data\config.json --strategy NostalgiaForInfinityX7
if errorlevel 1 goto fail
exit /b 0
:fail
echo 启动失败，看上面的报错，截图发我。
pause
exit /b 1
