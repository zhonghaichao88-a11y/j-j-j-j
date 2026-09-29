@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ================================================
echo              ALPHA-X 一键启动器
echo ================================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
  set "PY=py"
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (set "PY=python") else (
    echo [错误] 未找到 Python 3。
    echo 请先安装 Python 3.10+，然后重新启动。
    pause
    exit /b 1
  )
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/5] 正在创建独立 Python 环境...
  %PY% -m venv .venv
  if errorlevel 1 goto :fail
)

set "VPY=.venv\Scripts\python.exe"

echo [2/5] 正在检查/安装依赖...
%VPY% -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :fail

echo [3/5] 正在检查配置文件...
if not exist ".env" copy /Y ".env.example" ".env" >nul

if not exist "api_server.py" (
  echo [错误] 找不到 api_server.py。
  goto :fail
)

echo [4/5] 正在启动 ALPHA-X 服务...
start "ALPHA-X Server" /min cmd /c "cd /d ""%~dp0"" && ""%VPY%"" api_server.py"

set "HEALTH_OK=0"
for /l %%i in (1,1,30) do (
  timeout /t 1 /nobreak >nul
  powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2; if($r.StatusCode -eq 200){exit 0}else{exit 1} } catch { exit 1 }" >nul 2>nul
  if not errorlevel 1 (
    set "HEALTH_OK=1"
    goto :health_done
  )
)

:health_done
if "%HEALTH_OK%"=="1" (
  echo [5/5] ALPHA-X API 已正常启动。
  echo.
  echo 正在打开中文 WWW 控制台...
  start "" "http://127.0.0.1:8000/"
  echo.
  echo ================================================
  echo   ALPHA-X 已启动： http://127.0.0.1:8000/
  echo   交易服务不会因为关闭本窗口而停止。
  echo ================================================
  echo.
  pause
  exit /b 0
) else (
  echo [错误] ALPHA-X API 在规定时间内没有启动成功。
  echo 请运行“启动检查.bat”查看环境问题。
  goto :fail
)

:fail
echo.
echo [启动失败] 请检查上面的错误信息。
pause
exit /b 1
