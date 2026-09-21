$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$rootDir = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$stageScript = Join-Path $rootDir "scripts\stage_desktop_release_artifacts.ps1"
$tempPrefix = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd("\") + "\"
$fixtureRoot = Join-Path $tempPrefix ("jk-release-staging-" + [guid]::NewGuid().ToString("N"))
$resolvedFixture = [IO.Path]::GetFullPath($fixtureRoot).TrimEnd("\") + "\"
if (-not $resolvedFixture.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Fixture fora do diretorio temporario permitido: $resolvedFixture"
}
New-Item -ItemType Directory -Path $fixtureRoot | Out-Null

function New-ProfileArtifacts {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,
        [Parameter(Mandatory = $true)]
        [string]$Executable,
        [Parameter(Mandatory = $true)]
        [string]$Version
    )

    New-Item -ItemType Directory -Path $Source | Out-Null
    Set-Content -LiteralPath (Join-Path $Source $Executable) -Value "fixture-exe" -Encoding ascii -NoNewline
    Set-Content -LiteralPath (Join-Path $Source "$Executable.blockmap") -Value "fixture-blockmap" -Encoding ascii -NoNewline
    Set-Content -LiteralPath (Join-Path $Source "latest.yml") -Value @(
        "version: $Version",
        "files:",
        "  - url: $Executable",
        "path: $Executable"
    ) -Encoding ascii
}

function Assert-Names {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Directory,
        [Parameter(Mandatory = $true)]
        [string[]]$Expected
    )

    $actual = @(Get-ChildItem -LiteralPath $Directory -File | ForEach-Object Name | Sort-Object)
    $expectedSorted = @($Expected | Sort-Object)
    if (($actual -join ",") -ne ($expectedSorted -join ",")) {
        throw "Arquivos divergentes em ${Directory}. Esperado: $($expectedSorted -join ', '); encontrado: $($actual -join ', ')"
    }
}

try {
    $version = "9.9.9"
    $setupExe = "JK-Sistema-Cliente-Setup-$version.exe"
    $updateExe = "JK-Sistema-Cliente-Update-$version.exe"
    $setupSource = Join-Path $fixtureRoot "setup"
    $updateSource = Join-Path $fixtureRoot "update"
    New-ProfileArtifacts -Source $setupSource -Executable $setupExe -Version $version
    New-ProfileArtifacts -Source $updateSource -Executable $updateExe -Version $version

    $appOutput = Join-Path $fixtureRoot "app-output"
    & $stageScript -Mode "app-update" -Version $version -SetupSource $setupSource -UpdateSource $updateSource -OutputDirectory $appOutput
    Assert-Names -Directory $appOutput -Expected @(
        $setupExe,
        "$setupExe.blockmap",
        $updateExe,
        "$updateExe.blockmap",
        "latest.yml",
        "SHA256SUMS.txt"
    )
    $appLatest = Get-Content -LiteralPath (Join-Path $appOutput "latest.yml") -Raw
    if ($appLatest -notmatch [regex]::Escape($updateExe) -or $appLatest -match [regex]::Escape($setupExe)) {
        throw "app-update nao preservou o latest.yml do Update leve."
    }
    $appChecksum = Join-Path $appOutput "SHA256SUMS.txt"
    if (@(Get-Content -LiteralPath $appChecksum).Count -ne 5) {
        throw "SHA256SUMS do app-update deve listar cinco artefatos."
    }
    $checksumBytes = [IO.File]::ReadAllBytes($appChecksum)
    if (
        $checksumBytes.Length -ge 3 -and
        $checksumBytes[0] -eq 0xEF -and
        $checksumBytes[1] -eq 0xBB -and
        $checksumBytes[2] -eq 0xBF
    ) {
        throw "SHA256SUMS do app-update contem BOM UTF-8."
    }

    $invalidUpdateSource = Join-Path $fixtureRoot "invalid-update"
    New-ProfileArtifacts -Source $invalidUpdateSource -Executable $updateExe -Version $version
    Set-Content -LiteralPath (Join-Path $invalidUpdateSource "latest.yml") -Value @(
        "version: $version",
        "files:",
        "  - url: $updateExe",
        "path: $setupExe"
    ) -Encoding ascii
    $invalidRejected = $false
    try {
        & $stageScript -Mode "app-update" -Version $version -SetupSource $setupSource -UpdateSource $invalidUpdateSource -OutputDirectory (Join-Path $fixtureRoot "invalid-output")
    }
    catch {
        $invalidRejected = $true
        if ($_.Exception.Message -notmatch "path principal") {
            throw
        }
    }
    if (-not $invalidRejected) {
        throw "Staging aceitou latest.yml com path principal incorreto."
    }

    $duplicateUpdateSource = Join-Path $fixtureRoot "duplicate-update"
    New-ProfileArtifacts -Source $duplicateUpdateSource -Executable $updateExe -Version $version
    Add-Content -LiteralPath (Join-Path $duplicateUpdateSource "latest.yml") -Value "path: $setupExe" -Encoding ascii
    $duplicateRejected = $false
    try {
        & $stageScript -Mode "app-update" -Version $version -SetupSource $setupSource -UpdateSource $duplicateUpdateSource -OutputDirectory (Join-Path $fixtureRoot "duplicate-output")
    }
    catch {
        $duplicateRejected = $true
        if ($_.Exception.Message -notmatch "exatamente uma ocorrencia de path principal") {
            throw
        }
    }
    if (-not $duplicateRejected) {
        throw "Staging aceitou latest.yml com path principal duplicado."
    }

    $runtimeOutput = Join-Path $fixtureRoot "runtime-output"
    & $stageScript -Mode "runtime-update" -Version $version -SetupSource $setupSource -UpdateSource $updateSource -OutputDirectory $runtimeOutput
    Assert-Names -Directory $runtimeOutput -Expected @(
        $setupExe,
        "$setupExe.blockmap",
        "latest.yml",
        "SHA256SUMS.txt"
    )
    $runtimeLatest = Get-Content -LiteralPath (Join-Path $runtimeOutput "latest.yml") -Raw
    if ($runtimeLatest -notmatch [regex]::Escape($setupExe)) {
        throw "runtime-update nao preservou o latest.yml do Setup completo."
    }
    if (@(Get-Content -LiteralPath (Join-Path $runtimeOutput "SHA256SUMS.txt")).Count -ne 3) {
        throw "SHA256SUMS do runtime-update deve listar tres artefatos."
    }

    Write-Host "electron release artifact staging: OK"
}
finally {
    $resolvedFixture = [IO.Path]::GetFullPath($fixtureRoot).TrimEnd("\") + "\"
    if (-not $resolvedFixture.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Recusa ao limpar fixture fora do diretorio temporario: $resolvedFixture"
    }
    if (Test-Path -LiteralPath $fixtureRoot) {
        Remove-Item -LiteralPath $fixtureRoot -Recurse -Force
    }
}
