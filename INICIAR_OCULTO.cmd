@echo off
rem STT Portatil sem janela. Usado pelo "Iniciar com o Windows" da tela (Startup\STT_Portatil.vbs).
rem Saida em dados\logs\stt.log. Para desligar: botao "Desligar o servico" na tela, ou PARAR.bat.
setlocal
cd /d "%~dp0"
set "PYTHONNOUSERSITE=1"
set "PYTHONIOENCODING=utf-8"
if not exist "%~dp0dados\logs" mkdir "%~dp0dados\logs"

:subir
echo [%date% %time%] ==== iniciando ==== >> "%~dp0dados\logs\stt.log"
"%~dp0runtime\python.exe" -s "%~dp0app\servidor.py" >> "%~dp0dados\logs\stt.log" 2>&1
if %errorlevel%==3 goto subir
echo [%date% %time%] saiu com codigo %errorlevel% >> "%~dp0dados\logs\stt.log"
