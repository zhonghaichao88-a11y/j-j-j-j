@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   OKX API Key Reconfiguration
echo ========================================
echo.
echo You will be asked to enter 3 values:
echo   1. API Key
echo   2. Secret Key
echo   3. Passphrase
echo.
echo Get them from OKX App - Profile - API Keys
echo.
echo Right-click to paste, then press Enter.
echo.

python setup.py

echo.
echo ========================================
echo   Done! Configuration saved.
echo ========================================
echo.
echo Now run "start.bat" to start the system.
echo.
pause
