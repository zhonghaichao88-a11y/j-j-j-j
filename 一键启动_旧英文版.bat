@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   OKX Auto Trading System
echo ========================================
echo.

echo [1/4] Checking Python...
python --version
if %errorlevel% neq 0 (
    echo Python not found. Please install from python.org
    pause
    exit /b 1
)

echo.
echo [2/4] Installing packages (please wait 1-3 min)...
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn

echo.
echo [3/4] Setup...
python setup.py

echo.
echo [4/4] Starting server...
echo Open http://localhost:8000 in your browser
echo Close this window to stop.
echo.

start "" cmd /c "timeout /t 3 /nobreak >nul & start http://localhost:8000"

python api_server.py

pause
