@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在生成并上传未来 7 天的视频（每天晚上 7 点自动发布）...
echo 如果提示输入短信验证码，请输入手机收到的验证码。
python auto_publish.py
pause
