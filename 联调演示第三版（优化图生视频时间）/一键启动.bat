@echo off
pushd "%~dp0" || exit /b 1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\launch_demo.ps1 %*
set "PBSC_EXIT=%errorlevel%"
popd
pause
exit /b %PBSC_EXIT%
