@echo off
setlocal
set "JK_CONTEXT_HUB_SURFACE=development"
TITLE JK Sistema Backend
cd /d "%~dp0"

set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONUSERBASE="
set "VIRTUAL_ENV="
set "PYTHONNOUSERSITE=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "PIP_CONFIG_FILE=NUL"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"

REM A execucao pelo checkout deve usar exclusivamente codigo e runtime desta pasta.
REM Isso tambem neutraliza variaveis herdadas de uma copia instalada do aplicativo.
set "JK_APP_ROOT_DIR=%~dp0"
set "JK_LOCAL_BACKEND_SOURCE_DIR=%~dp0"
set "JK_LOCAL_BACKEND_DIR=%~dp0"
set "JK_APP_VERSION="
for /f "delims=" %%V in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$package = ConvertFrom-Json -InputObject (Get-Content -Raw -LiteralPath '%~dp0package.json'); Write-Output $package.version"') do set "JK_APP_VERSION=%%V"
if not defined JK_APP_VERSION (
	echo ERRO: Nao foi possivel ler a versao local em package.json.
	pause
	exit /b 1
)
REM Mantem o backend iniciado pelo BAT compativel com o perfil Firebase do Electron.
REM Sem estas flags, o Electron reinicia um backend saudavel e encerra esta janela.
set "JK_FIREBASE_LIVE_FEATURES=true"
set "FIREBASE_LIVE_FEATURES=true"
set "JK_FIREBASE_CHAT_PRESENCE_ENABLED=true"

set "RUNTIME_VERSIONS_FILE=%~dp0runtime-versions.json"
if not exist "%RUNTIME_VERSIONS_FILE%" (
	echo ERRO: Configuracao de runtimes nao encontrada: runtime-versions.json
	pause
	exit /b 1
)
set "PYTHON_VERSION="
set "PYTHON_MINOR="
set "PYTHON_ABI="
set "PYTHON_INSTALLER="
for /f "tokens=1,* delims==" %%A in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$config = ConvertFrom-Json -InputObject (Get-Content -Raw -LiteralPath '%RUNTIME_VERSIONS_FILE%'); $python = $config.python; Write-Output ('PYTHON_VERSION=' + $python.version); Write-Output ('PYTHON_MINOR=' + $python.minor); Write-Output ('PYTHON_ABI=' + $python.abi); Write-Output ('PYTHON_INSTALLER=' + $python.windowsInstaller)"') do set "%%A=%%B"
if not defined PYTHON_VERSION goto :ERRO_CONFIG_RUNTIME
if not defined PYTHON_MINOR goto :ERRO_CONFIG_RUNTIME
if not defined PYTHON_ABI goto :ERRO_CONFIG_RUNTIME
if not defined PYTHON_INSTALLER goto :ERRO_CONFIG_RUNTIME
if not "%PYTHON_ABI%"=="cp%PYTHON_MINOR:.=%" goto :ERRO_CONFIG_RUNTIME
goto :CONFIG_RUNTIME_OK

:ERRO_CONFIG_RUNTIME
echo ERRO: runtime-versions.json possui uma configuracao Python invalida.
pause
exit /b 1

:CONFIG_RUNTIME_OK

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
	"$venvPrefixLower = ((Join-Path $root '.venv').TrimEnd('\') + '\').ToLowerInvariant();" ^
	"$runtimePrefixLower = ((Join-Path $root '.python-runtime').TrimEnd('\') + '\').ToLowerInvariant();" ^
	"$appData = (Join-Path $env:APPDATA 'jk-sistema-desktop').ToLowerInvariant();" ^
	"$current = $PID;" ^
	"function Test-OwnPrivatePython([object]$process) { $name = [string]$process.Name; $exe = [string]$process.ExecutablePath; if (($name -notmatch '^pythonw?\.exe$') -or -not $exe) { return $false }; $exeLower = $exe.ToLowerInvariant(); return ($exeLower.StartsWith($venvPrefixLower) -or $exeLower.StartsWith($runtimePrefixLower)) };" ^
	"function Stop-JkProcess([object]$process, [string]$reason) { $pidToStop = [int]$process.ProcessId; Write-Host ('   Encerrando ' + $reason + ': PID ' + $pidToStop + ' - ' + $process.Name); try { Stop-Process -Id $pidToStop -Force -ErrorAction Stop } catch { if (Get-Process -Id $pidToStop -ErrorAction SilentlyContinue) { Write-Warning ('Nao foi possivel encerrar o PID ' + $pidToStop + ': ' + $_.Exception.Message) } else { Write-Host ('   PID ' + $pidToStop + ' ja estava encerrado.') }; return }; Wait-Process -Id $pidToStop -Timeout 10 -ErrorAction SilentlyContinue; if (Get-Process -Id $pidToStop -ErrorAction SilentlyContinue) { Write-Warning ('PID ' + $pidToStop + ' continua ativo apos 10 segundos.') } else { Write-Host ('   PID ' + $pidToStop + ' encerrado e confirmado.') } };" ^
	"$allProcesses = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue);" ^
	"$procs = @($allProcesses | Where-Object { $cmd = [string]$_.CommandLine; $exe = [string]$_.ExecutablePath; $name = [string]$_.Name; if ($_.ProcessId -eq $current) { $false } else { $text = ($cmd + ' ' + $exe).ToLowerInvariant(); (($name -ieq 'electron.exe') -and ($text.Contains($rootLower) -or $text.Contains($appData) -or $text.Contains('jk-sistema-desktop'))) -or (($cmd -and $cmd.ToLowerInvariant().Contains('jk_electron_launcher')) -and $text.Contains($rootLower)) -or (Test-OwnPrivatePython $_) } });" ^
	"foreach ($p in $procs) { $reason = if (Test-OwnPrivatePython $p) { 'Python privado local' } else { 'instancia antiga do JK Sistema' }; Stop-JkProcess $p $reason };" ^
	"$remaining = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { Test-OwnPrivatePython $_ });" ^
	"foreach ($p in $remaining) { Write-Warning ('Python privado continua ativo: PID ' + $p.ProcessId + ' - ' + $p.ExecutablePath) };" ^
	"$ports = @(8001,8011,8012);" ^
	"foreach ($port in $ports) { $owners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique); foreach ($ownerPid in $owners) { if (-not $ownerPid -or $ownerPid -eq $current) { continue }; $owner = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $ownerPid) -ErrorAction SilentlyContinue; if (-not $owner) { $owner = [pscustomobject]@{ ProcessId = $ownerPid; Name = 'desconhecido' } }; Stop-JkProcess $owner ('processo na porta ' + $port) } }"
timeout /t 2 >nul
echo.

REM Quando o pacote offline completo existe, o provisionador canonico cria ou
REM repara o Python privado e a .venv de forma transacional, sem usar a rede.
set "OFFLINE_RUNTIME_MANIFEST="
if exist "runtime-manifest.json" set "OFFLINE_RUNTIME_MANIFEST=1"
if exist ".installer_runtime\runtime-manifest.json" set "OFFLINE_RUNTIME_MANIFEST=1"
if defined OFFLINE_RUNTIME_MANIFEST if exist "python_runtime\portable\python.exe" if exist "python_wheels\manifest.json" if exist "scripts\provision_python_runtime.py" (
	echo 0.1 Preparando ambiente Python privado e offline...
	"python_runtime\portable\python.exe" -B -I "scripts\provision_python_runtime.py" --source-root "%CD%" --target-root "%CD%" --log-file "%CD%\logs\python-runtime-provision.log"
	if errorlevel 1 (
		echo ERRO: Nao foi possivel preparar o Python privado e a .venv.
		echo Consulte logs\python-runtime-provision.log e info\python-runtime-status.json.
		pause
		exit /b 1
	)
	set "SYSTEM_PYTHON=.python-runtime\python.exe"
	set "PYTHON_EXE=.venv\Scripts\python.exe"
	goto :DEPENDENCIAS_PRONTAS
)

REM Localiza exatamente o Python exigido por runtime-versions.json.
set "SYSTEM_PYTHON="
set "SYSTEM_PYTHON_ARGS="
if exist ".python-runtime\python.exe" (
	".python-runtime\python.exe" -B -I -c "import platform; raise SystemExit(0 if platform.python_version() == '%PYTHON_VERSION%' else 1)" >nul 2>nul
	if not errorlevel 1 set "SYSTEM_PYTHON=.python-runtime\python.exe"
)
if not defined SYSTEM_PYTHON (
	where py >nul 2>nul
	if not errorlevel 1 (
		py -%PYTHON_MINOR% -c "import platform; raise SystemExit(0 if platform.python_version() == '%PYTHON_VERSION%' else 1)" >nul 2>nul
		if not errorlevel 1 (
			set "SYSTEM_PYTHON=py"
			set "SYSTEM_PYTHON_ARGS=-%PYTHON_MINOR%"
		)
	)
)
if not defined SYSTEM_PYTHON (
	where python >nul 2>nul
	if not errorlevel 1 (
		python -c "import platform; raise SystemExit(0 if platform.python_version() == '%PYTHON_VERSION%' else 1)" >nul 2>nul
		if not errorlevel 1 set "SYSTEM_PYTHON=python"
	)
)

if not defined SYSTEM_PYTHON (
	echo ERRO: Python %PYTHON_VERSION% nao encontrado neste computador.
	echo Instale python_runtime\%PYTHON_INSTALLER%
	echo ou disponibilize o runtime local em .python-runtime\python.exe.
	pause
	exit /b 1
)

REM Verifica se a venv existente funciona de verdade nesta maquina
set "PYTHON_EXE="
if exist ".venv\Scripts\python.exe" (
	".venv\Scripts\python.exe" -B -I -c "import platform; raise SystemExit(0 if platform.python_version() == '%PYTHON_VERSION%' else 1)" >nul 2>nul
	if not errorlevel 1 (
		set "PYTHON_EXE=.venv\Scripts\python.exe"
	) else (
		echo    Ambiente virtual incompativel com Python %PYTHON_VERSION%. Recriando...
		rmdir /s /q .venv
	)
)

if not defined PYTHON_EXE (
	echo 0. Criando ambiente virtual local...
	call "%SYSTEM_PYTHON%" %SYSTEM_PYTHON_ARGS% -B -I -m venv .venv
	if errorlevel 1 (
		echo ERRO: Nao foi possivel criar o ambiente virtual .venv
		pause
		exit /b 1
	)
	set "PYTHON_EXE=.venv\Scripts\python.exe"
)

"%PYTHON_EXE%" -B -I -c "import platform; raise SystemExit(0 if platform.python_version() == '%PYTHON_VERSION%' else 1)" >nul 2>nul
if errorlevel 1 (
	echo ERRO: A .venv criada nao usa Python %PYTHON_VERSION%.
	pause
	exit /b 1
)

echo 1. Verificando dependencias...
"%PYTHON_EXE%" -B -I -m pip --isolated --version
if errorlevel 1 (
	echo ERRO: pip indisponivel na venv.
	pause
	exit /b 1
)

echo 1.1 Removendo pacote fitz incorreto, se existir...
"%PYTHON_EXE%" -B -I -m pip --isolated uninstall -y fitz >nul 2>nul

if exist "requirements.txt" (
	if exist "python_wheels\manifest.json" (
		"%PYTHON_EXE%" -B -I -m pip --isolated install --require-hashes --no-index --find-links "python_wheels" -r requirements.txt
	) else (
		"%PYTHON_EXE%" -B -I -m pip --isolated install --require-hashes -r requirements.txt
	)
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

:DEPENDENCIAS_PRONTAS
echo 1.2 Validando importacao do PyMuPDF...
"%PYTHON_EXE%" -B -I -c "import fitz; print(fitz.__doc__[:20] if getattr(fitz, '__doc__', None) else 'ok')" >nul 2>nul
if errorlevel 1 (
	echo ERRO: O PyMuPDF nao foi carregado corretamente.
	echo A instalacao local possui conflito com o pacote fitz.
	pause
	exit /b 1
)

echo 2. Liberando porta 8001 (se necessario)...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8001" ^| findstr "LISTENING"') do (
	echo    Encerrando processo da porta 8001: PID %%P
	taskkill /PID %%P /F >nul 2>&1
)

echo 2.1 Liberando porta 8011 do worker de promocoes (se necessario)...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8011" ^| findstr "LISTENING"') do (
	echo    Encerrando processo da porta 8011: PID %%P
	taskkill /PID %%P /F >nul 2>&1
)

echo 2.2 Iniciando worker dedicado de promocoes...
start "JK Promo Worker" /min cmd /c "cd /d \"%~dp0\" && \"%PYTHON_EXE%\" -B -I -m uvicorn --app-dir \"%CD%\" promo_worker_api:app --host 127.0.0.1 --port 8011"

echo 3. Iniciando API...
echo    O JK Sistema Desktop (Electron) sera iniciado automaticamente.
echo    Se nao abrir, acesse: http://127.0.0.1:8001/frontend_index.html
echo.
echo    NAO FECHE ESTA JANELA PRETA ENQUANTO USAR O SISTEMA.
echo.

set "ELECTRON_RUN_AS_NODE="
set "JK_LOCAL_SERVERS_PREPARED_BY_LAUNCHER=1"

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
"%PYTHON_EXE%" -B -I -m uvicorn --app-dir "%CD%" backend_api:app --port 8001
pause
