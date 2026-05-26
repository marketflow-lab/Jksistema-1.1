@echo off
TITLE Gerador de Executavel JK Sistema
echo ==========================================
echo      GERANDO INSTALADOR DO SISTEMA
echo ==========================================

cd electron_app
call npm install
call npm run dist

echo.
echo ------------------------------------------
echo SUCESSO! O instalador esta na pasta:
echo electron_app\dist
echo ------------------------------------------
pause