@echo off & chcp 65001>nul & cd /d "%~dp0" & where python>nul 2>nul && python scripts\vlm_setup.py %* || py -3.11 scripts\vlm_setup.py %* & pause
