@echo off
setlocal
cd /d "%~dp0"
if defined JK_NODE_BIN (
  set ELECTRON_RUN_AS_NODE=1
  "%JK_NODE_BIN%" scripts\credenciais.js import %*
) else (
  node scripts\credenciais.js import %*
)
set JK_CREDENTIALS_EXIT_CODE=%ERRORLEVEL%
if "%JK_CREDENTIALS_NO_PAUSE%"=="" (
  echo.
  pause
)
exit /b %JK_CREDENTIALS_EXIT_CODE%
