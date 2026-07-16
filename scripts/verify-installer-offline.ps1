param(
    [string]$ResourcesRoot = "",
    [switch]$KeepWorkDir
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
if (-not $ResourcesRoot) {
    $ResourcesRoot = Join-Path $repoRoot "electron_app\dist-client-setup\win-unpacked\resources"
}
$resourcesPath = [IO.Path]::GetFullPath($ResourcesRoot)
$localApp = Join-Path $resourcesPath "local_app"
$wheelDir = Join-Path $localApp "python_wheels"
$requirementsPath = Join-Path $localApp "requirements.txt"
$pythonInstaller = Get-ChildItem -LiteralPath (Join-Path $localApp "python_runtime") -Filter "python-*.exe" -File |
    Sort-Object Name |
    Select-Object -First 1

if (-not (Test-Path -LiteralPath $localApp -PathType Container)) {
    throw "Pacote local_app nao encontrado em $localApp"
}
if (-not (Test-Path -LiteralPath $requirementsPath -PathType Leaf)) {
    throw "requirements.txt nao encontrado no pacote."
}
if (-not (Test-Path -LiteralPath $wheelDir -PathType Container)) {
    throw "Wheelhouse offline nao encontrado no pacote."
}
if (-not $pythonInstaller) {
    throw "Instalador Python empacotado nao encontrado."
}

$workBase = [IO.Path]::GetFullPath((Join-Path $repoRoot ".codex_tmp"))
New-Item -ItemType Directory -Force -Path $workBase | Out-Null
$workDir = [IO.Path]::GetFullPath((Join-Path $workBase ("installer-offline-smoke-" + [guid]::NewGuid().ToString("N"))))
$workPrefix = $workBase.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $workDir.StartsWith($workPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Diretorio temporario fora do workspace: $workDir"
}

$pythonDir = Join-Path $workDir "python"
$venvDir = Join-Path $workDir "venv"
$infoDir = Join-Path $workDir "info"
New-Item -ItemType Directory -Force -Path $workDir, $infoDir | Out-Null

try {
    Write-Host "[offline-smoke] Instalando o Python empacotado em area isolada..."
    $installerArgs = @(
        "/quiet",
        "InstallAllUsers=0",
        "TargetDir=`"$pythonDir`"",
        "Include_pip=1",
        "Include_launcher=0",
        "AssociateFiles=0",
        "Shortcuts=0",
        "Include_test=0",
        "PrependPath=0"
    )
    $installerProcess = Start-Process -FilePath $pythonInstaller.FullName -ArgumentList $installerArgs -Wait -PassThru -WindowStyle Hidden
    if ($installerProcess.ExitCode -ne 0) {
        throw "Instalador Python retornou codigo $($installerProcess.ExitCode)."
    }

    $pythonExe = Join-Path $pythonDir "python.exe"
    if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
        $registeredInstallPath = ""
        $registeredKey = "HKCU:\Software\Python\PythonCore\3.11\InstallPath"
        if (Test-Path -LiteralPath $registeredKey) {
            $registeredInstallPath = [string](Get-Item -LiteralPath $registeredKey).GetValue("")
        }
        $registeredPython = if ($registeredInstallPath) { Join-Path $registeredInstallPath "python.exe" } else { "" }
        if ($registeredPython -and (Test-Path -LiteralPath $registeredPython -PathType Leaf)) {
            $pythonExe = $registeredPython
            Write-Host "[offline-smoke] O instalador entrou em manutencao; usando o Python 3.11 registrado para validar a venv isolada."
        } else {
            throw "Python empacotado nao foi instalado em $pythonDir e nao ha Python 3.11 registrado utilizavel."
        }
    }
    $pythonVersion = (& $pythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
    if ($LASTEXITCODE -ne 0 -or $pythonVersion -ne "3.11") {
        throw "Bootstrap Python incompativel para o teste offline: $pythonVersion"
    }

    Write-Host "[offline-smoke] Criando venv e instalando somente a partir das wheels do pacote..."
    & $pythonExe -m venv $venvDir
    if ($LASTEXITCODE -ne 0) { throw "Falha ao criar a venv offline." }
    $venvPython = Join-Path $venvDir "Scripts\python.exe"
    & $venvPython -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) { throw "Falha ao preparar pip na venv offline." }
    & $venvPython -m pip install --disable-pip-version-check --no-index --find-links $wheelDir -r $requirementsPath
    if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar requirements sem internet." }
    & $venvPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw "pip check encontrou dependencias inconsistentes." }

    Write-Host "[offline-smoke] Validando modulos criticos e importacao do backend empacotado..."
    $previousInfoDir = $env:JK_INFO_DIR
    $previousAppVersion = $env:JK_APP_VERSION
    $previousNoBytecode = $env:PYTHONDONTWRITEBYTECODE
    $env:JK_INFO_DIR = $infoDir
    $env:JK_APP_VERSION = (Get-Content -LiteralPath (Join-Path $repoRoot "electron_app\package.json") -Raw | ConvertFrom-Json).version
    $env:PYTHONDONTWRITEBYTECODE = "1"
    Push-Location $localApp
    try {
        & $venvPython -B -c "import av, fastapi, faster_whisper, openai_codex, pandas, playwright, psycopg, selenium, uvicorn, websockets; import backend_api; assert getattr(backend_api, 'app', None) is not None; print('offline-imports-ok')"
        if ($LASTEXITCODE -ne 0) { throw "Falha ao importar modulos criticos/backend_api no pacote offline." }
    } finally {
        Pop-Location
        $env:JK_INFO_DIR = $previousInfoDir
        $env:JK_APP_VERSION = $previousAppVersion
        $env:PYTHONDONTWRITEBYTECODE = $previousNoBytecode
    }

    Write-Host "[offline-smoke] OK - Python, wheels e backend funcionam sem downloads de dependencias."
} finally {
    if ($KeepWorkDir) {
        Write-Host "[offline-smoke] Area temporaria preservada em $workDir"
    } elseif ($workDir.StartsWith($workPrefix, [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $workDir)) {
        Remove-Item -LiteralPath $workDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
