@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "INPUT=%~1"
if not defined INPUT set /p "INPUT=请输入K线压缩包完整路径: "
python -c "import numpy,pandas"
if errorlevel 1 goto dependencies
python backtest\audit_uploaded.py "%INPUT%" backtest\v61_user_data
if errorlevel 1 goto done
python backtest\v6_replay.py --data backtest\v61_user_data --trailing --out backtest\v61_local_results
goto done
:dependencies
echo 缺少Python、numpy或pandas，请使用原ALPHA-X的Python运行环境。
:done
pause
