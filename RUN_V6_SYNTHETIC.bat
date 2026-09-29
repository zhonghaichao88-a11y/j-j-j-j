@echo off
chcp 65001 >nul
cd /d "%~dp0"
python backtest\test_v6.py
if errorlevel 1 goto done
python backtest\v6_replay.py --synthetic --days 45 --out backtest\v6_results_synthetic
:done
pause
