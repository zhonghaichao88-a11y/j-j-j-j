@echo off
chcp 65001 >nul
title SeeU 视频交友 - 本地测试
cd /d "%~dp0"

echo ========================================
echo   SeeU 视频交友 一键启动（本地测试）
echo ========================================
echo.

echo [1/4] 检查 Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 没有找到 Python。
    echo        请到 https://www.python.org/downloads/ 安装 Python 3.10 或以上，
    echo        安装时一定要勾选 "Add Python to PATH"，装好后重新双击本文件。
    pause
    exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo [错误] Python 版本太旧，需要 3.10 或以上。当前版本：
    python --version
    pause
    exit /b 1
)
echo [OK] Python 可用

echo.
echo [2/4] 安装依赖（第一次会慢一点）...
python -m pip install -q -r server\requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
if errorlevel 1 (
    echo [提示] 清华镜像失败，改用默认源重试...
    python -m pip install -q -r server\requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请检查网络后重试
        pause
        exit /b 1
    )
)
echo [OK] 依赖已安装

echo.
echo [3/4] 准备测试数据...
if not exist "server\seeu.db" (
    python -m server.seed
) else (
    echo [OK] 已有数据，跳过（想清空重来就删除 server\seeu.db）
)

netstat -ano | findstr /r /c:":8000 .*LISTENING" >nul
if not errorlevel 1 (
    echo.
    echo [错误] 8000 端口已被占用，可能已经启动过一次。
    echo        请先关闭之前的启动窗口，再重新双击本文件。
    pause
    exit /b 1
)

echo.
echo [4/4] 启动服务...
echo.
echo ========================================
echo   启动成功后会自动打开两个浏览器窗口：
echo.
echo   普通窗口 = 你（用户 A）
echo     手机号随便填，例如 13800000001
echo   无痕窗口 = 测试主播（用户 B）
echo     手机号填 19900000000
echo.
echo   验证码点「获取验证码」会自动填好
echo   充值选「模拟支付（开发模式）」
echo   想自己打开：http://127.0.0.1:8000
echo.
echo   管理后台：http://127.0.0.1:8000/admin.html  密码 admin123（仅本地测试）
echo.
echo   关闭本窗口 = 停止服务
echo ========================================
echo.

rem 3 秒后打开浏览器：普通窗口 + 无痕窗口（优先 Chrome，其次 Edge）
set "URL=http://127.0.0.1:8000"
set "BROWSER="
set "PRIVATE="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "BROWSER=%ProgramFiles%\Google\Chrome\Application\chrome.exe" & set "PRIVATE=--incognito"
if not defined BROWSER if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "BROWSER=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" & set "PRIVATE=--incognito"
if not defined BROWSER if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "BROWSER=%LocalAppData%\Google\Chrome\Application\chrome.exe" & set "PRIVATE=--incognito"
if not defined BROWSER if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "BROWSER=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" & set "PRIVATE=--inprivate"
if not defined BROWSER if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" set "BROWSER=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" & set "PRIVATE=--inprivate"

if defined BROWSER (
    start "" /b powershell -NoProfile -Command "Start-Sleep 3; Start-Process '%BROWSER%' '%URL%'; Start-Sleep 1; Start-Process '%BROWSER%' '%PRIVATE% %URL%'"
) else (
    start "" /b powershell -NoProfile -Command "Start-Sleep 3; Start-Process '%URL%'"
    echo [提示] 没找到 Chrome 或 Edge，只打开了一个窗口。
    echo        第二个用户请在浏览器里按 Ctrl+Shift+N 开无痕窗口，打开 %URL%
)

set SEEU_DEV=1
if not defined SEEU_ADMIN_TOKEN set SEEU_ADMIN_TOKEN=admin123
python -m uvicorn server.main:app --host 127.0.0.1 --port 8000
echo.
echo 服务已停止。
pause
