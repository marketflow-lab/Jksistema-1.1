param(
    [string]$ResourcesRoot = "",
    [string]$WorkBase = "",
    [switch]$KeepWorkDir
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

if ($PSVersionTable.PSEdition -eq "Desktop") {
    $windowsPowerShellModulePaths = @(
        (Join-Path ([Environment]::GetFolderPath("MyDocuments")) "WindowsPowerShell\Modules")
        (Join-Path $env:ProgramFiles "WindowsPowerShell\Modules")
        (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\Modules")
    )
    $env:PSModulePath = $windowsPowerShellModulePaths -join [IO.Path]::PathSeparator
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
if (-not $ResourcesRoot) {
    $ResourcesRoot = Join-Path $repoRoot "electron_app\dist-client-setup\win-unpacked\resources"
}
$resourcesPath = [IO.Path]::GetFullPath($ResourcesRoot)
$localApp = Join-Path $resourcesPath "local_app"
$wheelDir = Join-Path $localApp "python_wheels"
$requirementsPath = Join-Path $localApp "requirements.txt"
$runtimeManifestPath = Join-Path $localApp "runtime-manifest.json"
$runtimeVersionsPath = Join-Path $localApp "runtime-versions.json"
$portablePython = Join-Path $localApp "python_runtime\portable\python.exe"
$provisioner = Join-Path $localApp "scripts\provision_python_runtime.py"

if (-not (Test-Path -LiteralPath $localApp -PathType Container)) {
    throw "Pacote local_app nao encontrado em $localApp"
}
if (-not (Test-Path -LiteralPath $requirementsPath -PathType Leaf)) {
    throw "requirements.txt nao encontrado no pacote."
}
if (-not (Test-Path -LiteralPath $wheelDir -PathType Container)) {
    throw "Wheelhouse offline nao encontrado no pacote."
}
if (-not (Test-Path -LiteralPath $runtimeManifestPath -PathType Leaf)) {
    throw "runtime-manifest.json nao encontrado no pacote."
}
if (-not (Test-Path -LiteralPath $runtimeVersionsPath -PathType Leaf)) {
    throw "runtime-versions.json nao encontrado no pacote."
}
if (-not (Test-Path -LiteralPath $portablePython -PathType Leaf)) {
    throw "Runtime Python portatil nao encontrado em $portablePython"
}
if (-not (Test-Path -LiteralPath $provisioner -PathType Leaf)) {
    throw "Provisionador Python nao encontrado em $provisioner"
}

$runtimeVersions = Get-Content -LiteralPath $runtimeVersionsPath -Raw | ConvertFrom-Json
if ([int]$runtimeVersions.schemaVersion -ne 1 -or -not $runtimeVersions.python) {
    throw "runtime-versions.json empacotado e invalido."
}
$expectedPythonVersion = [string]$runtimeVersions.python.version
$expectedPythonAbi = [string]$runtimeVersions.python.abi
$expectedPythonImplementation = [string]$runtimeVersions.python.implementation
if ($expectedPythonImplementation -notmatch '^[a-z]{2}$') {
    throw "Implementacao Python invalida em runtime-versions.json."
}
$expectedPythonBits = switch ([string]$runtimeVersions.python.windowsArchitecture) {
    "amd64" { 64 }
    "arm64" { 64 }
    "x86" { 32 }
    default { throw "Arquitetura Windows invalida em runtime-versions.json." }
}
$runtimeManifest = Get-Content -LiteralPath $runtimeManifestPath -Raw | ConvertFrom-Json
if ([string]$runtimeManifest.python.version -ne $expectedPythonVersion -or [string]$runtimeManifest.python.abi -ne $expectedPythonAbi) {
    throw "Manifesto Python diverge de runtime-versions.json."
}

$workBase = if ($WorkBase) {
    [IO.Path]::GetFullPath($WorkBase)
} else {
    [IO.Path]::GetFullPath((Join-Path ([IO.Path]::GetTempPath()) "jk-sistema-offline-smoke"))
}
New-Item -ItemType Directory -Force -Path $workBase | Out-Null
$workDir = [IO.Path]::GetFullPath((Join-Path $workBase ("jk99-" + [guid]::NewGuid().ToString("N").Substring(0, 16))))
$workPrefix = $workBase.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (
    -not $workDir.StartsWith($workPrefix, [StringComparison]::OrdinalIgnoreCase) -or
    -not ([IO.Path]::GetFullPath((Split-Path -Parent $workDir))).Equals(
        $workBase.TrimEnd([IO.Path]::DirectorySeparatorChar),
        [StringComparison]::OrdinalIgnoreCase
    )
) {
    throw "Diretorio temporario fora da base aprovada: $workDir"
}

$targetApp = Join-Path $workDir "local_app"
$infoDir = Join-Path $targetApp "info"
$logFile = Join-Path $targetApp "logs\python_runtime_provision.log"
$statusFile = Join-Path $infoDir "python-runtime-status.json"
$venvDir = Join-Path $targetApp ".venv"
$runtimeDir = Join-Path $targetApp ".python-runtime"
New-Item -ItemType Directory -Force -Path $targetApp, $infoDir | Out-Null
Write-Host "[offline-smoke] Area temporaria curta: $workDir"

try {
    Write-Host "[offline-smoke] Provisionando runtime e .venv apenas com os recursos do pacote..."
    $previousNoBytecode = $env:PYTHONDONTWRITEBYTECODE
    $previousNoIndex = $env:PIP_NO_INDEX
    $previousInfoDir = $env:JK_INFO_DIR
    $previousAppVersion = $env:JK_APP_VERSION
    $env:PYTHONDONTWRITEBYTECODE = "1"
    $env:PIP_NO_INDEX = "1"
    $env:JK_INFO_DIR = $infoDir
    $env:JK_APP_VERSION = [string]$runtimeManifest.version
    try {
        & $portablePython -B -I $provisioner `
            --source-root $localApp `
            --target-root $targetApp `
            --log-file $logFile `
            --lock-timeout 15 `
            --command-timeout 1800
        if ($LASTEXITCODE -ne 0) {
            $tail = if (Test-Path -LiteralPath $logFile) {
                (Get-Content -LiteralPath $logFile -Tail 80) -join [Environment]::NewLine
            } else { "log ausente" }
            throw "Provisionador retornou codigo $LASTEXITCODE.`n$tail"
        }
    } finally {
        $env:PYTHONDONTWRITEBYTECODE = $previousNoBytecode
        $env:PIP_NO_INDEX = $previousNoIndex
        $env:JK_INFO_DIR = $previousInfoDir
        $env:JK_APP_VERSION = $previousAppVersion
    }

    if (-not (Test-Path -LiteralPath $statusFile -PathType Leaf)) {
        throw "Status do provisionamento nao foi gravado em $statusFile"
    }
    $status = Get-Content -LiteralPath $statusFile -Raw | ConvertFrom-Json
    if ([string]$status.state -ne "ready") {
        throw "Provisionamento nao terminou pronto: $($status | ConvertTo-Json -Compress -Depth 5)"
    }
    if ([string]$status.python_version -ne $expectedPythonVersion -or [string]$status.python_abi -ne $expectedPythonAbi) {
        throw "Status final registrou Python/ABI incorretos."
    }

    $runtimePython = Join-Path $runtimeDir "python.exe"
    $venvPython = Join-Path $venvDir "Scripts\python.exe"
    $venvMarker = Join-Path $venvDir ".jk-venv-ready.json"
    foreach ($required in @($runtimePython, $venvPython, $venvMarker)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "Artefato provisionado ausente: $required"
        }
    }

    Write-Host "[offline-smoke] Validando Python exato, pip check, modulos criticos e backend empacotado..."
    $probeCode = "import json,platform,struct,sys; print(json.dumps({'version':platform.python_version(),'abi':'$expectedPythonImplementation'+str(sys.version_info.major)+str(sys.version_info.minor),'bits':struct.calcsize('P')*8}))"
    $probe = @(& $runtimePython -B -I -c $probeCode)
    if ($LASTEXITCODE -ne 0) { throw "Runtime Python copiado nao executa." }
    $probeData = [string]$probe[-1] | ConvertFrom-Json
    if ([string]$probeData.version -ne $expectedPythonVersion -or [string]$probeData.abi -ne $expectedPythonAbi -or [int]$probeData.bits -ne $expectedPythonBits) {
        throw "Runtime copiado tem versao/ABI inesperados."
    }
    & $venvPython -B -I -m pip --isolated check
    if ($LASTEXITCODE -ne 0) { throw "pip check encontrou dependencias inconsistentes." }

    $previousInfoDir = $env:JK_INFO_DIR
    $previousAppVersion = $env:JK_APP_VERSION
    $previousNoBytecode = $env:PYTHONDONTWRITEBYTECODE
    $env:JK_INFO_DIR = $infoDir
    $env:JK_APP_VERSION = [string]$runtimeManifest.version
    $env:PYTHONDONTWRITEBYTECODE = "1"
    Push-Location $localApp
    try {
        & $venvPython -B -I -c "import sys; sys.path.insert(0, '.'); import av, fastapi, faster_whisper, openai_codex, pandas, playwright, psycopg, selenium, uvicorn, websockets; import backend_api; assert getattr(backend_api, 'app', None) is not None; print('offline-imports-ok')"
        if ($LASTEXITCODE -ne 0) { throw "Falha ao importar modulos criticos/backend_api no pacote offline." }
    } finally {
        Pop-Location
        $env:JK_INFO_DIR = $previousInfoDir
        $env:JK_APP_VERSION = $previousAppVersion
        $env:PYTHONDONTWRITEBYTECODE = $previousNoBytecode
    }

    Write-Host "[offline-smoke] Validando idempotencia sem reconstruir a .venv..."
    $markerBefore = (Get-FileHash -LiteralPath $venvMarker -Algorithm SHA256).Hash
    $venvPythonBefore = (Get-FileHash -LiteralPath $venvPython -Algorithm SHA256).Hash
    & $portablePython -B -I $provisioner --source-root $localApp --target-root $targetApp --log-file $logFile --lock-timeout 15
    if ($LASTEXITCODE -ne 0) { throw "Segunda execucao idempotente falhou com codigo $LASTEXITCODE." }
    $status = Get-Content -LiteralPath $statusFile -Raw | ConvertFrom-Json
    if ([string]$status.state -ne "ready" -or [string]$status.action -ne "reused") {
        throw "Segunda execucao nao reutilizou o ambiente validado."
    }
    if ((Get-FileHash -LiteralPath $venvMarker -Algorithm SHA256).Hash -ne $markerBefore) {
        throw "Marcador da .venv foi alterado durante a execucao idempotente."
    }
    if ((Get-FileHash -LiteralPath $venvPython -Algorithm SHA256).Hash -ne $venvPythonBefore) {
        throw "Executavel da .venv foi alterado durante a execucao idempotente."
    }

    Write-Host "[offline-smoke] OK - runtime portatil, instalacao offline, backend e idempotencia aprovados."
} finally {
    if ($KeepWorkDir) {
        Write-Host "[offline-smoke] Area temporaria preservada em $workDir"
    } elseif ($workDir.StartsWith($workPrefix, [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $workDir)) {
        $item = Get-Item -LiteralPath $workDir -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Recusa ao remover reparse point temporario: $workDir"
        }
        Remove-Item -LiteralPath $workDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
