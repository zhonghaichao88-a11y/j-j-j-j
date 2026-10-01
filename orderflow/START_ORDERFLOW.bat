@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
python -c "import sys; assert sys.version_info >= (3,10), 'Python 3.10+ required'"
if errorlevel 1 goto fail
if not exist .venv\Scripts\python.exe python -m venv .venv
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m pip install -q -r requirements.txt
if errorlevel 1 goto fail
if not exist .env copy .env.example .env >nul
echo 订单流看盘：浏览器打开 http://127.0.0.1:8010   （默认模拟盘，不会动你的钱）
start "" http://127.0.0.1:8010
.venv\Scripts\python.exe of_app.py
if errorlevel 1 goto fail
exit /b 0
:fail
echo 启动失败，看上面的报错，截图发我。
pause
exit /b 1
