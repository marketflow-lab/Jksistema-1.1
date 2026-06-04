@echo off
TITLE JK Sistema de Gestao Launcher
echo Iniciando o sistema JK...

:: 1. Garante que o diretÃ³rio de trabalho Ã© a pasta do arquivo
cd /d "%~dp0"

:: Callback publico usado no OAuth local. O Firebase Hosting redireciona de volta para 127.0.0.1:8001.
set "JK_REDIRECT_URI=https://jkjkjk-485920.web.app/auth/callback"
set "JK_BLING_REDIRECT_URI=https://jkjkjk-485920.web.app/auth/callback"
set "GOOGLE_LOGIN_REDIRECT_URI_LOCAL=https://jkjkjk-485920.web.app/auth/google/callback"

:: 2. Inicia o Servidor Backend (FastAPI) na porta 8001
if exist ".venv\Scripts\python.exe" (
    start /min cmd /c "cd /d \"%~dp0\" && .venv\Scripts\python.exe -m uvicorn backend_api:app --host 127.0.0.1 --port 8001"
) else (
    start /min cmd /c "python -m uvicorn backend_api:app --host 127.0.0.1 --port 8001"
)

:: 3. Espera 4 segundos para o servidor subir
timeout /t 4 >nul

:: 4. Inicia o Electron (Launcher Desktop)
set "ELECTRON_RUN_AS_NODE="
cd electron_app

:: Verifica se node_modules existe, se nÃ£o, instala
if not exist "node_modules" (
    echo Instalando dependencias do Electron...
    call npm install
)

start npm start
