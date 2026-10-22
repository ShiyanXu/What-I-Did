@echo off
chcp 65001 >nul
cd /d "%~dp0.."
if exist "web\index.html" (
  start "" "web\index.html"
) else (
  echo 还没有生成看板。先运行:  python wid.py report
  pause
)
