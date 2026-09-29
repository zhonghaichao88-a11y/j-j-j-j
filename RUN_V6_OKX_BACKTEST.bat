@echo off
chcp 65001 >nul
cd /d "%~dp0"
python backtest\download_v6.py --days 60 --out backtest\v6_okx_data
if errorlevel 1 goto done
python backtest\v6_replay.py --data backtest\v6_okx_data --out backtest\v6_results_okx
if errorlevel 1 goto done
python backtest\v6_replay.py --data backtest\v6_okx_data --stress 2 --out backtest\v6_results_okx_stress
:done
pause
