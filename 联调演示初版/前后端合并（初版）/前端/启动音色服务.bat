@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto python
py -m backend.launch
pause
exit /b
:python
python -m backend.launch
pause
