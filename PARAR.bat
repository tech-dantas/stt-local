@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set "PYTHONNOUSERSITE=1"
"%~dp0runtime\python.exe" -s "%~dp0app\parar.py"
timeout /t 3 >nul
