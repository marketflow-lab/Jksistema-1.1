[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("app-update", "runtime-update")]
    [string]$Mode,
    [string]$Version = "",
    [string]$SetupSource = "",
    [string]$UpdateSource = "",
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$rootDir = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($Version)) {
    $packagePath = Join-Path $rootDir "electron_app\package.json"
    $package = Get-Content -LiteralPath $packagePath -Raw | ConvertFrom-Json
    $Version = [string]$package.version
}
if ([string]::IsNullOrWhiteSpace($Version)) {
    throw "Versao do pacote ausente."
}
if ([string]::IsNullOrWhiteSpace($SetupSource)) {
    $SetupSource = Join-Path $rootDir "electron_app\dist-client-setup"
}
if ([string]::IsNullOrWhiteSpace($UpdateSource)) {
    $UpdateSource = Join-Path $rootDir "electron_app\dist-client-update"
}
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $rootDir "release-artifacts"
}

$SetupSource = [IO.Path]::GetFullPath($SetupSource)
$UpdateSource = [IO.Path]::GetFullPath($UpdateSource)
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$setupExe = "JK-Sistema-Cliente-Setup-$($Version).exe"
$updateExe = "JK-Sistema-Cliente-Update-$($Version).exe"

function Assert-ProfileArtifacts {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,
        [Parameter(Mandatory = $true)]
        [string]$Executable
    )

    $expected = @($Executable, "$Executable.blockmap", "latest.yml") | Sort-Object
    $actual = @(
        Get-ChildItem -LiteralPath $Source -File |
            Where-Object {
                $_.Extension -in @(".exe", ".blockmap") -or $_.Name -eq "latest.yml"
            } |
            ForEach-Object Name |
            Sort-Object
    )
    if (($actual -join ",") -ne ($expected -join ",")) {
        throw "Artefatos inesperados em ${Source}. Esperado: $($expected -join ', '); encontrado: $($actual -join ', ')"
    }
}

function Assert-SingleManifestValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Content,
        [Parameter(Mandatory = $true)]
        [string]$Pattern,
        [Parameter(Mandatory = $true)]
        [string]$Expected,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    $matches = @([regex]::Matches($Content, $Pattern))
    if ($matches.Count -ne 1) {
        throw "latest.yml deve conter exatamente uma ocorrencia de $Label; encontrado: $($matches.Count)."
    }
    $actual = [string]$matches[0].Groups[1].Value
    if ($actual -cne $Expected) {
        throw "latest.yml declara $Label '$actual'; esperado: '$Expected'."
    }
}

Assert-ProfileArtifacts -Source $SetupSource -Executable $setupExe
if ($Mode -eq "app-update") {
    Assert-ProfileArtifacts -Source $UpdateSource -Executable $updateExe
}

if (Test-Path -LiteralPath $OutputDirectory) {
    throw "Diretorio de staging ja existe: $OutputDirectory"
}
New-Item -ItemType Directory -Path $OutputDirectory | Out-Null

$selected = @(
    (Join-Path $SetupSource $setupExe),
    (Join-Path $SetupSource "$setupExe.blockmap")
)
$latestSource = Join-Path $SetupSource "latest.yml"
$expectedNames = @($setupExe, "$setupExe.blockmap", "latest.yml")
$manifestExecutable = $setupExe

if ($Mode -eq "app-update") {
    $selected += @(
        (Join-Path $UpdateSource $updateExe),
        (Join-Path $UpdateSource "$updateExe.blockmap")
    )
    $latestSource = Join-Path $UpdateSource "latest.yml"
    $expectedNames += @($updateExe, "$updateExe.blockmap")
    $manifestExecutable = $updateExe
}

foreach ($file in $selected) {
    Copy-Item -LiteralPath $file -Destination (Join-Path $OutputDirectory (Split-Path $file -Leaf))
}
Copy-Item -LiteralPath $latestSource -Destination (Join-Path $OutputDirectory "latest.yml")

$latestPath = Join-Path $OutputDirectory "latest.yml"
$latest = Get-Content -LiteralPath $latestPath -Raw
Assert-SingleManifestValue -Content $latest -Pattern '(?m)^version:\s*[''"]?([^''"\s]+)[''"]?\s*$' -Expected $Version -Label "version"
Assert-SingleManifestValue -Content $latest -Pattern '(?m)^path:\s*[''"]?([^''"\s]+)[''"]?\s*$' -Expected $manifestExecutable -Label "path principal"
Assert-SingleManifestValue -Content $latest -Pattern '(?m)^\s*-\s+url:\s*[''"]?([^''"\s]+)[''"]?\s*$' -Expected $manifestExecutable -Label "files.url"
if ($Mode -eq "app-update" -and $latest -match [regex]::Escape($setupExe)) {
    throw "latest.yml do app-update aponta indevidamente para o Setup completo."
}

$actualNames = @(
    Get-ChildItem -LiteralPath $OutputDirectory -File |
        ForEach-Object Name |
        Sort-Object
)
$expectedNames = @($expectedNames | Sort-Object)
if (($actualNames -join ",") -ne ($expectedNames -join ",")) {
    throw "Staging divergente. Esperado: $($expectedNames -join ', '); encontrado: $($actualNames -join ', ')"
}

$hashLines = @(
    Get-ChildItem -LiteralPath $OutputDirectory -File |
        Sort-Object Name |
        ForEach-Object {
            $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            "$hash  $($_.Name)"
        }
)
$checksumPath = Join-Path $OutputDirectory "SHA256SUMS.txt"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllLines($checksumPath, [string[]]$hashLines, $utf8NoBom)
Write-Host "Artefatos de release preparados para $Mode em $OutputDirectory"
