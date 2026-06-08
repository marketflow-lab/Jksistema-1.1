@echo off
setlocal
cd /d "%~dp0"

echo ==========================================
echo   Deploy Firebase OAuth Bridge - JK Sistema
echo ==========================================
echo.
echo Projeto Firebase: jkjkjk-485920
echo Hosting publico:
echo   https://jkjkjk-485920.web.app/auth/callback
echo   https://jkjkjk-485920.web.app/auth/google/callback
echo.

where npx.cmd >nul 2>nul
if errorlevel 1 (
  echo ERRO: Node/npm/npx nao encontrado.
  echo Instale o Node.js e tente novamente.
  pause
  exit /b 1
)

if exist "jkjkjk-485920-e598a0a0dcb9.json" (
  set "GOOGLE_APPLICATION_CREDENTIALS=%CD%\jkjkjk-485920-e598a0a0dcb9.json"
)

call npx.cmd --yes firebase-tools@latest deploy --only hosting --project jkjkjk-485920 --non-interactive
if errorlevel 1 (
  echo.
  echo Deploy falhou.
  echo Se aparecer SERVICE_DISABLED, ative a Cloud Resource Manager API:
  echo https://console.developers.google.com/apis/api/cloudresourcemanager.googleapis.com/overview?project=jkjkjk-485920
  echo.
  echo Tambem confirme se sua conta/conta de servico tem permissao Firebase Hosting Admin.
  pause
  exit /b %errorlevel%
)

echo.
echo Deploy concluido.
echo Cadastre no Bling:
echo https://jkjkjk-485920.web.app/auth/callback
pause
