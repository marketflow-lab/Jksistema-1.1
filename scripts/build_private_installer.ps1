param(
    [Parameter(Mandatory = $true)]
    [string]$CredentialSourceRoot,
    [string]$AdditionalCredentialSourceRoot = "",
    [string]$OutputRoot = "",
    [string]$PasswordFile = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = [IO.Path]::GetFullPath((Split-Path -Parent $scriptDir))
$electronDir = Join-Path $repoRoot "electron_app"
$package = Get-Content -LiteralPath (Join-Path $electronDir "package.json") -Raw | ConvertFrom-Json
$version = [string]$package.version
$privateBuildDir = Join-Path $electronDir "private_build"
$bundlePath = Join-Path $privateBuildDir "credentials.bundle.json"
$configPath = Join-Path $privateBuildDir "electron-builder.private.json"
$resolvedOutputRoot = if ($OutputRoot) {
    [IO.Path]::GetFullPath($OutputRoot)
} else {
    Join-Path $repoRoot ("private-release\v" + $version)
}
$resolvedPasswordFile = if ($PasswordFile) {
    [IO.Path]::GetFullPath($PasswordFile)
} else {
    Join-Path $resolvedOutputRoot "SENHA-COFRE-PRIVADO.txt"
}

$sourceRoot = [IO.Path]::GetFullPath($CredentialSourceRoot)
if (-not (Test-Path -LiteralPath $sourceRoot -PathType Container)) {
    throw "A origem principal das credenciais nao existe."
}

New-Item -ItemType Directory -Force -Path $privateBuildDir, $resolvedOutputRoot | Out-Null

$python = Join-Path $repoRoot "python_runtime\portable\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $pythonCommand = Get-Command python -ErrorAction Stop
    $python = $pythonCommand.Source
}

$bundleArgs = @(
    "-B",
    (Join-Path $scriptDir "build_private_credentials_bundle.py"),
    "--source-root", $sourceRoot,
    "--password-file", $resolvedPasswordFile,
    "--generate-password",
    "--output", $bundlePath
)
if ($AdditionalCredentialSourceRoot) {
    $additionalRoot = [IO.Path]::GetFullPath($AdditionalCredentialSourceRoot)
    if (Test-Path -LiteralPath $additionalRoot -PathType Container) {
        $bundleArgs += @("--source-root", $additionalRoot)
    }
}

Write-Host "[private-installer] Coletando somente chaves allowlisted e service accounts..."
& $python @bundleArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Push-Location $repoRoot
try {
    & node (Join-Path $scriptDir "verify-dependency-locks.js")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & npm.cmd --prefix electron_app run prepare:runtime
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & npm.cmd --prefix electron_app run verify:package
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & node (Join-Path $electronDir "scripts\create-private-builder-config.js") $bundlePath $resolvedOutputRoot $configPath
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Push-Location $electronDir
    try {
        & (Join-Path $electronDir "node_modules\.bin\electron-builder.cmd") --config $configPath
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } finally {
        Pop-Location
    }

    $resourcesRoot = Join-Path $resolvedOutputRoot "win-unpacked\resources"
    & node (Join-Path $electronDir "scripts\verify-installer-package.js") --packaged $resourcesRoot
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $packagedBundle = Join-Path $resourcesRoot "private_bootstrap\credentials.bundle.json"
    if (-not (Test-Path -LiteralPath $packagedBundle -PathType Leaf)) {
        throw "O cofre privado nao foi incorporado ao pacote."
    }
    $sourceHash = (Get-FileHash -LiteralPath $bundlePath -Algorithm SHA256).Hash
    $packagedHash = (Get-FileHash -LiteralPath $packagedBundle -Algorithm SHA256).Hash
    if ($sourceHash -ne $packagedHash) {
        throw "O hash do cofre privado divergiu durante o empacotamento."
    }

    $installer = Join-Path $resolvedOutputRoot ("JK-Sistema-Cliente-Privado-Setup-" + $version + ".exe")
    if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
        throw "O executavel privado esperado nao foi gerado."
    }
    $installerHash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash
    Write-Host ("[private-installer] OK version=" + $version + " sha256=" + $installerHash)
    Write-Host "[private-installer] A senha foi salva separadamente na pasta privada de saida."
} finally {
    Pop-Location
}
