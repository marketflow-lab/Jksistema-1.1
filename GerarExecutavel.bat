@echo off
TITLE Gerador de Executavel JK Sistema
echo ==========================================
echo      GERANDO INSTALADOR DO SISTEMA
echo ==========================================

cd /d "%~dp0"

set "FIREBASE_CREDENTIAL_FOUND="
for %%F in ("jkjkjk-*.json" "firebase-service-account.json" "firebase_service_account.json" "*service-account*.json") do (
    if exist %%~F set "FIREBASE_CREDENTIAL_FOUND=1"
)

if not defined FIREBASE_CREDENTIAL_FOUND (
    echo.
    echo ERRO: Nenhuma credencial Firebase encontrada na raiz do projeto.
    echo Coloque aqui o JSON da service account, por exemplo:
    echo   jkjkjk-485920-e598a0a0dcb9.json
    echo ou:
    echo   firebase-service-account.json
    echo.
    echo Sem esse arquivo, o instalador normal nao consegue consultar logins no Firebase.
    pause
    exit /b 1
)

echo.
echo Preparando Python e dependencias para o instalador...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\prepare_installer_runtime.ps1"
if errorlevel 1 (
    echo.
    echo ERRO: Falha ao preparar as dependencias do instalador.
    pause
    exit /b 1
)

cd electron_app
call npm ci
call npm run dist
if errorlevel 1 (
    echo.
    echo ERRO: Falha ao gerar o instalador.
    pause
    exit /b 1
)

echo.
echo ------------------------------------------
echo SUCESSO! O instalador esta na pasta:
echo electron_app\dist-client-setup
echo ------------------------------------------
pause
