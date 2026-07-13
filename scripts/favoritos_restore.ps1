[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateSet('Code', 'Data')]
    [string]$Mode = 'Code',
    [string]$ManifestPath = '',
    [switch]$SkipProcessCheck
)

$ErrorActionPreference = 'Stop'

if (-not $ManifestPath) {
    $scriptRoot = $PSScriptRoot
    if (-not $scriptRoot -and $MyInvocation.MyCommand.Path) {
        $scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
    }
    $ManifestPath = Join-Path $scriptRoot 'manifest.json'
}

function Resolve-NormalizedPath([string]$Path) {
    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
}

function Test-PathInside([string]$Path, [string]$Root) {
    $resolvedPath = Resolve-NormalizedPath $Path
    $resolvedRoot = Resolve-NormalizedPath $Root
    return $resolvedPath.Equals($resolvedRoot, [StringComparison]::OrdinalIgnoreCase) -or
        $resolvedPath.StartsWith($resolvedRoot + '\', [StringComparison]::OrdinalIgnoreCase)
}

function Get-Sha256([string]$Path) {
    $stream = $null
    $sha = $null
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        $sha = [System.Security.Cryptography.SHA256]::Create()
        $bytes = $sha.ComputeHash($stream)
        return ([System.BitConverter]::ToString($bytes)).Replace('-', '')
    } finally {
        if ($stream) { $stream.Dispose() }
        if ($sha) { $sha.Dispose() }
    }
}

if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
    throw "Manifesto de restauracao nao encontrado: $ManifestPath"
}

$manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$allowedRoots = @(
    $manifest.workspace,
    $manifest.installed_root,
    $manifest.package_root
) | Where-Object { $_ } | ForEach-Object { Resolve-NormalizedPath $_ }

if (-not $SkipProcessCheck) {
    $running = Get-CimInstance Win32_Process | Where-Object {
        $exe = [string]$_.ExecutablePath
        $cmd = [string]$_.CommandLine
        ($exe -and (Test-PathInside $exe $manifest.workspace) -and $_.Name -eq 'electron.exe') -or
        ($exe -and (Test-PathInside $exe $manifest.installed_root) -and $cmd -match 'uvicorn\s+(backend_api|promo_worker_api):app')
    }
    if ($running) {
        $ids = ($running | Select-Object -ExpandProperty ProcessId) -join ', '
        throw "Feche o JK Sistema e os backends antes de restaurar. Processos ativos: $ids"
    }
}

$categories = if ($Mode -eq 'Code') { @('repo', 'installed', 'package') } else { @('data') }
$entries = @($manifest.entries | Where-Object { $categories -contains $_.category })
if (-not $entries.Count) {
    throw "O manifesto nao contem entradas para o modo $Mode."
}

$preRestoreRoot = Join-Path (Split-Path -Parent $ManifestPath) ('pre_restore_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))

foreach ($entry in $entries) {
    $target = Resolve-NormalizedPath ([string]($entry.target))
    $allowed = $false
    foreach ($root in $allowedRoots) {
        if (Test-PathInside $target $root) {
            $allowed = $true
            break
        }
    }
    if (-not $allowed) {
        throw "Destino fora das raizes permitidas: $target"
    }

    if (Test-Path -LiteralPath $target -PathType Leaf) {
        $safeRel = ($entry.category + '\' + ($target -replace '^[A-Za-z]:[\\/]*', '' -replace '[:*?"<>|]', '_'))
        $preRestorePath = Join-Path $preRestoreRoot $safeRel
        if ($PSCmdlet.ShouldProcess($target, "Criar copia de seguranca em $preRestorePath")) {
            New-Item -ItemType Directory -Path (Split-Path -Parent $preRestorePath) -Force | Out-Null
            Copy-Item -LiteralPath $target -Destination $preRestorePath -Force
        }
    }

    if ([bool]($entry.existed)) {
        $backup = Join-Path (Split-Path -Parent $ManifestPath) ([string]($entry.backup))
        if (-not (Test-Path -LiteralPath $backup -PathType Leaf)) {
            throw "Arquivo do snapshot ausente: $backup"
        }
        $backupHash = Get-Sha256 $backup
        $expectedHash = [string]($entry.sha256)
        if ($expectedHash -and $backupHash -ne $expectedHash) {
            throw "Hash invalido no snapshot: backup=$backup category=$($entry.category) expected=$expectedHash actual=$backupHash existed=$($entry.existed)"
        }
        if ($PSCmdlet.ShouldProcess($target, "Restaurar de $backup")) {
            New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
            Copy-Item -LiteralPath $backup -Destination $target -Force
            $restoredHash = Get-Sha256 $target
            if ($restoredHash -ne $backupHash) {
                throw "Falha de verificacao apos restaurar: $target"
            }
        }
    } elseif (Test-Path -LiteralPath $target -PathType Leaf) {
        if ($PSCmdlet.ShouldProcess($target, 'Remover arquivo que nao existia no checkpoint')) {
            Remove-Item -LiteralPath $target -Force
        }
    }
}

[pscustomobject]@{
    success = $true
    mode = $Mode
    restored_entries = $entries.Count
    pre_restore_backup = $preRestoreRoot
    manifest = (Resolve-NormalizedPath $ManifestPath)
} | ConvertTo-Json -Compress
