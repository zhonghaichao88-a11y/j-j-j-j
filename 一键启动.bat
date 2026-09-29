@echo off
chcp 65001 >nul
title ALPHA-X
cd /d "%~dp0"

echo ========================================
echo   ALPHA-X 一键启动
echo ========================================
echo.

echo [1/3] 检查Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到Python，请先安装Python 3.8+
    pause
    exit /b 1
)
echo [OK] Python已安装

echo.
echo [2/3] 安装依赖（第一次会慢一点）...
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
if errorlevel 1 (
    echo [警告] 镜像源失败，用默认源重试...
    python -m pip install -r requirements.txt
)
echo [OK] 依赖已安装

echo.
echo [3/3] 启动服务...
echo.
echo ========================================
echo   启动成功！
echo   浏览器打开: http://127.0.0.1:8000
echo   按Ctrl+C停止
echo ========================================
echo.

start "" cmd /c "timeout /t 3 /nobreak >nul && start http://127.0.0.1:8000"

python api_server.py

pause
