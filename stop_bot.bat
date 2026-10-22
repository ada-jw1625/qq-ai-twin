@echo off
chcp 65001 >nul
echo 关闭小白机器人（只关 bot.py，不动 NapCat 和 QQ）...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*bot.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; 'stopped ' + $_.ProcessId }"
pause
