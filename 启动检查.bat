@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo ================================================
echo              ALPHA-X 启动检查
echo ================================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
  set "PY=py"
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (set "PY=python") else (
    echo [红] 未找到 Python 3
    pause
    exit /b 1
  )
)

echo [绿] Python 可用
if exist ".env" (echo [绿] .env 已存在) else (echo [黄] .env 不存在：首次启动会从 .env.example 创建)
if exist "requirements.txt" (echo [绿] requirements.txt 存在) else (echo [红] requirements.txt 缺失)
if exist "api_server.py" (echo [绿] api_server.py 存在) else (echo [红] api_server.py 缺失)
if exist ".env.example" (echo [绿] .env.example 存在) else (echo [红] .env.example 缺失)
if exist ".venv\Scripts\python.exe" (
  echo [绿] 独立虚拟环境已存在
  ".venv\Scripts\python.exe" -c "import fastapi,uvicorn,pandas,numpy,sklearn; print('[绿] 核心 Python 依赖可导入')" 2>nul
  if errorlevel 1 echo [黄] 部分依赖尚未安装，运行“启动 ALPHA-X.bat”自动安装
) else (
  echo [黄] 尚未创建独立虚拟环境
)
echo.
echo 检查完成。不会发送任何交易请求。
pause
