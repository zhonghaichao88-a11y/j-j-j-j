@echo off
cd /d "%~dp0"
set "V62_PY=python"
if exist .venv\Scripts\python.exe set "V62_PY=.venv\Scripts\python.exe"
%V62_PY% backtest\run_v62.py --data data_v62 --out backtest\v62_rerun --workers 4
if errorlevel 1 goto fail
%V62_PY% backtest\run_v62.py --data data_v62 --out backtest\v62_rerun_stress --workers 4 --stress 2
if errorlevel 1 goto fail
echo Replay complete. Results are in backtest/v62_rerun and backtest/v62_rerun_stress.
pause
exit /b 0
:fail
echo Replay failed. Read the error above.
pause
exit /b 1
