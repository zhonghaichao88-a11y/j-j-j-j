@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo [1/4] 检查 Python ...
python -c "import sys; assert sys.version_info >= (3,10), 'Python 3.10+ required'"
if errorlevel 1 goto fail
if not exist .venv\Scripts\python.exe (
  echo [2/4] 第一次运行：创建运行环境，大约 1 分钟 ...
  python -m venv .venv
  if errorlevel 1 goto fail
)
echo [3/4] 安装/检查依赖（第一次要几分钟，下面会滚动显示进度，别关窗口）...
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -q pandas TA-Lib
if errorlevel 1 echo 提示：pandas / TA-Lib 没装上，三个急跌抄底打法不会出信号（其他打法照常）。截图发我。
if not exist .env copy .env.example .env >nul
echo [4/4] 启动订单流，准备好后会自动打开网页 ...
.venv\Scripts\python.exe launch_of.py
if errorlevel 1 goto fail
exit /b 0
:fail
echo 启动失败，看上面的报错，截图发我。
pause
exit /b 1
