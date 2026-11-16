@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if errorlevel 1 goto failed
set "PBSC_PY=.venv\Scripts\python.exe"
"%PBSC_PY%" -X utf8 -m pip install -r requirements.txt
if errorlevel 1 goto failed
"%PBSC_PY%" -X utf8 scripts\download_ocr_models.py
if errorlevel 1 goto failed
"%PBSC_PY%" -X utf8 scripts\download_asr_models.py
if errorlevel 1 goto failed
"%PBSC_PY%" -X utf8 scripts\download_llm_models.py %*
if errorlevel 1 goto failed
echo Setup complete.
pause
exit /b 0
:failed
echo Setup failed. Fix the failed step above and run setup again.
pause
exit /b 1
