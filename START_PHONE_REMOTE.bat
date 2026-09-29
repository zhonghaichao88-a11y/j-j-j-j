@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title ALPHA-X 手机远程访问

echo ==================================================
echo          ALPHA-X 手机远程访问 - 稳定启动版
echo ==================================================
echo.
echo 说明：这个窗口不会一闪而过。任何错误都会停在这里。
echo.

REM ---------- 1. 基础文件 ----------
if not exist ".env" (
  if exist ".env.example" (
    copy /Y ".env.example" ".env" >nul
  ) else (
    echo [错误] 找不到 .env.example
    goto :fail
  )
)
if not exist "api_server.py" (echo [错误] 找不到 api_server.py&goto :fail)
if not exist "requirements.txt" (echo [错误] 找不到 requirements.txt&goto :fail)

REM ---------- 2. Python 环境 ----------
if not exist ".venv\Scripts\python.exe" (
  echo [1/6] 第一次启动：创建独立 Python 环境...
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3 -m venv .venv
  ) else (
    where python >nul 2>nul
    if errorlevel 1 (echo [错误] 未找到 Python 3。&goto :fail)
    python -m venv .venv
  )
  if errorlevel 1 goto :fail
) else (
  echo [1/6] Python 独立环境：正常
)
set "VPY=%~dp0.venv\Scripts\python.exe"

REM ---------- 3. 依赖检查 ----------
echo [2/6] 检查核心依赖...
"%VPY%" -c "import fastapi,uvicorn,ccxt,pandas,numpy,sklearn,dotenv; print('核心依赖正常')" >nul 2>nul
if errorlevel 1 (
  echo [提示] 正在安装/修复依赖，请等待...
  "%VPY%" -m pip install --disable-pip-version-check -r requirements.txt
  if errorlevel 1 (
    echo [错误] 依赖安装失败。请检查网络后重新运行。
    goto :fail
  )
)
echo [OK] 核心依赖正常

REM ---------- 4. 远程密钥 ----------
echo [3/6] 准备手机访问密钥...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p='.env'; $lines=@(Get-Content $p); $m=$lines | Where-Object { $_ -match '^ALPHA_REMOTE_API_KEY=(.*)$' } | Select-Object -First 1; $k=''; if($m){$k=($m -replace '^ALPHA_REMOTE_API_KEY=','').Trim()}; if([string]::IsNullOrWhiteSpace($k)){ $b=New-Object byte[] 24; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); $k=[Convert]::ToBase64String($b).TrimEnd('=') }; $lines=@($lines | Where-Object {$_ -notmatch '^ALPHA_REMOTE_API_KEY=' -and $_ -notmatch '^ALPHA_REMOTE_MODE='}); $lines+='ALPHA_REMOTE_MODE=1'; $lines+=('ALPHA_REMOTE_API_KEY='+$k); Set-Content -Path $p -Value $lines -Encoding UTF8" >nul 2>&1
if errorlevel 1 (echo [错误] 无法写入远程配置。&goto :fail)
for /f "tokens=1,* delims==" %%A in ('findstr /B "ALPHA_REMOTE_API_KEY=" .env') do set "REMOTE_KEY=%%B"
if not defined REMOTE_KEY (echo [错误] 没有生成远程访问密钥。&goto :fail)
echo [OK] 手机访问密钥已准备

REM ---------- 5. Cloudflared ----------
echo [4/6] 检查手机远程通道...
if not exist "tools" mkdir tools
if not exist "tools\cloudflared.exe" (
  echo [提示] 未找到 cloudflared，正在自动下载...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$u='https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe'; try { Invoke-WebRequest -UseBasicParsing -Uri $u -OutFile 'tools\cloudflared.exe' -ErrorAction Stop; exit 0 } catch { exit 1 }"
  if errorlevel 1 (
    echo [提示] PowerShell 下载失败，尝试 curl...
    where curl >nul 2>nul
    if errorlevel 1 (echo [错误] 系统没有 curl，无法下载 cloudflared。&goto :fail)
    curl.exe -L --fail --retry 3 -o "tools\cloudflared.exe" "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    if errorlevel 1 (echo [错误] cloudflared 下载失败。&goto :fail)
  )
)
for %%F in ("tools\cloudflared.exe") do set "CF_SIZE=%%~zF"
if not defined CF_SIZE (echo [错误] cloudflared 文件无效。&goto :fail)
if %CF_SIZE% LSS 10000000 (
  echo [错误] cloudflared 文件太小，可能下载的是错误页面。
  echo 请删除 tools\cloudflared.exe 后重试。
  goto :fail
)
echo [OK] cloudflared 正常

REM ---------- 6. 后端 + 健康检查 ----------
echo [5/6] 启动 ALPHA-X 后端...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>nul
if errorlevel 1 (
  start "ALPHA-X 后端（不要关闭）" cmd /k "cd /d ""%~dp0"" && ""%VPY%"" api_server.py"
) else (
  echo [OK] 发现 8000 端口已有 ALPHA-X 后端
)

set "HEALTH_OK=0"
for /l %%i in (1,1,45) do (
  timeout /t 1 /nobreak >nul
  powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2; if($r.StatusCode -eq 200){exit 0}else{exit 1} } catch { exit 1 }" >nul 2>nul
  if not errorlevel 1 (set "HEALTH_OK=1"&goto :health_done)
)
:health_done
if not "%HEALTH_OK%"=="1" (
  echo [错误] ALPHA-X 后端 45 秒内没有正常启动。
  echo 请看“ALPHA-X 后端（不要关闭）”窗口里的具体错误。
  goto :fail
)
echo [OK] ALPHA-X 后端正常，8000端口可访问

REM ---------- 建立 Tunnel ----------
echo [6/6] 正在建立手机访问通道...
if exist "remote_tunnel.log" del /q "remote_tunnel.log" >nul 2>&1
if exist "remote_url.txt" del /q "remote_url.txt" >nul 2>&1

start "ALPHA-X 手机远程通道（不要关闭）" cmd /c ""%~dp0tools\cloudflared.exe" tunnel --url http://127.0.0.1:8000 --no-autoupdate > "%~dp0remote_tunnel.log" 2>&1"

set "REMOTE_URL="
for /l %%i in (1,1,30) do (
  timeout /t 1 /nobreak >nul
  if exist "remote_tunnel.log" (
    for /f "usebackq tokens=*" %%U in (`powershell -NoProfile -Command "$t=Get-Content 'remote_tunnel.log' -Raw; if($t -match 'https://[-a-zA-Z0-9]+\.trycloudflare\.com'){ $Matches[0] }"`) do set "REMOTE_URL=%%U"
  )
  if defined REMOTE_URL goto :url_done
)
:url_done

if not defined REMOTE_URL (
  echo [错误] 没有拿到 Cloudflare 手机网址。
  echo.
  echo 请查看文件：remote_tunnel.log
  echo 或查看“ALPHA-X 手机远程通道（不要关闭）”窗口。
  goto :fail
)

set "FULL_URL=!REMOTE_URL!#key=!REMOTE_KEY!"
echo.
echo ==================================================
echo                  手机访问已成功
echo ==================================================
echo.
echo 手机网址：
echo !REMOTE_URL!
echo.
echo 手机访问密钥：
echo !REMOTE_KEY!
echo.
echo 建议直接复制下面这一整行到手机浏览器：
echo !FULL_URL!
echo.
echo 已自动复制到剪贴板（如果系统允许）。
echo.
echo 重要：不要关闭这个窗口，也不要关闭“手机远程通道”窗口。
echo 关闭后手机访问会断开。
echo ==================================================
echo !FULL_URL! | clip
echo.
echo 系统正在运行。按 Ctrl+C 只会结束远程通道。
echo 如需完全停止，请关闭后端窗口和本窗口。
echo.
:keep_alive
timeout /t 3600 /nobreak >nul
goto :keep_alive

:fail
echo.
echo ==================================================
echo [启动失败]
echo ==================================================
echo 交易策略没有因为本次失败而发送交易请求。
echo 请把本窗口最后 10-20 行错误文字发给我，
echo 我可以直接定位，不需要你猜。
echo.
pause
exit /b 1
