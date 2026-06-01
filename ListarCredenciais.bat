@echo off
setlocal
cd /d "%~dp0"
node scripts\credenciais.js list %*
