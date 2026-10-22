@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo 启动小白机器人（先确认 NapCat 已登录小白）...
python -u bot.py
pause
