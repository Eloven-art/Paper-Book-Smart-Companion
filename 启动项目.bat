@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please run: python -m venv .venv
  echo Then run: .venv\Scripts\python -m pip install -r requirements.txt
  pause
  exit /b 1
)
echo Open http://127.0.0.1:8000 after the server starts.
".venv\Scripts\python.exe" -m server
pause
