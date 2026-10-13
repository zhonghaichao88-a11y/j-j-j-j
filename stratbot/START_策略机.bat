@echo off
rem StratBot one-click start. ASCII only on purpose: Chinese text in a .bat can be
rem mis-read by cmd as commands ("is not recognized as an internal or external command").
rem All Chinese messages are printed by start_sb.py instead.
setlocal
cd /d "%~dp0"
title StratBot - keep this window open (closing it stops the bot)
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo.
  echo ============================================================
  echo  Python 3.10 or newer was not found on this computer.
  echo  1. Download it from https://www.python.org/downloads/
  echo  2. During install, tick "Add python.exe to PATH"
  echo  3. Double-click this file again.
  echo ============================================================
  echo.
  pause
  exit /b 1
)
%PY% "%~dp0start_sb.py"
set "RC=%errorlevel%"
if not "%RC%"=="0" (
  echo.
  echo Stopped with error code %RC%. Please take a screenshot of this window.
  pause
)
exit /b %RC%
