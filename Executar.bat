@echo off
TITLE JK Sistema de Gestao Launcher
echo Iniciando o sistema JK...

REM O bootstrap canonico le runtime-versions.json, prepara o Python exigido,
REM inicia o backend e abre o Electron.
call "%~dp0iniciar_servidor.bat"
exit /b %errorlevel%
