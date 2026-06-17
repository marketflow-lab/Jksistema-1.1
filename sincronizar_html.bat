@echo off
REM =====================================================
REM Fonte oficial dos HTML: /static
REM Espelha /static para a raiz legada e valida hashes.
REM =====================================================

setlocal
pushd "%~dp0" > nul

echo.
echo Sincronizando HTML: static\ -^> raiz
echo.

node scripts\sync-static-html-mirrors.js
if errorlevel 1 goto :erro

node electron_app\scripts\verify-installer-package.js
if errorlevel 1 goto :erro

echo.
echo Sincronizacao concluida. Fonte oficial: static\
echo.
popd > nul
exit /b 0

:erro
echo.
echo Sincronizacao falhou. Corrija os arquivos acima antes de testar ou empacotar.
echo.
popd > nul
exit /b 1
