@echo off
title ALPHA-X Auto Trader
echo ========================================
echo   ALPHA-X Auto Trader
echo ========================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.8+
    pause
    exit /b 1
)

REM Check dependencies
echo [1/3] Checking dependencies...
python -c "import fastapi, uvicorn, ccxt, pandas, numpy, sklearn" >nul 2>&1
if errorlevel 1 (
    echo [INSTALL] Installing dependencies...
    pip install fastapi uvicorn ccxt pandas numpy scikit-learn python-dotenv aiofiles
)

REM Check .env and API Key
echo [2/3] Checking config...
if not exist .env (
    echo [INFO] .env not found, creating from template...
    copy .env.example .env >nul
)

REM Check if API Key is configured
python -c "import os; from dotenv import load_dotenv; load_dotenv('.env'); exit(0 if os.getenv('OKX_API_KEY') and os.getenv('OKX_API_KEY')!='your_api_key_here' else 1)" >nul 2>&1
if errorlevel 1 (
    echo.
    echo ========================================
    echo   Please enter your OKX API Key
    echo   (Get from OKX App - API - Create API)
    echo ========================================
    echo.
    set /p OKX_API_KEY=API Key: 
    set /p OKX_API_SECRET=API Secret: 
    set /p OKX_API_PASSPHRASE=Passphrase: 
    echo.
    echo [INFO] Saving to .env...
    python -c "import re; c=open('.env',encoding='utf-8').read(); c=re.sub(r'OKX_API_KEY=.*', 'OKX_API_KEY=%OKX_API_KEY%', c); c=re.sub(r'OKX_API_SECRET=.*', 'OKX_API_SECRET=%OKX_API_SECRET%', c); c=re.sub(r'OKX_API_PASSPHRASE=.*', 'OKX_API_PASSPHRASE=%OKX_API_PASSPHRASE%', c); open('.env','w',encoding='utf-8').write(c)"
    echo [OK] API Key saved!
    echo.
)

REM Force AWS node (domestic users can connect directly)
set OKX_BASE_URL=https://www.okx.com
set ALPHA_OKX_BASE_URL=https://www.okx.com

REM Start
echo [3/3] Starting server (AWS node)...
echo.
echo ========================================
echo   Server started!
echo   Node: https://www.okx.com
echo   Open in browser: http://127.0.0.1:8000
echo   Press Ctrl+C to stop
echo ========================================
echo.

REM Auto open browser after 3 seconds
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://127.0.0.1:8000"

python api_server.py

pause
