@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\launch_demo.ps1 %*
set "PBSC_EXIT=%errorlevel%"
pause
exit /b %PBSC_EXIT%
