@echo off
chcp 65001 >nul
cd /d "%~dp0..\social-auto-upload"
echo 会弹出一个浏览器窗口，用手机抖音扫码登录。登录成功后窗口会自动关闭。
.venv\Scripts\sau douyin login --account kousuan --headed
.venv\Scripts\sau douyin check --account kousuan
pause
