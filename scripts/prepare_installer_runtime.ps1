$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$pythonVersion = "3.11.9"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Split-Path -Parent $scriptDir
$requirementsPath = Join-Path $rootDir "requirements.txt"
$runtimeDir = Join-Path $rootDir "python_runtime"
$wheelDir = Join-Path $rootDir "python_wheels"
$pythonInstaller = Join-Path $runtimeDir "python-$pythonVersion-amd64.exe"
$pythonInstallerUrl = "https://www.python.org/ftp/python/$pythonVersion/python-$pythonVersion-amd64.exe"

if (-not (Test-Path $requirementsPath -PathType Leaf)) {
    throw "Arquivo requirements.txt nao encontrado em $requirementsPath"
}

New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
New-Item -ItemType Directory -Force -Path $wheelDir | Out-Null

if (-not (Test-Path $pythonInstaller -PathType Leaf)) {
    Write-Host "Baixando Python $pythonVersion para empacotar no instalador..."
    Invoke-WebRequest -Uri $pythonInstallerUrl -OutFile $pythonInstaller -UseBasicParsing
} else {
    Write-Host "Python $pythonVersion ja preparado em python_runtime."
}

$requirementsHash = (Get-FileHash -Path $requirementsPath -Algorithm SHA256).Hash.ToLowerInvariant()
$markerPath = Join-Path $wheelDir ".requirements-$requirementsHash.ok"
$existingWheels = @(Get-ChildItem -Path $wheelDir -Filter "*.whl" -File -ErrorAction SilentlyContinue)

if ((Test-Path $markerPath -PathType Leaf) -and $existingWheels.Count -gt 0) {
    Write-Host "Dependencias offline ja preparadas em python_wheels ($($existingWheels.Count) arquivos)."
    exit 0
}

Write-Host "Preparando dependencias offline para Windows/Python 3.11..."
Get-ChildItem -Path $wheelDir -Filter "*.whl" -File -ErrorAction SilentlyContinue | Remove-Item -Force
Get-ChildItem -Path $wheelDir -Filter ".requirements-*.ok" -File -ErrorAction SilentlyContinue | Remove-Item -Force

$pythonCommand = $null
$pythonArgsPrefix = @()
$pythonExe = Get-Command python -ErrorAction SilentlyContinue
$pyLauncher = Get-Command py -ErrorAction SilentlyContinue

if ($pythonExe) {
    $pythonCommand = $pythonExe.Source
} elseif ($pyLauncher) {
    $pythonCommand = $pyLauncher.Source
    $pythonArgsPrefix = @("-3")
} else {
    throw "Nenhum Python local encontrado para baixar as dependencias do instalador."
}

& $pythonCommand @pythonArgsPrefix -m pip --version | Out-Host
if ($LASTEXITCODE -ne 0) {
    & $pythonCommand @pythonArgsPrefix -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) {
        throw "Nao foi possivel habilitar o pip no Python local."
    }
}

$downloadArgs = @(
    "-m", "pip", "download",
    "--dest", $wheelDir,
    "--prefer-binary",
    "--only-binary=:all:",
    "--platform", "win_amd64",
    "--implementation", "cp",
    "--python-version", "3.11",
    "--abi", "cp311",
    "-r", $requirementsPath
)

& $pythonCommand @pythonArgsPrefix @downloadArgs
if ($LASTEXITCODE -ne 0) {
    throw "Falha ao baixar as dependencias offline para o instalador."
}

$wheelCount = @(Get-ChildItem -Path $wheelDir -Filter "*.whl" -File -ErrorAction SilentlyContinue).Count
if ($wheelCount -le 0) {
    throw "Nenhuma dependencia offline foi gerada em python_wheels."
}

"ok" | Set-Content -Path $markerPath -Encoding ASCII
Write-Host "Dependencias offline preparadas: $wheelCount arquivos em python_wheels."
