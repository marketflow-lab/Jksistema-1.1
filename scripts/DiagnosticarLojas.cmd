@echo off
setlocal DisableDelayedExpansion
title Diagnostico das lojas - JK Sistema
echo Preparando o diagnostico das lojas desta maquina...
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0shared_sync_store_report.ps1"
set "diagnostic_exit=%errorlevel%"
echo.
if not "%diagnostic_exit%"=="0" (
    echo O diagnostico nao terminou. Confira a mensagem acima.
) else (
    echo Envie o arquivo relatorio-lojas-DATA-HORA.json salvo nesta mesma pasta.
)
echo.
echo Pressione qualquer tecla para fechar esta janela.
pause >nul
exit /b %diagnostic_exit%
