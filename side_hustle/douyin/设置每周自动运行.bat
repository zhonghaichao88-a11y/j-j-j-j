@echo off
chcp 65001 >nul
cd /d "%~dp0"
schtasks /Create /F /TN "抖音小说推文自动排期" /SC WEEKLY /D SUN /ST 20:00 /TR "cmd /c cd /d \"%~dp0\" && python auto_publish.py"
echo.
echo 已设置：每周日晚上 8 点自动生成并排期下一周的视频（那个时间电脑要开着）。
echo 想取消：双击「取消每周自动运行.bat」
pause
