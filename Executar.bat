@echo off
TITLE JK Sistema de Gestao Launcher
echo Iniciando o sistema JK...

REM O bootstrap canonico prepara o Python 3.11.9, inicia o backend e abre o Electron.
call "%~dp0iniciar_servidor.bat"
exit /b %errorlevel%
