@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto python
start "" http://localhost:8000
py -m http.server 8000 --bind 127.0.0.1
pause
exit /b
:python
where python >nul 2>nul
if errorlevel 1 goto missing
start "" http://localhost:8000
python -m http.server 8000 --bind 127.0.0.1
pause
exit /b
:missing
echo Python 3 is required. Install Python and run this file again.
pause
