@echo off
REM Gera o pacote portatil em dist\ (veja scripts\empacotar.py). Opcoes: --com-modelo  --sem-zip
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set "PYTHONNOUSERSITE=1"
set "PYTHONIOENCODING=utf-8"
"%~dp0runtime\python.exe" -s "%~dp0scripts\empacotar.py" %*
echo.
pause
