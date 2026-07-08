@echo off
setlocal
TITLE JK Sistema Backend (DEV Reload)
cd /d "%~dp0"

REM Callback publico usado no OAuth local. O Firebase Hosting redireciona de volta para 127.0.0.1:8001.
set "JK_REDIRECT_URI=https://jkjkjk-485920.web.app/auth/callback"
set "JK_BLING_REDIRECT_URI=https://jkjkjk-485920.web.app/auth/callback"
set "GOOGLE_LOGIN_REDIRECT_URI_LOCAL=https://jkjkjk-485920.web.app/auth/google/callback"

echo ================================================
echo   INICIANDO SERVIDOR JK SISTEMA (DEV RELOAD)
echo ================================================
echo.
echo MODO DESENVOLVIMENTO: alteracoes em .py reiniciam a API automaticamente.
echo ATENCAO: sincronizacoes em andamento podem ser interrompidas ao salvar arquivos.
echo Mercado Livre OAuth callback: %JK_REDIRECT_URI%
echo Bling OAuth callback: %JK_BLING_REDIRECT_URI%
echo Google OAuth local callback: %GOOGLE_LOGIN_REDIRECT_URI_LOCAL%
echo.

echo 0. Limpando processos antigos do JK Sistema...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
	"$root = (Resolve-Path '%~dp0').Path.TrimEnd('\');" ^
	"$rootLower = $root.ToLowerInvariant();" ^
	"$appData = (Join-Path $env:APPDATA 'jk-sistema-desktop').ToLowerInvariant();" ^
	"$current = $PID;" ^
	"$procs = Get-CimInstance Win32_Process | Where-Object { $cmd = [string]$_.CommandLine; $exe = [string]$_.ExecutablePath; $name = [string]$_.Name; if ($_.ProcessId -eq $current) { $false } else { $text = ($cmd + ' ' + $exe).ToLowerInvariant(); (($name -ieq 'electron.exe') -and ($text.Contains($rootLower) -or $text.Contains($appData) -or $text.Contains('jk-sistema-desktop'))) -or ($cmd -and $cmd.ToLowerInvariant().Contains('jk_electron_launcher')) -or (($name -match 'pythonw?\.exe') -and $cmd -and (($cmd.ToLowerInvariant().Contains('uvicorn backend_api:app')) -or ($cmd.ToLowerInvariant().Contains('uvicorn promo_worker_api:app')))) } };" ^
	"foreach ($p in $procs) { Write-Host ('   Encerrando instancia antiga: PID ' + $p.ProcessId + ' - ' + $p.Name); Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue };" ^
	"$ports = @(8001,8011,8012);" ^
	"foreach ($port in $ports) { Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { if ($_ -and $_ -ne $current) { Write-Host ('   Liberando porta ' + $port + ': PID ' + $_); Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue } } }"
timeout /t 2 >nul
echo.

REM Localiza Python do sistema (sempre necessario para recriar venv se quebrada)
set "SYSTEM_PYTHON="
where py >nul 2>nul
if not errorlevel 1 set "SYSTEM_PYTHON=py -3"
if not defined SYSTEM_PYTHON (
	where python >nul 2>nul
	if not errorlevel 1 set "SYSTEM_PYTHON=python"
)

if not defined SYSTEM_PYTHON (
	echo ERRO: Python nao encontrado neste computador.
	echo Instale o Python 3 em https://www.python.org/downloads/
	echo e execute novamente este arquivo.
	pause
	exit /b 1
)

REM Verifica se a venv existente funciona de verdade nesta maquina
set "PYTHON_EXE="
if exist ".venv\Scripts\python.exe" (
	".venv\Scripts\python.exe" --version >nul 2>nul
	if not errorlevel 1 (
		set "PYTHON_EXE=.venv\Scripts\python.exe"
	) else (
		echo    Ambiente virtual corrompido ou de outra maquina. Recriando...
		rmdir /s /q .venv
	)
)

if not defined PYTHON_EXE (
	echo 0. Criando ambiente virtual local...
	call %SYSTEM_PYTHON% -m venv .venv
	if errorlevel 1 (
		echo ERRO: Nao foi possivel criar o ambiente virtual .venv
		pause
		exit /b 1
	)
	set "PYTHON_EXE=.venv\Scripts\python.exe"
)

echo 1. Verificando dependencias...
"%PYTHON_EXE%" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
	echo ERRO: Falha ao atualizar o pip da venv.
	pause
	exit /b 1
)

echo 1.1 Removendo pacote fitz incorreto, se existir...
"%PYTHON_EXE%" -m pip uninstall -y fitz >nul 2>nul

if exist "requirements.txt" (
	"%PYTHON_EXE%" -m pip install -r requirements.txt
) else (
	"%PYTHON_EXE%" -m pip install streamlit streamlit-authenticator pandas requests gspread google-auth plotly bcrypt urllib3 python-dotenv numpy beautifulsoup4 lxml openpyxl pymupdf python-barcode reportlab playwright fastapi uvicorn python-multipart selenium webdriver-manager "python-jose[cryptography]"
)
if errorlevel 1 (
	echo ERRO: Falha ao instalar as dependencias Python necessarias.
	pause
	exit /b 1
)

echo 1.3 Garantindo watcher de arquivos para auto-reload (watchfiles)...
"%PYTHON_EXE%" -m pip install --upgrade watchfiles
if errorlevel 1 (
	echo AVISO: Nao foi possivel instalar watchfiles.
	echo O servidor sera iniciado mesmo assim, mas o reload pode ficar instavel no Windows.
)

echo 1.2 Validando importacao do PyMuPDF...
"%PYTHON_EXE%" -c "import fitz; print(fitz.__doc__[:20] if getattr(fitz, '__doc__', None) else 'ok')" >nul 2>nul
if errorlevel 1 (
	echo ERRO: O PyMuPDF nao foi carregado corretamente.
	echo A instalacao local possui conflito com o pacote fitz.
	pause
	exit /b 1
)

echo 2. Instalando navegador Playwright...
"%PYTHON_EXE%" -m playwright install chromium
if errorlevel 1 (
	echo ERRO: Falha ao instalar o navegador do Playwright.
	pause
	exit /b 1
)

echo 2.1 Liberando porta 8001 (se necessario)...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8001" ^| findstr "LISTENING"') do (
	echo    Encerrando processo da porta 8001: PID %%P
	taskkill /PID %%P /T /F >nul 2>&1
)

echo 2.2 Liberando porta 8011 do worker de promocoes (se necessario)...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8011" ^| findstr "LISTENING"') do (
	echo    Encerrando processo da porta 8011: PID %%P
	taskkill /PID %%P /T /F >nul 2>&1
)

echo 2.3 Liberando porta 8012 da API auxiliar de IA (se necessario)...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8012" ^| findstr "LISTENING"') do (
	echo    Encerrando processo da porta 8012: PID %%P
	taskkill /PID %%P /T /F >nul 2>&1
)

echo 2.4 Garantindo PostgreSQL vetorial da IA (Docker/pgvector)...
set "DOCKER_EXE="
where docker >nul 2>nul
if not errorlevel 1 set "DOCKER_EXE=docker"
if not defined DOCKER_EXE if exist "C:\Program Files\Docker\Docker\resources\bin\docker.exe" set "DOCKER_EXE=C:\Program Files\Docker\Docker\resources\bin\docker.exe"
if defined DOCKER_EXE (
	"%DOCKER_EXE%" start jk-pgvector >nul 2>nul
	if errorlevel 1 (
		echo    AVISO: Nao foi possivel iniciar o container jk-pgvector. Abra o Docker Desktop e tente novamente.
	) else (
		echo    Container jk-pgvector pronto.
	)
) else (
	echo    AVISO: Docker nao encontrado. A IA usara apenas o contexto da tela ate o pgvector iniciar.
)

echo 2.5 Iniciando worker dedicado de promocoes...
start "JK Promo Worker" /min cmd /c "cd /d \"%~dp0\" && \"%PYTHON_EXE%\" -m uvicorn promo_worker_api:app --host 127.0.0.1 --port 8011"

echo 2.6 Iniciando API auxiliar de IA...
start "JK IA API" /min cmd /c "cd /d \"%~dp0\" && set OPENAI_MODEL=gpt-5.4-nano&& \"%PYTHON_EXE%\" -m uvicorn backend_api:app --host 127.0.0.1 --port 8012"

echo 3. Iniciando API em modo dev com auto-reload...
echo    O JK Sistema Desktop (Electron) sera iniciado automaticamente.
echo    Se nao abrir, acesse: http://127.0.0.1:8001/frontend_index.html
echo.
echo    AO SALVAR ARQUIVOS PYTHON, O SERVIDOR REINICIARA AUTOMATICAMENTE.
echo    EVITE SINCRONIZACOES LONGAS NESTE MODO.
echo.

REM Chamadas da IA rodam em workers paralelos para nao travar o servidor.
REM Aumente IA_MAX_WORKERS se precisar aceitar mais perguntas simultaneas.
REM IA_PROVIDER=auto usa Groq quando houver chave e Gemini como fallback.
set "IA_MAX_WORKERS=4"
set "IA_GEMINI_MAX_RETRIES=3"
set "IA_PROVIDER=auto"
set "GROQ_MODEL=llama-3.3-70b-versatile"
set "OPENAI_MODEL=gpt-5.4-nano"
set "ELECTRON_RUN_AS_NODE="

REM Detecta NPM para iniciar o app Electron
set "HAS_NPM="
where npm.cmd >nul 2>nul
if not errorlevel 1 set "HAS_NPM=1"

set "ELECTRON_READY="
if exist "node_modules\.bin\electron.cmd" set "ELECTRON_READY=1"

if not defined HAS_NPM goto :ABRIR_NO_NAVEGADOR
if defined ELECTRON_READY goto :ABRIR_ELECTRON

echo 3.0 Instalando dependencias do Desktop - Electron...
call npm.cmd install
if errorlevel 1 goto :ABRIR_NO_NAVEGADOR
if not exist "node_modules\.bin\electron.cmd" goto :ABRIR_NO_NAVEGADOR
set "ELECTRON_READY=1"

REM Espera 3 segundos e inicia o app Desktop; fallback para navegador
timeout /t 3 >nul
if defined ELECTRON_READY goto :ABRIR_ELECTRON
goto :ABRIR_NO_NAVEGADOR

:ABRIR_ELECTRON
echo 3.1 Iniciando JK Sistema Desktop - Electron...
if not exist "%~dp0logs" mkdir "%~dp0logs"
echo    Log do desktop: logs\electron_dev.log
set "ELECTRON_LAUNCHER=%TEMP%\jk_electron_launcher_dev.cmd"
(
	echo @echo off
	echo cd /d "%~dp0"
	echo set ELECTRON_RUN_AS_NODE=
	echo echo Aguardando backend local na porta 8001... ^>^> logs\electron_dev.log
	echo set WAIT_COUNT=0
	echo :WAIT_API
	echo powershell -NoProfile -ExecutionPolicy Bypass -Command "$client = New-Object Net.Sockets.TcpClient; try { $client.Connect('127.0.0.1', 8001); exit 0 } catch { exit 1 } finally { $client.Dispose() }" ^>nul 2^>^&1
	echo if not errorlevel 1 goto START_ELECTRON
	echo set /a WAIT_COUNT+=1
	echo if %%WAIT_COUNT%% GEQ 120 ^(
	echo ^  echo ERRO: Backend 8001 nao respondeu a tempo. ^>^> logs\electron_dev.log
	echo ^  exit /b 1
	echo ^)
	echo timeout /t 1 ^>nul
	echo goto WAIT_API
	echo :START_ELECTRON
	echo echo Backend 8001 pronto. Abrindo desktop... ^>^> logs\electron_dev.log
	echo if exist "%~dp0node_modules\electron\dist\electron.exe" ^(
	echo ^  echo Iniciando Electron por executavel local... ^>^> logs\electron_dev.log
	echo ^  set "JK_APP_ROOT=%~dp0"
	echo ^  set "JK_APP_ROOT=%%JK_APP_ROOT:~0,-1%%"
	echo ^  start "JK Sistema Desktop" /D "%~dp0" "%~dp0node_modules\electron\dist\electron.exe" "%%JK_APP_ROOT%%"
	echo ^) else ^(
	echo ^  echo Iniciando Electron por node_modules\.bin... ^>^> logs\electron_dev.log
	echo ^  call node_modules\.bin\electron.cmd . ^>^> logs\electron_dev.log 2^>^&1
	echo ^)
	echo exit /b 0
) > "%ELECTRON_LAUNCHER%"
start "JK Sistema Desktop Launcher" /min cmd /c ""%ELECTRON_LAUNCHER%""
goto :CONTINUAR_BACKEND

:ABRIR_NO_NAVEGADOR
echo AVISO: Nao foi possivel iniciar o Electron. Abrindo no navegador padrao...
start "" http://127.0.0.1:8001/frontend_index.html

:CONTINUAR_BACKEND

REM Modo desenvolvimento com reload automatico
REM --reload-dir explicito melhora a deteccao de mudancas no Windows.
"%PYTHON_EXE%" -m uvicorn backend_api:app --host 127.0.0.1 --port 8001 --reload --reload-dir .
pause
