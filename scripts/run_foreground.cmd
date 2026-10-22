@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set "PY="
if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY set "PY=python"
echo 前台运行采集器（Ctrl+C 停止）。用于调试，日常请用计划任务。
%PY% wid.py collect
pause
