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
.venv\Scripts\python.exe launch_of.py
if errorlevel 1 goto fail
exit /b 0
:fail
echo 启动失败，看上面的报错，截图发我。
pause
exit /b 1
