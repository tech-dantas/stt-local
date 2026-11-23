@echo off
REM ====================================================================
REM  STT Local - clique duas vezes para usar.
REM  Sobe a transcricao de audio (Parakeet, no processador) e abre a tela
REM  de configuracao no navegador. Fechar esta janela desliga o servico.
REM  Nada a instalar: o Python e o modelo ja moram nesta pasta.
REM ====================================================================
chcp 65001 >nul 2>&1
setlocal
cd /d "%~dp0"
title STT Local
set "PYTHONNOUSERSITE=1"
set "PYTHONIOENCODING=utf-8"
set "ABRIR=--abrir"

:subir
"%~dp0runtime\python.exe" -s "%~dp0app\servidor.py" %ABRIR%
REM Codigo 3 = a tela mudou porta ou rede: sobe de novo, sem reabrir o navegador.
if %errorlevel%==3 (
    set "ABRIR="
    goto subir
)
echo.
echo  STT Local desligado.
pause
