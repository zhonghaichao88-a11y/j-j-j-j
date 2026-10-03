@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 第一次使用会自动安装需要的组件...
python -m pip install -q pillow edge-tts reportlab
where ffmpeg >nul 2>nul || (echo 没有找到 ffmpeg，请先按 说明.md 安装 & pause & exit /b)
echo 1 = 一年级上册   2 = 一年级下册   3 = 二年级上册   4 = 二年级下册   5 = 三年级上册
set /p N=选择年级（输入数字）:
if "%N%"=="1" set GRADE=一年级上册
if "%N%"=="2" set GRADE=一年级下册
if "%N%"=="3" set GRADE=二年级上册
if "%N%"=="4" set GRADE=二年级下册
if "%N%"=="5" set GRADE=三年级上册
if not defined GRADE (echo 输入不对 & pause & exit /b)
set /p FROM=从第几期开始（输入数字）:
set /a TO=%FROM%+6
python kousuan_video.py --grade %GRADE% --issue %FROM% --to %TO%
echo.
echo 完成！视频在「成品视频」文件夹里，每个视频旁边的 .txt 是发布标题和话题。
pause
