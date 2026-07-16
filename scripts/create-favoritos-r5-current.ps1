[CmdletBinding()]
param(
    [string]$DestinationRoot = '',
    [string]$WorkspaceRoot = '',
    [string]$InstalledRoot = '',
    [string]$PackageRoot = '',
    [string]$TemplateR5Root = '',
    [ValidatePattern('^R\d+$')]
    [string]$Checkpoint = 'R5',
    [string]$PreviousCheckpoint = '',
    [string[]]$PlannedAbsentPaths = @(
        'static/favoritos/v2/coleta/enrichment-cache.js',
        'tests/favoritos_enrichment_dedup.js'
    )
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

function Resolve-NormalizedPath([string]$Path) {
    return [IO.Path]::GetFullPath($Path).TrimEnd('\')
}

function Test-PathInside([string]$Path, [string]$Root) {
    $resolvedPath = Resolve-NormalizedPath $Path
    $resolvedRoot = Resolve-NormalizedPath $Root
    return $resolvedPath.Equals($resolvedRoot, [StringComparison]::OrdinalIgnoreCase) -or
        $resolvedPath.StartsWith($resolvedRoot + '\', [StringComparison]::OrdinalIgnoreCase)
}

function Assert-SafeRelativePath([string]$RelativePath) {
    if ([string]::IsNullOrWhiteSpace($RelativePath) -or [IO.Path]::IsPathRooted($RelativePath)) {
        throw "Caminho relativo invalido: $RelativePath"
    }
    $parts = $RelativePath.Replace('/', '\').Split('\')
    if ($parts -contains '..' -or $parts -contains '.') {
        throw "Caminho relativo inseguro: $RelativePath"
    }
}

function Join-SafePath([string]$Root, [string]$RelativePath) {
    Assert-SafeRelativePath $RelativePath
    $joined = Resolve-NormalizedPath (Join-Path $Root $RelativePath)
    if (-not (Test-PathInside $joined $Root)) {
        throw "Caminho saiu da raiz permitida: $RelativePath"
    }
    return $joined
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
}

function Get-MtimeNs([IO.FileInfo]$Item) {
    return ([int64]$Item.LastWriteTimeUtc.Ticks - 621355968000000000L) * 100L
}

function Write-Utf8Json([string]$Path, $Value, [int]$Depth = 30) {
    $json = ($Value | ConvertTo-Json -Depth $Depth) + [Environment]::NewLine
    [IO.File]::WriteAllText($Path, $json, [Text.UTF8Encoding]::new($false))
}

function Get-EntryTarget($Entry, $Roots) {
    $relative = [string]$Entry.relative_path
    switch ([string]$Entry.category) {
        'repo' { return Join-SafePath $Roots.workspace $relative }
        'installed' { return Join-SafePath $Roots.installed $relative }
        'package' { return Join-SafePath $Roots.package $relative }
        'data' { return Join-SafePath (Join-Path $Roots.installed 'info') $relative }
        default { throw "Categoria desconhecida: $($Entry.category)" }
    }
}

function New-SnapshotEntry(
    [string]$Category,
    [string]$Scope,
    [string]$RelativePath,
    [string]$Target,
    [string]$BackupRelative,
    [string]$BuildingRoot
) {
    $relative = $RelativePath.Replace('\', '/')
    Assert-SafeRelativePath $relative
    $backup = Join-SafePath $BuildingRoot $BackupRelative
    $exists = Test-Path -LiteralPath $Target -PathType Leaf
    if ($exists) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $backup) -Force | Out-Null
        Copy-Item -LiteralPath $Target -Destination $backup -Force
        $sourceHash = Get-Sha256 $Target
        $backupHash = Get-Sha256 $backup
        if ($sourceHash -ne $backupHash) {
            throw "Hash divergente durante a captura: $Target"
        }
        $file = Get-Item -LiteralPath $backup
        return [pscustomobject][ordered]@{
            category = $Category
            scope = $Scope
            relative_path = $relative
            target = Resolve-NormalizedPath $Target
            backup = $BackupRelative.Replace('\', '/')
            existed = $true
            length = [int64]$file.Length
            mtime_ns = Get-MtimeNs $file
            sha256 = $backupHash
        }
    }
    return [pscustomobject][ordered]@{
        category = $Category
        scope = $Scope
        relative_path = $relative
        target = Resolve-NormalizedPath $Target
        backup = $BackupRelative.Replace('\', '/')
        existed = $false
        length = $null
        mtime_ns = $null
        sha256 = $null
    }
}

function Assert-AppStopped([string]$Workspace, [string]$Installed) {
    $workspacePattern = [regex]::Escape((Resolve-NormalizedPath $Workspace))
    $profilePattern = [regex]::Escape((Resolve-NormalizedPath (Split-Path -Parent $Installed)))
    $running = @(Get-CimInstance Win32_Process | Where-Object {
        $name = [string]$_.Name
        $cmd = [string]$_.CommandLine
        (($name -eq 'electron.exe') -and ($cmd -match $workspacePattern -or $cmd -match $profilePattern)) -or
        (($name -eq 'python.exe') -and $cmd -match 'uvicorn\s+(backend_api|promo_worker_api):app' -and
            ($cmd -match $workspacePattern -or $cmd -match '--port\s+(8001|8011)'))
    })
    $listeners = @()
    foreach ($port in @(8001, 8011)) {
        $listeners += @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
    }
    if ($running.Count -or $listeners.Count) {
        $ids = @($running | ForEach-Object ProcessId) + @($listeners | ForEach-Object OwningProcess) |
            Sort-Object -Unique
        throw "Captura recusada: feche o Electron e os backends desta instancia. Processos/portas ativos: $($ids -join ', ')"
    }
}

function Invoke-SandboxValidation([string]$CheckpointRoot, $Manifest, [string]$CheckpointName) {
    $tempRoot = Resolve-NormalizedPath ([IO.Path]::GetTempPath())
    $sandboxPrefix = 'JK-Favoritos-' + $CheckpointName + '-validation-'
    $sandboxRoot = Join-Path $tempRoot ($sandboxPrefix + [guid]::NewGuid().ToString('N'))
    $sandboxRoot = Resolve-NormalizedPath $sandboxRoot
    if (-not $sandboxRoot.StartsWith($tempRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or
        -not (Split-Path -Leaf $sandboxRoot).StartsWith($sandboxPrefix, [StringComparison]::Ordinal)) {
        throw "Raiz de sandbox insegura: $sandboxRoot"
    }

    $roots = [pscustomobject]@{
        workspace = Join-Path $sandboxRoot 'targets\repo'
        installed = Join-Path $sandboxRoot 'targets\installed'
        package = Join-Path $sandboxRoot 'targets\package'
    }
    foreach ($root in @($roots.workspace, $roots.installed, $roots.package)) {
        New-Item -ItemType Directory -Path $root -Force | Out-Null
        [IO.File]::WriteAllText((Join-Path $root '__unlisted_canary.txt'), 'preserve-me', [Text.UTF8Encoding]::new($false))
    }

    $report = [ordered]@{
        success = $false
        checkpoint = $CheckpointName
        started_at = (Get-Date).ToString('o')
        sandbox_root = $sandboxRoot
        code_entries = 0
        data_entries = 0
        absent_entries_removed = 0
        canaries_preserved = $false
        sandbox_deleted = $false
    }

    try {
        foreach ($entry in @($Manifest.entries)) {
            $target = Get-EntryTarget $entry $roots
            New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
            [IO.File]::WriteAllText($target, ('sentinel-' + [guid]::NewGuid().ToString('N')), [Text.UTF8Encoding]::new($false))
        }

        $restore = Join-Path $CheckpointRoot 'restore.ps1'
        $manifestPath = Join-Path $CheckpointRoot 'manifest.json'
        $common = @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $restore,
            '-ManifestPath', $manifestPath,
            '-WorkspaceRootOverride', $roots.workspace,
            '-InstalledRootOverride', $roots.installed,
            '-PackageRootOverride', $roots.package,
            '-SandboxRoot', $sandboxRoot,
            '-SkipProcessCheck'
        )

        $codeOutput = & powershell.exe @common -Mode Code 2>&1
        if ($LASTEXITCODE -ne 0) { throw "Restore Code falhou na sandbox: $($codeOutput -join ' ')" }
        foreach ($scope in @('Auth', 'Favoritos', 'Catalog', 'AvantPro')) {
            if (@($Manifest.entries | Where-Object { $_.category -eq 'data' -and $_.scope -eq $scope }).Count -eq 0) { continue }
            $dataOutput = & powershell.exe @common -Mode Data -DataScope $scope 2>&1
            if ($LASTEXITCODE -ne 0) { throw "Restore Data/$scope falhou na sandbox: $($dataOutput -join ' ')" }
        }

        foreach ($entry in @($Manifest.entries)) {
            $target = Get-EntryTarget $entry $roots
            if ([bool]$entry.existed) {
                if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw "Arquivo nao restaurado na sandbox: $target" }
                if ((Get-Sha256 $target) -ne ([string]$entry.sha256).ToUpperInvariant()) {
                    throw "Hash divergente na sandbox: $target"
                }
            } elseif (Test-Path -LiteralPath $target) {
                throw "Marcador de ausencia nao foi respeitado: $target"
            } else {
                $report.absent_entries_removed++
            }
            if ([string]$entry.category -eq 'data') { $report.data_entries++ } else { $report.code_entries++ }
        }
        foreach ($root in @($roots.workspace, $roots.installed, $roots.package)) {
            $canary = Join-Path $root '__unlisted_canary.txt'
            if ((Get-Content -LiteralPath $canary -Raw) -ne 'preserve-me') { throw "Canario alterado: $canary" }
        }
        $report.canaries_preserved = $true
        $report.success = $true
    } finally {
        $report.finished_at = (Get-Date).ToString('o')
        if ($report.success -and (Test-Path -LiteralPath $sandboxRoot)) {
            $resolved = Resolve-NormalizedPath $sandboxRoot
            if ($resolved.StartsWith($tempRoot + '\', [StringComparison]::OrdinalIgnoreCase) -and
                (Split-Path -Leaf $resolved).StartsWith($sandboxPrefix, [StringComparison]::Ordinal)) {
                Remove-Item -LiteralPath $resolved -Recurse -Force
                $report.sandbox_deleted = -not (Test-Path -LiteralPath $resolved)
            }
        }
        Write-Utf8Json (Join-Path $CheckpointRoot 'validation_report.json') $report 20
    }
    if (-not $report.success -or -not $report.sandbox_deleted) {
        throw "Validacao sandbox do $CheckpointName nao foi concluida com limpeza segura."
    }
    return [pscustomobject]$report
}

if (-not $WorkspaceRoot) { $WorkspaceRoot = Split-Path -Parent $PSScriptRoot }
if (-not $InstalledRoot) { $InstalledRoot = Join-Path $env:APPDATA 'JK Sistema Cliente\local_app' }
if (-not $PackageRoot) {
    $PackageRoot = Join-Path $WorkspaceRoot 'electron_app\dist-client-setup\win-unpacked\resources\local_app'
}
if (-not $TemplateR5Root) {
    $TemplateR5Root = Join-Path $env:LOCALAPPDATA 'JK-Sistema-Restore\Favoritos\20260710_100651\R5'
}
if (-not $DestinationRoot) {
    $DestinationRoot = Join-Path $env:LOCALAPPDATA ('JK-Sistema-Restore\Favoritos\' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '\' + $Checkpoint)
}

$WorkspaceRoot = Resolve-NormalizedPath $WorkspaceRoot
$InstalledRoot = Resolve-NormalizedPath $InstalledRoot
$PackageRoot = Resolve-NormalizedPath $PackageRoot
$TemplateR5Root = Resolve-NormalizedPath $TemplateR5Root
$DestinationRoot = Resolve-NormalizedPath $DestinationRoot
$infoRoot = Join-Path $InstalledRoot 'info'

if (Test-Path -LiteralPath $DestinationRoot) { throw "Destino ja existe; sobrescrita recusada: $DestinationRoot" }
foreach ($required in @($WorkspaceRoot, $InstalledRoot, $PackageRoot, $infoRoot)) {
    if (-not (Test-Path -LiteralPath $required -PathType Container)) { throw "Raiz obrigatoria ausente: $required" }
}
$templateManifestPath = Join-Path $TemplateR5Root 'manifest.json'
$templateRestorePath = Join-Path $TemplateR5Root 'restore.ps1'
if (-not (Test-Path -LiteralPath $templateManifestPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $templateRestorePath -PathType Leaf)) {
    throw "Template R5 incompleto: $TemplateR5Root"
}

Assert-AppStopped $WorkspaceRoot $InstalledRoot

$syncScript = Join-Path $WorkspaceRoot 'scripts\sync-favoritos-runtime.js'
$parityOutput = & node $syncScript --check --all-targets 2>&1
if ($LASTEXITCODE -ne 0) { throw "Paridade recusada: $($parityOutput -join ' ')" }

$parent = Split-Path -Parent $DestinationRoot
New-Item -ItemType Directory -Path $parent -Force | Out-Null
$buildingRoot = $DestinationRoot + '.building-' + [guid]::NewGuid().ToString('N')
New-Item -ItemType Directory -Path $buildingRoot -Force | Out-Null

# Restringe o snapshot, que contem configuracoes de autenticacao, ao usuario atual.
$principal = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }
$aclOutput = & icacls.exe $buildingRoot /inheritance:r /grant:r "${principal}:(OI)(CI)F" /c 2>&1
if ($LASTEXITCODE -ne 0) { throw "Falha ao restringir ACL do ${Checkpoint}: $($aclOutput -join ' ')" }

$assetManifestPath = Join-Path $WorkspaceRoot 'static\favoritos\asset-manifest.json'
$assetManifest = Get-Content -LiteralPath $assetManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$runtimeFixed = @(
    'electron_shell.html', 'favoritos.html', 'static/favoritos.html', 'static/favoritos/asset-manifest.json',
    'backend_api.py', 'backend/schemas/__init__.py', 'backend/schemas/favoritos.py', 'backend/routers/favoritos.py',
    'backend/services/favoritos.py', 'backend/services/favoritos_busca.py', 'backend/services/favoritos_core.py',
    'backend/services/favoritos_endpoints.py', 'backend/services/favoritos_extract.py', 'backend/services/favoritos_jobs.py',
    'backend/services/favoritos_margem.py', 'backend/services/favoritos_ml.py',
    'backend/services/favoritos_planilhas_colar.py', 'backend/services/favoritos_ranking_ia.py',
    'backend/services/favoritos_storage.py', 'backend/services/shared_sync_merge_user_data.py',
    'backend/services/promocoes_api.py', 'backend/services/promocoes_api_analise.py',
    'backend/services/promocoes_api_jobs.py', 'backend/services/promocoes_api_participacoes.py',
    'backend/services/promocoes_common.py', 'backend/services/promocoes_core_custos.py',
    'electron_app/installer-required-resources.json', 'electron_app/package.json', 'electron_app/preload.js',
    'electron_app/main/modules/local-app-paths.js', 'electron_app/main/modules/backend.js',
    'electron_app/main/modules/ipc.js', 'electron_app/main/modules/favoritos-worker-browser.js'
)
$runtimePaths = @($runtimeFixed + @($assetManifest.assets | ForEach-Object { 'static/' + [string]$_ })) |
    ForEach-Object { $_.Replace('\', '/') } | Sort-Object -Unique

$repoExtras = @(
    'scripts/favoritos_restore.ps1', 'scripts/sync-favoritos-runtime.js', 'scripts/sync-static-html-mirrors.js',
    'scripts/create-favoritos-r5-current.ps1', 'electron_app/scripts/verify-installer-package.js'
)
$testsRoot = Join-Path $WorkspaceRoot 'tests'
if (Test-Path -LiteralPath $testsRoot -PathType Container) {
    $repoExtras += @(Get-ChildItem -LiteralPath $testsRoot -File -Force | Where-Object {
        $_.Name -match '(?i)favoritos|promocoes'
    } | ForEach-Object { 'tests/' + $_.Name })
}
$repoPaths = @($runtimePaths + $repoExtras + $PlannedAbsentPaths) |
    ForEach-Object { ([string]$_).Replace('\', '/') } | Sort-Object -Unique

$entries = [Collections.Generic.List[object]]::new()
foreach ($relative in $repoPaths) {
    $target = Join-SafePath $WorkspaceRoot $relative
    $backup = Join-Path 'code\repo' $relative
    $entries.Add((New-SnapshotEntry 'repo' 'Code' $relative $target $backup $buildingRoot))
}
foreach ($category in @('installed', 'package')) {
    $root = if ($category -eq 'installed') { $InstalledRoot } else { $PackageRoot }
    foreach ($relative in $runtimePaths) {
        $target = Join-SafePath $root $relative
        $backup = Join-Path ("code\$category") $relative
        $entries.Add((New-SnapshotEntry $category 'Code' $relative $target $backup $buildingRoot))
    }
}

$templateManifest = Get-Content -LiteralPath $templateManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$dataByRelative = @{}
foreach ($entry in @($templateManifest.entries | Where-Object { $_.category -eq 'data' })) {
    $key = ([string]$entry.relative_path).Replace('\', '/').ToLowerInvariant()
    $dataByRelative[$key] = [pscustomobject]@{ relative = ([string]$entry.relative_path).Replace('\', '/'); scope = [string]$entry.scope }
}
foreach ($file in @(Get-ChildItem -LiteralPath $infoRoot -File -Recurse -Force -ErrorAction Stop)) {
    $relative = $file.FullName.Substring($infoRoot.Length + 1).Replace('\', '/')
    $leaf = $file.Name
    $scope = $null
    if ($leaf -match '^(?i:auth_users\.db(?:-wal|-shm)?|bling_conf\.json|bling_contas\.json|credentials\.json|integracoes\.json|jwt_secret\.key|usuarios_cache\.json|usuarios_local\.json)$' -or
        $leaf -match '^(?i:lojas_config.*\.json(?:\.bak)?)$') {
        $scope = 'Auth'
    } elseif ($leaf -match '^(?i:favoritos_)' -or
        $relative -match '(?i)(^|/)favoritos_(jobs|decisoes_ia)/') {
        $scope = 'Favoritos'
    } elseif ($leaf -match '^(?i:cadastro_produtos|cadastro_custos_lojas)') {
        $scope = 'Catalog'
    } elseif ($relative -match '^(?i:electron_user_data/_avantpro_storage_last_good/)') {
        $scope = 'AvantPro'
    }
    if ($scope) {
        $dataByRelative[$relative.ToLowerInvariant()] = [pscustomobject]@{ relative = $relative; scope = $scope }
    }
}
foreach ($item in @($dataByRelative.Values | Sort-Object relative)) {
    $target = Join-SafePath $infoRoot $item.relative
    $backup = Join-Path ("data\$($item.scope)") $item.relative
    $entries.Add((New-SnapshotEntry 'data' $item.scope $item.relative $target $backup $buildingRoot))
}

Copy-Item -LiteralPath $templateRestorePath -Destination (Join-Path $buildingRoot 'restore.ps1') -Force
$restoreBuildingPath = Join-Path $buildingRoot 'restore.ps1'
if ($Checkpoint -ne 'R5') {
    $restoreText = [IO.File]::ReadAllText($restoreBuildingPath, [Text.Encoding]::UTF8)
    $guardR5 = " -ne 'R5') {"
    if (-not $restoreText.Contains($guardR5)) {
        throw 'Guard de checkpoint R5 nao foi encontrado na copia do restaurador.'
    }
    $restoreText = $restoreText.Replace($guardR5, " -ne '$Checkpoint') {")
    $restoreText = $restoreText.Replace('manifesto R5 schema_version 2', "manifesto $Checkpoint schema_version 2")
    [IO.File]::WriteAllText($restoreBuildingPath, $restoreText, [Text.UTF8Encoding]::new($false))
}
Copy-Item -LiteralPath $assetManifestPath -Destination (Join-Path $buildingRoot 'asset-manifest.json') -Force

$errorActionAnteriorGit = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$statusRaw = @(& git -C $WorkspaceRoot status --short 2>&1)
$statusExitCode = $LASTEXITCODE
$ErrorActionPreference = $errorActionAnteriorGit
if ($statusExitCode -ne 0) { throw "git status falhou: $($statusRaw -join ' ')" }
$statusLines = @($statusRaw | ForEach-Object { [string]$_ } | Where-Object { $_ -notmatch '(?i)warning:.*permission denied' })
[IO.File]::WriteAllLines((Join-Path $buildingRoot 'git_status.txt'), $statusLines, [Text.UTF8Encoding]::new($false))
$patchPath = Join-Path $buildingRoot 'working_tree.patch'
$ErrorActionPreference = 'Continue'
& git -C $WorkspaceRoot diff --binary --full-index HEAD "--output=$patchPath" -- . 2>&1 | Out-Null
$diffExitCode = $LASTEXITCODE
$ErrorActionPreference = $errorActionAnteriorGit
if ($diffExitCode -ne 0) { throw 'Falha ao gerar working_tree.patch.' }

$ErrorActionPreference = 'Continue'
$untrackedRaw = @(& git -C $WorkspaceRoot ls-files --others --exclude-standard 2>&1)
$untrackedExitCode = $LASTEXITCODE
$ErrorActionPreference = $errorActionAnteriorGit
if ($untrackedExitCode -ne 0) { throw "git ls-files falhou: $($untrackedRaw -join ' ')" }
$untrackedAll = @($untrackedRaw | ForEach-Object { [string]$_ } | Where-Object { $_ -notmatch '(?i)warning:.*permission denied' })
$repoSet = @{}
foreach ($relative in $repoPaths) { $repoSet[$relative.ToLowerInvariant()] = $true }
$untrackedTargets = @($untrackedAll | ForEach-Object { $_.Replace('\', '/') } | Where-Object {
    $repoSet.ContainsKey($_.ToLowerInvariant())
} | Sort-Object -Unique)
Write-Utf8Json (Join-Path $buildingRoot 'untracked_targets.json') $untrackedTargets 5

$ErrorActionPreference = 'Continue'
$branch = (@(& git -C $WorkspaceRoot branch --show-current 2>&1) -join '').Trim()
$branchExitCode = $LASTEXITCODE
$head = (@(& git -C $WorkspaceRoot rev-parse HEAD 2>&1) -join '').Trim()
$headExitCode = $LASTEXITCODE
$ErrorActionPreference = $errorActionAnteriorGit
if ($branchExitCode -ne 0 -or $headExitCode -ne 0) { throw 'Falha ao capturar branch/commit do Git.' }
$counts = @{}
foreach ($category in @('repo', 'installed', 'package', 'data')) {
    $counts[$category] = @($entries | Where-Object { $_.category -eq $category }).Count
}
$scopeCounts = @{}
foreach ($scope in @('Auth', 'Favoritos', 'Catalog', 'AvantPro')) {
    $scopeCounts[$scope] = @($entries | Where-Object { $_.category -eq 'data' -and $_.scope -eq $scope }).Count
}
$manifest = [ordered]@{
    schema_version = 2
    checkpoint = $Checkpoint
    previous_checkpoint = if ($PreviousCheckpoint) { $PreviousCheckpoint } else { $null }
    purpose = if ($Checkpoint -eq 'R5') {
        'Ponto fresco anterior a otimizacao de tempo do Favoritos, sem perda de cobertura da primeira pagina.'
    } else {
        'Checkpoint aprovado apos a otimizacao de tempo do Favoritos, com cobertura integral validada.'
    }
    created_at = (Get-Date).ToString('o')
    workspace = $WorkspaceRoot
    installed_root = $InstalledRoot
    package_root = $PackageRoot
    info_root = $infoRoot
    git_branch = $branch
    git_head = $head
    git_status = ($statusLines -join [Environment]::NewLine)
    parity = [ordered]@{
        success = $true
        output = ($parityOutput -join [Environment]::NewLine)
        checked_at = (Get-Date).ToString('o')
    }
    asset_manifest = [ordered]@{
        path = 'asset-manifest.json'
        version = [string]$assetManifest.version
        sha256 = Get-Sha256 (Join-Path $buildingRoot 'asset-manifest.json')
    }
    patch = [ordered]@{
        path = 'working_tree.patch'
        sha256 = Get-Sha256 $patchPath
        binary = $true
    }
    untracked_targets = [ordered]@{
        path = 'untracked_targets.json'
        sha256 = Get-Sha256 (Join-Path $buildingRoot 'untracked_targets.json')
        count = $untrackedTargets.Count
    }
    stopped_processes = [ordered]@{ count = 0; note = 'O criador nao encerra processos; o preflight exige que ja estejam fechados.' }
    planned_absent_paths = @($PlannedAbsentPaths | ForEach-Object { $_.Replace('\', '/') })
    counts = $counts
    data_scope_counts = $scopeCounts
    entries = @($entries)
    restore_sha256 = Get-Sha256 (Join-Path $buildingRoot 'restore.ps1')
}
Write-Utf8Json (Join-Path $buildingRoot 'manifest.json') $manifest 30

$validation = Invoke-SandboxValidation $buildingRoot $manifest $Checkpoint

if (Test-Path -LiteralPath $DestinationRoot) { throw "Destino apareceu durante a captura; sobrescrita recusada: $DestinationRoot" }
Move-Item -LiteralPath $buildingRoot -Destination $DestinationRoot

[pscustomobject]@{
    success = $true
    checkpoint = $Checkpoint
    path = $DestinationRoot
    version = [string]$assetManifest.version
    code_entries = [int]$counts.repo + [int]$counts.installed + [int]$counts.package
    data_entries = [int]$counts.data
    validation = 'sandbox_ok'
    canaries_preserved = [bool]$validation.canaries_preserved
    parity = ($parityOutput -join ' | ')
} | ConvertTo-Json -Compress
