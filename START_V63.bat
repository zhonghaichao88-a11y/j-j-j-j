@echo off
setlocal
cd /d "%~dp0"
python -c "import sys; assert sys.version_info >= (3,10), 'Python 3.10+ required'"
if errorlevel 1 goto fail
if not exist .venv\Scripts\python.exe python -m venv .venv
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto fail
if not exist .env copy .env.example .env >nul
echo V6.3 research build. Open http://127.0.0.1:8000/ after startup.
.venv\Scripts\python.exe api_server.py
if errorlevel 1 goto fail
exit /b 0
:fail
echo Startup failed. Read the error above.
pause
exit /b 1
