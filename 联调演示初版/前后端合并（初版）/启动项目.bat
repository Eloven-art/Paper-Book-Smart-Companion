@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -X utf8 -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -X utf8 scripts\upgrade_reader_data.py
if errorlevel 1 goto failed
echo Open http://127.0.0.1:8000/?preview=1 after the server starts.
".venv\Scripts\python.exe" -X utf8 -m server
pause
exit /b
:failed
echo Setup failed. Please share the error above.
pause
exit /b 1
