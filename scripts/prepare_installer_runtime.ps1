param(
    [string]$WhisperModelSource = $env:JK_WHISPER_MODEL_SOURCE
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$pythonVersion = "3.11.9"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Split-Path -Parent $scriptDir
$packageJsonPath = Join-Path $rootDir "electron_app\package.json"
$requirementsPath = Join-Path $rootDir "requirements.txt"
$runtimeDir = Join-Path $rootDir "python_runtime"
$wheelDir = Join-Path $rootDir "python_wheels"
$stagingDir = Join-Path $rootDir ".installer_runtime"
$prerequisitesDir = Join-Path $stagingDir "prerequisites"
$blackJhonDir = Join-Path $stagingDir "black_jhon"
$stagedModelDir = Join-Path $blackJhonDir "faster-whisper-small"
$pythonInstaller = Join-Path $runtimeDir "python-$pythonVersion-amd64.exe"
$pythonInstallerUrl = "https://www.python.org/ftp/python/$pythonVersion/python-$pythonVersion-amd64.exe"
$vcInstaller = Join-Path $prerequisitesDir "VC_redist.x64.exe"
$vcInstallerUrl = "https://aka.ms/vc14/vc_redist.x64.exe"

function Assert-PathInside([string]$Path, [string]$Parent) {
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $fullParent = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
    if (-not $fullPath.StartsWith($fullParent, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Caminho fora da area permitida: $fullPath"
    }
}

function Assert-TrustedSignature([string]$Path, [string]$SubjectPattern) {
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    $subject = if ($signature.SignerCertificate) { [string]$signature.SignerCertificate.Subject } else { "" }
    if ($signature.Status -ne "Valid" -or $subject -notmatch $SubjectPattern) {
        throw "Assinatura invalida em $Path ($($signature.Status); $subject)."
    }
}

function Test-WhisperModel([string]$Path) {
    $manifestPath = Join-Path $Path "model-manifest.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { return $false }
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        $root = [System.IO.Path]::GetFullPath($Path).TrimEnd('\') + '\'
        $entries = @($manifest.files)
        if ($entries.Count -lt 1) { return $false }
        foreach ($entry in $entries) {
            $candidate = [System.IO.Path]::GetFullPath((Join-Path $Path ([string]$entry.path)))
            if (-not $candidate.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
            if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { return $false }
            $item = Get-Item -LiteralPath $candidate
            if ($item.Length -ne [int64]$entry.size) { return $false }
            $hash = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($hash -ne ([string]$entry.sha256).ToLowerInvariant()) { return $false }
        }
        return $true
    } catch {
        return $false
    }
}

if (-not (Test-Path -LiteralPath $requirementsPath -PathType Leaf)) {
    throw "Arquivo requirements.txt nao encontrado em $requirementsPath"
}
if (-not (Test-Path -LiteralPath $packageJsonPath -PathType Leaf)) {
    throw "Arquivo package.json do Electron nao encontrado em $packageJsonPath"
}
$appVersion = [string]((Get-Content -LiteralPath $packageJsonPath -Raw | ConvertFrom-Json).version)
if (-not $appVersion) {
    throw "Versao do aplicativo nao encontrada em $packageJsonPath"
}

New-Item -ItemType Directory -Force -Path $runtimeDir, $wheelDir, $prerequisitesDir, $blackJhonDir | Out-Null

if (-not (Test-Path -LiteralPath $pythonInstaller -PathType Leaf)) {
    Write-Host "Baixando Python $pythonVersion para o instalador..."
    Invoke-WebRequest -Uri $pythonInstallerUrl -OutFile $pythonInstaller -UseBasicParsing
}
Assert-TrustedSignature $pythonInstaller "Python Software Foundation"

if (-not (Test-Path -LiteralPath $vcInstaller -PathType Leaf)) {
    Write-Host "Baixando Visual C++ Redistributable x64..."
    Invoke-WebRequest -Uri $vcInstallerUrl -OutFile $vcInstaller -UseBasicParsing
}
Assert-TrustedSignature $vcInstaller "Microsoft"

$requirementsHash = (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256).Hash.ToLowerInvariant()
$prepareHash = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
$markerPath = Join-Path $wheelDir ".requirements-$requirementsHash-$prepareHash.ok"
$criticalPatterns = @("openai_codex-0.1.0b3-*.whl", "openai_codex_cli_bin-0.137.0a4-*.whl", "faster_whisper-1.2.1-*.whl")
$wheelhouseReady = Test-Path -LiteralPath $markerPath -PathType Leaf
foreach ($pattern in $criticalPatterns) {
    if (@(Get-ChildItem -LiteralPath $wheelDir -Filter $pattern -File -ErrorAction SilentlyContinue).Count -lt 1) {
        $wheelhouseReady = $false
    }
}

if (-not $wheelhouseReady) {
    Write-Host "Preparando dependencias offline para Windows/Python 3.11..."
    Get-ChildItem -LiteralPath $wheelDir -File -ErrorAction SilentlyContinue | Remove-Item -Force

    $pythonCommand = (Get-Command python -ErrorAction Stop).Source
    & $pythonCommand -m pip --version | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "pip local indisponivel." }

    $downloadArgs = @(
        "-m", "pip", "download", "--dest", $wheelDir, "--prefer-binary",
        "--only-binary=:all:", "--platform", "win_amd64", "--implementation", "cp",
        "--python-version", "3.11", "--abi", "cp311", "-r", $requirementsPath
    )
    & $pythonCommand @downloadArgs
    if ($LASTEXITCODE -ne 0) { throw "Falha ao baixar as dependencias offline." }

    $wheels = @(Get-ChildItem -LiteralPath $wheelDir -Filter "*.whl" -File)
    if ($wheels.Count -lt 20) { throw "Wheelhouse incompleto: $($wheels.Count) arquivos." }
    foreach ($pattern in $criticalPatterns) {
        if (@(Get-ChildItem -LiteralPath $wheelDir -Filter $pattern -File).Count -lt 1) {
            throw "Wheel critico ausente: $pattern"
        }
    }

    $wheelManifest = [ordered]@{
        requirements_sha256 = $requirementsHash
        generated_at = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
        wheels = @($wheels | Sort-Object Name | ForEach-Object {
            [ordered]@{
                name = $_.Name
                size = $_.Length
                sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        })
    }
    $wheelManifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $wheelDir "manifest.json") -Encoding UTF8
    "ok" | Set-Content -LiteralPath $markerPath -Encoding ASCII
}

if (-not (Test-WhisperModel $stagedModelDir)) {
    $candidates = @(
        $WhisperModelSource,
        (Join-Path $rootDir "info\ai_models\faster-whisper-small"),
        (Join-Path $env:APPDATA "JK Sistema Cliente\local_app\info\ai_models\faster-whisper-small")
    ) | Where-Object { $_ }
    $modelSource = $candidates | Where-Object { Test-WhisperModel $_ } | Select-Object -First 1
    if (-not $modelSource) {
        throw "Whisper Small valido nao encontrado. Defina JK_WHISPER_MODEL_SOURCE e execute novamente."
    }
    Assert-PathInside $stagedModelDir $stagingDir
    if (Test-Path -LiteralPath $stagedModelDir) { Remove-Item -LiteralPath $stagedModelDir -Recurse -Force }
    Copy-Item -LiteralPath $modelSource -Destination $stagedModelDir -Recurse -Force
}
if (-not (Test-WhisperModel $stagedModelDir)) { throw "Whisper Small empacotado falhou na verificacao SHA256." }

$buildManifest = [ordered]@{
    version = $appVersion
    python = [ordered]@{
        file = "python-$pythonVersion-amd64.exe"
        sha256 = (Get-FileHash -LiteralPath $pythonInstaller -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    visual_cpp = [ordered]@{
        file = "VC_redist.x64.exe"
        sha256 = (Get-FileHash -LiteralPath $vcInstaller -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    whisper = Get-Content -LiteralPath (Join-Path $stagedModelDir "model-manifest.json") -Raw | ConvertFrom-Json
}
$buildManifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $stagingDir "runtime-manifest.json") -Encoding UTF8

$wheelCount = @(Get-ChildItem -LiteralPath $wheelDir -Filter "*.whl" -File).Count
$modelBytes = (Get-ChildItem -LiteralPath $stagedModelDir -Recurse -File | Measure-Object Length -Sum).Sum
Write-Host "Runtime offline pronto: $wheelCount wheels; Whisper $([math]::Round($modelBytes / 1MB, 1)) MiB."
