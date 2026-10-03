@echo off
setlocal
cd /d "%~dp0"
title NFI live (close this window to stop)
python --version >nul 2>&1
if errorlevel 1 (
  echo.
  echo Python not found. Install Python 3.11+ from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during install, then double-click again.
  echo.
  pause
  exit /b 1
)
python "%~dp0launcher.py"
echo.
pause
