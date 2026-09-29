@echo off
cd /d "%~dp0"
set "V62_PY=python"
if exist .venv\Scripts\python.exe set "V62_PY=.venv\Scripts\python.exe"
%V62_PY% backtest\verify_v62.py
if errorlevel 1 goto fail
%V62_PY% -m unittest discover -s backtest -p "test_v6*.py" -v
if errorlevel 1 goto fail
echo PASS
pause
exit /b 0
:fail
echo FAIL
pause
exit /b 1
