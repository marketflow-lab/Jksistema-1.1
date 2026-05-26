@echo off
setlocal
cd /d "%~dp0"
".venv\Scripts\python.exe" gerenciar_usuarios_sql.py %*
endlocal
