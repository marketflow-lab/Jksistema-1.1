@echo off
setlocal
TITLE JK Sistema Backend
cd /d "%~dp0"

REM Callback publico usado no OAuth local. O Firebase Hosting redireciona de volta para 127.0.0.1:8001.
set "JK_REDIRECT_URI=https://jkjkjk-485920.web.app/auth/callback"
set "JK_BLING_REDIRECT_URI=https://jkjkjk-485920.web.app/auth/callback"
set "GOOGLE_LOGIN_REDIRECT_URI_LOCAL=https://jkjkjk-485920.web.app/auth/google/callback"

echo ==========================================
echo   INICIANDO SERVIDOR JK SISTEMA (FASTAPI)
echo ==========================================
echo.
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

REM Localiza exatamente o Python 3.11.9 exigido pelo lock de dependencias.
set "SYSTEM_PYTHON="
set "SYSTEM_PYTHON_ARGS="
if exist ".python-runtime\python.exe" (
	".python-runtime\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info[:3] == (3, 11, 9) else 1)" >nul 2>nul
	if not errorlevel 1 set "SYSTEM_PYTHON=.python-runtime\python.exe"
)
if not defined SYSTEM_PYTHON (
	where py >nul 2>nul
	if not errorlevel 1 (
		py -3.11 -c "import sys; raise SystemExit(0 if sys.version_info[:3] == (3, 11, 9) else 1)" >nul 2>nul
		if not errorlevel 1 (
			set "SYSTEM_PYTHON=py"
			set "SYSTEM_PYTHON_ARGS=-3.11"
		)
	)
)
if not defined SYSTEM_PYTHON (
	where python >nul 2>nul
	if not errorlevel 1 (
		python -c "import sys; raise SystemExit(0 if sys.version_info[:3] == (3, 11, 9) else 1)" >nul 2>nul
		if not errorlevel 1 set "SYSTEM_PYTHON=python"
	)
)

if not defined SYSTEM_PYTHON (
	echo ERRO: Python 3.11.9 nao encontrado neste computador.
	echo Instale python_runtime\python-3.11.9-amd64.exe
	echo ou disponibilize o runtime local em .python-runtime\python.exe.
	pause
	exit /b 1
)

REM Verifica se a venv existente funciona de verdade nesta maquina
set "PYTHON_EXE="
if exist ".venv\Scripts\python.exe" (
	".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info[:3] == (3, 11, 9) else 1)" >nul 2>nul
	if not errorlevel 1 (
		set "PYTHON_EXE=.venv\Scripts\python.exe"
	) else (
		echo    Ambiente virtual incompativel com Python 3.11.9. Recriando...
		rmdir /s /q .venv
	)
)

if not defined PYTHON_EXE (
	echo 0. Criando ambiente virtual local...
	call "%SYSTEM_PYTHON%" %SYSTEM_PYTHON_ARGS% -m venv .venv
	if errorlevel 1 (
		echo ERRO: Nao foi possivel criar o ambiente virtual .venv
		pause
		exit /b 1
	)
	set "PYTHON_EXE=.venv\Scripts\python.exe"
)

"%PYTHON_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info[:3] == (3, 11, 9) else 1)" >nul 2>nul
if errorlevel 1 (
	echo ERRO: A .venv criada nao usa Python 3.11.9.
	pause
	exit /b 1
)

echo 1. Verificando dependencias...
"%PYTHON_EXE%" -m pip --version
if errorlevel 1 (
	echo ERRO: pip indisponivel na venv.
	pause
	exit /b 1
)

echo 1.1 Removendo pacote fitz incorreto, se existir...
"%PYTHON_EXE%" -m pip uninstall -y fitz >nul 2>nul

if exist "requirements.txt" (
	"%PYTHON_EXE%" -m pip install -r requirements.txt
) else (
	echo ERRO: requirements.txt travado nao encontrado.
	pause
	exit /b 1
)
if errorlevel 1 (
	echo ERRO: Falha ao instalar as dependencias Python necessarias.
	pause
	exit /b 1
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
	taskkill /PID %%P /F >nul 2>&1
)

echo 2.2 Liberando porta 8011 do worker de promocoes (se necessario)...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8011" ^| findstr "LISTENING"') do (
	echo    Encerrando processo da porta 8011: PID %%P
	taskkill /PID %%P /F >nul 2>&1
)

echo 2.3 Iniciando worker dedicado de promocoes...
start "JK Promo Worker" /min cmd /c "cd /d \"%~dp0\" && \"%PYTHON_EXE%\" -m uvicorn promo_worker_api:app --host 127.0.0.1 --port 8011"

echo 3. Iniciando API...
echo    O JK Sistema Desktop (Electron) sera iniciado automaticamente.
echo    Se nao abrir, acesse: http://127.0.0.1:8001/frontend_index.html
echo.
echo    NAO FECHE ESTA JANELA PRETA ENQUANTO USAR O SISTEMA.
echo.

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
call npm.cmd ci
if errorlevel 1 goto :ABRIR_NO_NAVEGADOR
if not exist "node_modules\.bin\electron.cmd" goto :ABRIR_NO_NAVEGADOR
set "ELECTRON_READY=1"

:ABRIR_ELECTRON
echo 3.1 Iniciando JK Sistema Desktop - Electron...
if not exist "%~dp0logs" mkdir "%~dp0logs"
echo    Log do desktop: logs\electron.log
set "ELECTRON_LAUNCHER=%TEMP%\jk_electron_launcher_prod.cmd"
(
	echo @echo off
	echo cd /d "%~dp0"
	echo set ELECTRON_RUN_AS_NODE=
	echo echo Aguardando backend local na porta 8001... ^>^> logs\electron.log
	echo set WAIT_COUNT=0
	echo :WAIT_API
	echo powershell -NoProfile -ExecutionPolicy Bypass -Command "$client = New-Object Net.Sockets.TcpClient; try { $client.Connect('127.0.0.1', 8001); exit 0 } catch { exit 1 } finally { $client.Dispose() }" ^>nul 2^>^&1
	echo if not errorlevel 1 goto START_ELECTRON
	echo set /a WAIT_COUNT+=1
	echo if %%WAIT_COUNT%% GEQ 120 ^(
	echo ^  echo ERRO: Backend 8001 nao respondeu a tempo. ^>^> logs\electron.log
	echo ^  exit /b 1
	echo ^)
	echo timeout /t 1 ^>nul
	echo goto WAIT_API
	echo :START_ELECTRON
	echo echo Backend 8001 pronto. Abrindo desktop... ^>^> logs\electron.log
	echo if exist "%~dp0node_modules\electron\dist\electron.exe" ^(
	echo ^  echo Iniciando Electron por executavel local... ^>^> logs\electron.log
	echo ^  powershell -NoProfile -ExecutionPolicy Bypass -Command "Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue; $appRoot = '%~dp0'; $appPath = $appRoot.TrimEnd('\'); Start-Process -FilePath ($appRoot + 'node_modules\electron\dist\electron.exe') -ArgumentList ('\"' + $appPath + '\"') -WorkingDirectory $appRoot"
	echo ^) else ^(
	echo ^  echo Iniciando Electron por node_modules\.bin... ^>^> logs\electron.log
	echo ^  call node_modules\.bin\electron.cmd . ^>^> logs\electron.log 2^>^&1
	echo ^)
	echo exit /b 0
) > "%ELECTRON_LAUNCHER%"
start "JK Sistema Desktop Launcher" /min cmd /c ""%ELECTRON_LAUNCHER%""
goto :CONTINUAR_BACKEND

:ABRIR_NO_NAVEGADOR
echo AVISO: Nao foi possivel iniciar o Electron. Abrindo no navegador padrao...
start "" http://127.0.0.1:8001/frontend_index.html

:CONTINUAR_BACKEND

REM Executa sem --reload para evitar conflito com Playwright subprocess
"%PYTHON_EXE%" -m uvicorn backend_api:app --port 8001
pause
