@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set "PY="
if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY set "PY=python"
%PY% scripts\install_tasks.py --uninstall
echo.
pause
