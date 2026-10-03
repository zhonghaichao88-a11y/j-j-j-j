@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/4] 安装视频生成需要的组件...
python -m pip install -q pillow edge-tts reportlab || (echo 没装好 Python，请先到 python.org 安装并勾选 Add Python to PATH & pause & exit /b)
where ffmpeg >nul 2>nul || winget install -e --id Gyan.FFmpeg
echo [2/4] 下载自动发布工具 social-auto-upload...
if not exist "..\social-auto-upload" (
  powershell -NoProfile -Command "Invoke-WebRequest https://github.com/dreammis/social-auto-upload/archive/refs/heads/main.zip -OutFile ..\sau.zip; Expand-Archive ..\sau.zip -DestinationPath ..\ -Force; Rename-Item ..\social-auto-upload-main social-auto-upload; Remove-Item ..\sau.zip"
)
if not exist "..\social-auto-upload" (echo 下载失败，请检查网络后重试 & pause & exit /b)
echo [3/4] 安装自动发布工具...
pushd ..\social-auto-upload
if not exist conf.py copy conf.example.py conf.py >nul
python -m venv .venv
.venv\Scripts\python -m pip install -q -e . playwright
echo [4/4] 安装浏览器（较大，请耐心等待）...
set PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright
.venv\Scripts\patchright install chromium
popd
echo.
echo 安装完成！下一步：双击「登录抖音.bat」扫码登录。
pause
