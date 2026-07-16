param(
    [string]$WhisperModelSource = $env:JK_WHISPER_MODEL_SOURCE
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Split-Path -Parent $scriptDir
$runtimeVersionsPath = Join-Path $rootDir "runtime-versions.json"
if (-not (Test-Path -LiteralPath $runtimeVersionsPath -PathType Leaf)) {
    throw "Configuracao central de runtime ausente em $runtimeVersionsPath"
}
$runtimeVersions = Get-Content -LiteralPath $runtimeVersionsPath -Raw | ConvertFrom-Json
if ([int]$runtimeVersions.schemaVersion -ne 1 -or -not $runtimeVersions.python) {
    throw "runtime-versions.json invalido."
}
$pythonVersion = [string]$runtimeVersions.python.version
$pythonMinor = [string]$runtimeVersions.python.minor
$pythonImplementation = [string]$runtimeVersions.python.implementation
$pythonAbi = [string]$runtimeVersions.python.abi
$pythonArchitecture = [string]$runtimeVersions.python.windowsArchitecture
$pythonInstallerName = [string]$runtimeVersions.python.windowsInstaller
$pythonInstallerSize = [int64]$runtimeVersions.python.windowsInstallerSize
$pythonInstallerSha256 = ([string]$runtimeVersions.python.windowsInstallerSha256).ToLowerInvariant()
$windowsPortable = $runtimeVersions.python.windowsPortable
if (-not $windowsPortable) { throw "Pin python.windowsPortable ausente em runtime-versions.json." }
$pythonPortablePath = [string]$windowsPortable.path
$pythonPortableFileCount = [int]$windowsPortable.fileCount
$pythonPortableTotalSize = [int64]$windowsPortable.totalSize
$pythonPortableTreeSha256 = ([string]$windowsPortable.treeSha256).ToLowerInvariant()
$versionParts = @($pythonVersion.Split('.'))
if ($versionParts.Count -ne 3 -or @($versionParts | Where-Object { $_ -notmatch '^\d+$' }).Count -gt 0) {
    throw "Versao Python invalida em runtime-versions.json: $pythonVersion"
}
$derivedMinor = "$($versionParts[0]).$($versionParts[1])"
$derivedAbi = "$pythonImplementation$($versionParts[0])$($versionParts[1])"
if ($pythonMinor -ne $derivedMinor -or $pythonAbi -ne $derivedAbi) {
    throw "Minor/ABI Python divergentes em runtime-versions.json."
}
$pythonPlatforms = @{ amd64 = "win_amd64"; arm64 = "win_arm64"; x86 = "win32" }
$pythonPlatform = [string]$pythonPlatforms[$pythonArchitecture]
if (-not $pythonPlatform) { throw "Arquitetura Python nao suportada: $pythonArchitecture" }
if ([IO.Path]::GetFileName($pythonInstallerName) -ne $pythonInstallerName -or -not $pythonInstallerName.EndsWith('.exe')) {
    throw "Nome do instalador Python invalido: $pythonInstallerName"
}
if (
    $pythonInstallerSize -le 0 -or $pythonInstallerSha256 -notmatch '^[a-f0-9]{64}$' -or
    $pythonPortablePath -ne 'portable' -or $pythonPortableFileCount -le 0 -or
    $pythonPortableTotalSize -le 0 -or $pythonPortableTreeSha256 -notmatch '^[a-f0-9]{64}$'
) {
    throw "Pins do instalador/arvore Python invalidos em runtime-versions.json."
}
$packageJsonPath = Join-Path $rootDir "electron_app\package.json"
$requirementsPath = Join-Path $rootDir "requirements.txt"
$runtimeDir = Join-Path $rootDir "python_runtime"
$portableDir = Join-Path $runtimeDir "portable"
$wheelDir = Join-Path $rootDir "python_wheels"
$stagingDir = Join-Path $rootDir ".installer_runtime"
$prerequisitesDir = Join-Path $stagingDir "prerequisites"
$blackJhonDir = Join-Path $stagingDir "black_jhon"
$stagedModelDir = Join-Path $blackJhonDir "faster-whisper-small"
$pythonInstaller = Join-Path $runtimeDir $pythonInstallerName
$pythonInstallerUrl = "https://www.python.org/ftp/python/$pythonVersion/$pythonInstallerName"
$vcInstaller = Join-Path $prerequisitesDir "VC_redist.x64.exe"
$vcInstallerUrl = "https://aka.ms/vc14/vc_redist.x64.exe"
$wixCliVersion = "6.0.2"
$wixToolDir = Join-Path $stagingDir "tools\wix6"
$wixExe = Join-Path $wixToolDir "wix.exe"

function Assert-PathInside([string]$Path, [string]$Parent) {
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $fullParent = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
    if (-not $fullPath.StartsWith($fullParent, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Caminho fora da area permitida: $fullPath"
    }
}

function Remove-SafeTree([string]$Path, [string]$Parent) {
    Assert-PathInside $Path $Parent
    if (Test-Path -LiteralPath $Path) {
        $item = Get-Item -LiteralPath $Path -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Recusa ao remover reparse point: $Path"
        }
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}

function Assert-TrustedSignature([string]$Path, [string]$SubjectPattern) {
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    $subject = if ($signature.SignerCertificate) { [string]$signature.SignerCertificate.Subject } else { "" }
    if ($signature.Status -ne "Valid" -or $subject -notmatch $SubjectPattern) {
        throw "Assinatura invalida em $Path ($($signature.Status); $subject)."
    }
}

function Test-PinnedFile([string]$Path, [int64]$ExpectedSize, [string]$ExpectedSha256) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    $item = Get-Item -LiteralPath $Path
    if ($item.Length -ne $ExpectedSize) { return $false }
    $actualSha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    return $actualSha256 -eq $ExpectedSha256.ToLowerInvariant()
}

function Assert-PinnedFile([string]$Path, [int64]$ExpectedSize, [string]$ExpectedSha256, [string]$Label) {
    if (-not (Test-PinnedFile $Path $ExpectedSize $ExpectedSha256)) {
        throw "$Label diverge do pin imutavel em runtime-versions.json: $Path"
    }
}

function Invoke-CheckedProcess([string]$FilePath, [string[]]$ArgumentList, [string]$FailureMessage) {
    $process = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -ne 0 -and $process.ExitCode -ne 3010) {
        throw "$FailureMessage Codigo: $($process.ExitCode)."
    }
}

function Get-WixCli {
    $ready = $false
    if (Test-Path -LiteralPath $wixExe -PathType Leaf) {
        try {
            $versionOutput = @(& $wixExe --version 2>&1)
            $ready = $LASTEXITCODE -eq 0 -and [string]$versionOutput[-1] -match "^$([regex]::Escape($wixCliVersion))(?:\+|$)"
        } catch {
            $ready = $false
        }
    }
    if (-not $ready) {
        Remove-SafeTree $wixToolDir $stagingDir
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $wixToolDir) | Out-Null
        Write-Host "Baixando WiX CLI $wixCliVersion localmente para extrair o bundle Python..."
        Invoke-CheckedProcess "dotnet.exe" @(
            "tool", "install", "wix", "--tool-path", "`"$wixToolDir`"", "--version", $wixCliVersion
        ) "Falha ao instalar WiX CLI $wixCliVersion no cache local."
    }
    if (-not (Test-Path -LiteralPath $wixExe -PathType Leaf)) {
        throw "WiX CLI nao encontrado em $wixExe"
    }
    $versionOutput = @(& $wixExe --version 2>&1)
    if ($LASTEXITCODE -ne 0 -or [string]$versionOutput[-1] -notmatch "^$([regex]::Escape($wixCliVersion))(?:\+|$)") {
        throw "Versao inesperada do WiX CLI: $($versionOutput -join ' ')"
    }
    return $wixExe
}

function Test-PortablePython([string]$Path) {
    $pythonExe = Join-Path $Path "python.exe"
    if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) { return $false }
    try {
        $probeCode = "import ensurepip,json,platform,struct,sys,venv; print(json.dumps({'version':platform.python_version(),'abi':f'cp{sys.version_info.major}{sys.version_info.minor}','implementation':sys.implementation.name,'bits':struct.calcsize('P')*8}))"
        $previousNoBytecode = $env:PYTHONDONTWRITEBYTECODE
        $env:PYTHONDONTWRITEBYTECODE = "1"
        try {
            $output = @(& $pythonExe -B -I -c $probeCode 2>&1)
            if ($LASTEXITCODE -ne 0 -or $output.Count -lt 1) { return $false }
        } finally {
            $env:PYTHONDONTWRITEBYTECODE = $previousNoBytecode
        }
        $probe = [string]$output[-1] | ConvertFrom-Json
        return (
            [string]$probe.version -eq $pythonVersion -and
            [string]$probe.abi -eq $pythonAbi -and
            [string]$probe.implementation -eq "cpython" -and
            [int]$probe.bits -eq 64
        )
    } catch {
        return $false
    }
}

function Get-PortableInventory([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "Runtime portatil ausente em $Path"
    }
    $root = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $rootPrefix = $root + '\'
    $entries = @(Get-ChildItem -LiteralPath $root -Recurse -Force)
    foreach ($entry in $entries) {
        if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Link/reparse point proibido no runtime portatil: $($entry.FullName)"
        }
    }
    $relativePaths = [System.Collections.Generic.List[string]]::new()
    $filesByPath = @{}
    foreach ($file in @($entries | Where-Object { -not $_.PSIsContainer })) {
        $relative = $file.FullName.Substring($rootPrefix.Length).Replace('\', '/')
        $relativePaths.Add($relative)
        $filesByPath[$relative] = $file
    }
    $relativePaths.Sort([System.StringComparer]::Ordinal)
    $hasher = [Security.Cryptography.SHA256]::Create()
    $utf8 = [Text.UTF8Encoding]::new($false)
    [int64]$totalSize = 0
    try {
        foreach ($relative in $relativePaths) {
            $file = $filesByPath[$relative]
            $size = [int64]$file.Length
            $fileHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            $record = $utf8.GetBytes("$relative`0$size`0$fileHash`n")
            [void]$hasher.TransformBlock($record, 0, $record.Length, $record, 0)
            $totalSize += $size
        }
        [void]$hasher.TransformFinalBlock([byte[]]::new(0), 0, 0)
        $treeHash = ([BitConverter]::ToString($hasher.Hash)).Replace('-', '').ToLowerInvariant()
    } finally {
        $hasher.Dispose()
    }
    return [ordered]@{
        path = "portable"
        file_count = $relativePaths.Count
        total_size = $totalSize
        tree_sha256 = $treeHash
    }
}

function Test-PinnedPortablePython([string]$Path) {
    try {
        $inventory = Get-PortableInventory $Path
        $matchesPin = (
            [string]$inventory.path -eq $pythonPortablePath -and
            [int]$inventory.file_count -eq $pythonPortableFileCount -and
            [int64]$inventory.total_size -eq $pythonPortableTotalSize -and
            [string]$inventory.tree_sha256 -eq $pythonPortableTreeSha256
        )
        if (-not $matchesPin) { return $false }
        # O executavel so pode ser iniciado depois que toda a arvore corresponde
        # exatamente ao pin central revisado.
        return Test-PortablePython $Path
    } catch {
        return $false
    }
}

function New-PortablePython([string]$Destination) {
    $stage = Join-Path $runtimeDir (".portable-new-" + [guid]::NewGuid().ToString("N"))
    $bundleRoot = Join-Path $stagingDir ("python-bundle-" + $pythonVersion)
    $bundlePayloads = Join-Path $bundleRoot "payloads"
    $bundleBa = Join-Path $bundleRoot "ba"
    $bundleComponents = Join-Path $bundleRoot "components"
    $backup = Join-Path $runtimeDir ".portable-previous"
    Remove-SafeTree $stage $runtimeDir
    New-Item -ItemType Directory -Force -Path $stage | Out-Null

    try {
        Write-Host "Extraindo payloads embutidos do Python $pythonVersion sem executar o instalador..."
        $dark = Get-WixCli
        Remove-SafeTree $bundleRoot $stagingDir
        New-Item -ItemType Directory -Force -Path $bundleRoot | Out-Null
        Invoke-CheckedProcess $dark @(
            "burn", "extract", "`"$pythonInstaller`"", "-o", "`"$bundlePayloads`"", "-oba", "`"$bundleBa`""
        ) "Falha ao extrair o bundle oficial do Python."

        $bundleManifestPath = Join-Path $bundleBa "manifest.xml"
        if (-not (Test-Path -LiteralPath $bundleManifestPath -PathType Leaf)) {
            throw "Manifesto Burn ausente depois da extracao: $bundleManifestPath"
        }
        [xml]$bundleManifest = Get-Content -LiteralPath $bundleManifestPath -Raw
        New-Item -ItemType Directory -Force -Path $bundleComponents | Out-Null
        $namespace = [Xml.XmlNamespaceManager]::new($bundleManifest.NameTable)
        $namespace.AddNamespace("burn", "http://schemas.microsoft.com/wix/2008/Burn")
        $components = @(
            [ordered]@{ id = "core_AllUsers"; name = "core.msi"; required = $true; extract = $true },
            [ordered]@{ id = "exe_AllUsers"; name = "exe.msi"; required = $true; extract = $true },
            [ordered]@{ id = "dev_AllUsers"; name = "dev.msi"; required = $true; extract = $true },
            [ordered]@{ id = "lib_AllUsers"; name = "lib.msi"; required = $true; extract = $true },
            # pip.msi e um bootstrap sem tabela Media/arquivos e rejeita /a com MSI 2607.
            # Ele ainda e validado; o pip real e instalado depois via Lib\ensurepip.
            [ordered]@{ id = "pip_AllUsers"; name = "pip.msi"; required = $true; extract = $false },
            [ordered]@{ id = "tcltk_AllUsers"; name = "tcltk.msi"; required = $false; extract = $true }
        )
        foreach ($definition in $components) {
            $node = $bundleManifest.SelectSingleNode("//burn:Payload[@Id='$($definition.id)']", $namespace)
            if (-not $node) {
                if ($definition.required) { throw "Payload Burn obrigatorio ausente: $($definition.id)" }
                continue
            }
            if ([string]$node.Packaging -ne "embedded" -or -not [string]$node.Container) {
                throw "Payload Burn nao esta embutido no bundle: $($definition.id)"
            }
            if ([string]$node.FilePath -ne [string]$definition.name) {
                throw "Nome divergente no payload $($definition.id): $($node.FilePath)"
            }
            $sourcePath = [string]$node.SourcePath
            if (-not $sourcePath) {
                throw "SourcePath ausente no payload Burn $($definition.id)."
            }
            # Dependendo da versao/plataforma, WiX burn extract preserva SourcePath
            # (a0, a1...) ou materializa Container/FilePath (WixAttachedContainer/core.msi).
            # Aceitamos somente essas duas localizacoes declaradas pelo proprio manifesto.
            $sourceCandidate = Join-Path $bundlePayloads $sourcePath
            $containerCandidate = Join-Path (Join-Path $bundlePayloads ([string]$node.Container)) ([string]$node.FilePath)
            Assert-PathInside $sourceCandidate $bundlePayloads
            Assert-PathInside $containerCandidate $bundlePayloads
            $extractedPayload = @($sourceCandidate, $containerCandidate) |
                Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
                Select-Object -First 1
            if (-not $extractedPayload) {
                throw "Payload extraido ausente para $($definition.id): $sourceCandidate ou $containerCandidate"
            }
            $componentItem = Get-Item -LiteralPath $extractedPayload
            if ($componentItem.Length -ne [int64]$node.FileSize) {
                throw "Tamanho divergente no payload $($definition.id)."
            }
            $componentSha1 = (Get-FileHash -LiteralPath $extractedPayload -Algorithm SHA1).Hash.ToUpperInvariant()
            if ($componentSha1 -ne ([string]$node.Hash).ToUpperInvariant()) {
                throw "SHA1 divergente no payload $($definition.id)."
            }
            Assert-TrustedSignature $extractedPayload "Python Software Foundation"
            if (-not $definition.extract) {
                Write-Host "Payload Python verificado (bootstrap sem arquivos): $($definition.name)"
                continue
            }
            # A cadeia combina o EXE externo assinado, o tamanho/SHA1 do manifesto Burn
            # e a assinatura Authenticode da Python Software Foundation em cada MSI.
            $component = Join-Path $bundleComponents ([string]$definition.name)
            Assert-PathInside $component $bundleComponents
            Copy-Item -LiteralPath $extractedPayload -Destination $component -Force
            Write-Host "Extraindo componente Python verificado: $($definition.name)"
            Invoke-CheckedProcess "msiexec.exe" @(
                "/a", "`"$component`"", "/qn", "TARGETDIR=`"$stage`""
            ) "Falha ao extrair $($definition.name)."
        }
        Get-ChildItem -LiteralPath $stage -Filter "*.msi" -File -ErrorAction SilentlyContinue | Remove-Item -Force
        if (-not (Test-PinnedPortablePython $stage)) {
            throw "Payloads administrativos nao produziram a arvore Python fixada em runtime-versions.json."
        }
    } catch {
        $administrativeError = $_.Exception.Message
        Remove-SafeTree $stage $runtimeDir
        throw "Falha ao gerar runtime portatil sem instalar globalmente: $administrativeError"
    }
    if (-not (Test-PinnedPortablePython $stage)) {
        Remove-SafeTree $stage $runtimeDir
        throw "Runtime Python portatil gerado diverge do pin central."
    }

    $hadPrevious = Test-Path -LiteralPath $Destination
    Remove-SafeTree $backup $runtimeDir
    if ($hadPrevious) { Move-Item -LiteralPath $Destination -Destination $backup }
    try {
        Move-Item -LiteralPath $stage -Destination $Destination
        if (-not (Test-PinnedPortablePython $Destination)) {
            throw "Runtime Python falhou o pin central depois da promocao."
        }
        Remove-SafeTree $backup $runtimeDir
    } catch {
        Remove-SafeTree $Destination $runtimeDir
        if ($hadPrevious -and (Test-Path -LiteralPath $backup)) {
            Move-Item -LiteralPath $backup -Destination $Destination
        }
        throw
    }
}

function Test-Wheelhouse([string]$MarkerPath, [string]$RequirementsHash) {
    if (-not (Test-Path -LiteralPath $MarkerPath -PathType Leaf)) { return $false }
    $manifestPath = Join-Path $wheelDir "manifest.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { return $false }
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        if ([string]$manifest.requirements_sha256 -ne $RequirementsHash) { return $false }
        if ([string]$manifest.python_version -ne $pythonMinor) { return $false }
        if ([string]$manifest.abi -ne $pythonAbi) { return $false }
        if ([string]$manifest.platform -ne $pythonPlatform) { return $false }
        $declared = @($manifest.wheels)
        $actual = @(Get-ChildItem -LiteralPath $wheelDir -Filter "*.whl" -File)
        if ($declared.Count -lt 20 -or $declared.Count -ne $actual.Count) { return $false }
        foreach ($entry in $declared) {
            $name = [string]$entry.name
            if (-not $name -or $name -ne [IO.Path]::GetFileName($name)) { return $false }
            $candidate = Join-Path $wheelDir $name
            if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { return $false }
            $item = Get-Item -LiteralPath $candidate
            if ($item.Length -ne [int64]$entry.size) { return $false }
            if ((Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant() -ne [string]$entry.sha256) {
                return $false
            }
        }
        return $true
    } catch {
        return $false
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
if (-not $appVersion) { throw "Versao do aplicativo nao encontrada em $packageJsonPath" }

New-Item -ItemType Directory -Force -Path $runtimeDir, $wheelDir, $prerequisitesDir, $blackJhonDir | Out-Null

# Somente o instalador definido na configuracao central pode entrar no pacote.
foreach ($obsoleteInstaller in @(Get-ChildItem -LiteralPath $runtimeDir -Filter "python-*.exe" -File -ErrorAction SilentlyContinue)) {
    if ($obsoleteInstaller.Name -ne $pythonInstallerName) {
        Write-Host "Removendo instalador Python obsoleto: $($obsoleteInstaller.Name)"
        Remove-Item -LiteralPath $obsoleteInstaller.FullName -Force
    }
}
$portableBackup = Join-Path $runtimeDir ".portable-previous"
if ((Test-Path -LiteralPath $portableBackup -PathType Container) -and -not (Test-Path -LiteralPath $portableDir)) {
    Write-Host "Restaurando runtime portatil preservado por uma execucao interrompida."
    Move-Item -LiteralPath $portableBackup -Destination $portableDir
} elseif (Test-Path -LiteralPath $portableBackup) {
    Remove-SafeTree $portableBackup $runtimeDir
}
foreach ($obsoleteStage in @(Get-ChildItem -LiteralPath $runtimeDir -Directory -Force -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -like '.portable-new-*'
})) {
    Remove-SafeTree $obsoleteStage.FullName $runtimeDir
}

if ((Test-Path -LiteralPath $pythonInstaller -PathType Leaf) -and -not (
    Test-PinnedFile $pythonInstaller $pythonInstallerSize $pythonInstallerSha256
)) {
    Write-Host "Descartando instalador Python que diverge do pin central."
    Remove-Item -LiteralPath $pythonInstaller -Force
}
if (-not (Test-Path -LiteralPath $pythonInstaller -PathType Leaf)) {
    Write-Host "Baixando Python $pythonVersion para o instalador..."
    Invoke-WebRequest -Uri $pythonInstallerUrl -OutFile $pythonInstaller -UseBasicParsing
}
Assert-PinnedFile $pythonInstaller $pythonInstallerSize $pythonInstallerSha256 "Instalador oficial do Python"
Assert-TrustedSignature $pythonInstaller "Python Software Foundation"
$packagedInstallers = @(Get-ChildItem -LiteralPath $runtimeDir -Filter "python-*.exe" -File)
if ($packagedInstallers.Count -ne 1 -or $packagedInstallers[0].Name -ne $pythonInstallerName) {
    throw "python_runtime deve conter somente $pythonInstallerName."
}

if (-not (Test-PinnedPortablePython $portableDir)) {
    New-PortablePython $portableDir
}
if (-not (Test-PinnedPortablePython $portableDir)) {
    throw "Runtime Python portatil diverge do pin central em $portableDir"
}
$portablePython = Join-Path $portableDir "python.exe"

if (-not (Test-Path -LiteralPath $vcInstaller -PathType Leaf)) {
    Write-Host "Baixando Visual C++ Redistributable x64..."
    Invoke-WebRequest -Uri $vcInstallerUrl -OutFile $vcInstaller -UseBasicParsing
}
Assert-TrustedSignature $vcInstaller "Microsoft"

$requirementsHash = (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256).Hash.ToLowerInvariant()
$prepareHash = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
$markerPath = Join-Path $wheelDir ".requirements-$requirementsHash-$pythonAbi-$prepareHash.ok"
$criticalPatterns = @("openai_codex-*.whl", "openai_codex_cli_bin-*.whl", "faster_whisper-*.whl")
$wheelhouseReady = Test-Wheelhouse $markerPath $requirementsHash
foreach ($pattern in $criticalPatterns) {
    if (@(Get-ChildItem -LiteralPath $wheelDir -Filter $pattern -File -ErrorAction SilentlyContinue).Count -lt 1) {
        $wheelhouseReady = $false
    }
}

if (-not $wheelhouseReady) {
    Write-Host "Preparando dependencias offline para Windows/Python $pythonMinor ($pythonAbi)..."
    Get-ChildItem -LiteralPath $wheelDir -File -ErrorAction SilentlyContinue | Remove-Item -Force
    # Ferramentas de build ficam fora do runtime distribuido. Isso evita incorporar
    # launchers com caminhos absolutos e impede que pip/user-site global contamine o build.
    $buildVenvDir = Join-Path $stagingDir ("python-build-venv-" + $pythonAbi)
    Remove-SafeTree $buildVenvDir $stagingDir
    $previousNoBytecode = $env:PYTHONDONTWRITEBYTECODE
    $previousNoUserSite = $env:PYTHONNOUSERSITE
    $previousPythonPath = $env:PYTHONPATH
    $previousPythonHome = $env:PYTHONHOME
    $env:PYTHONDONTWRITEBYTECODE = "1"
    $env:PYTHONNOUSERSITE = "1"
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
    try {
        & $portablePython -B -I -m venv --copies $buildVenvDir
        if ($LASTEXITCODE -ne 0) { throw "Falha ao criar ambiente isolado para resolver o wheelhouse." }
        $buildPython = Join-Path $buildVenvDir "Scripts\python.exe"
        $probeOutput = @(& $buildPython -B -I -c "import json,pip,platform,sys; print(json.dumps({'version':platform.python_version(),'prefix':sys.prefix,'pip_file':pip.__file__}))" 2>&1)
        if ($LASTEXITCODE -ne 0 -or $probeOutput.Count -lt 1) {
            throw "pip indisponivel no ambiente isolado de build: $($probeOutput -join ' ')"
        }
        $buildProbe = [string]$probeOutput[-1] | ConvertFrom-Json
        $buildPrefix = [IO.Path]::GetFullPath([string]$buildProbe.prefix).TrimEnd('\') + '\'
        $pipFile = [IO.Path]::GetFullPath([string]$buildProbe.pip_file)
        if (
            [string]$buildProbe.version -ne $pythonVersion -or
            -not $buildPrefix.StartsWith(([IO.Path]::GetFullPath($buildVenvDir).TrimEnd('\') + '\'), [StringComparison]::OrdinalIgnoreCase) -or
            -not $pipFile.StartsWith($buildPrefix, [StringComparison]::OrdinalIgnoreCase)
        ) {
            throw "Ambiente pip de build nao esta isolado em $buildVenvDir."
        }
        Write-Host "pip de build isolado verificado: $pipFile"
        $downloadArgs = @(
            "-B", "-I", "-m", "pip", "--isolated", "download", "--disable-pip-version-check", "--require-hashes",
            "--dest", $wheelDir, "--prefer-binary", "--only-binary=:all:",
            "--platform", $pythonPlatform, "--implementation", $pythonImplementation,
            "--python-version", $pythonMinor, "--abi", $pythonAbi, "-r", $requirementsPath
        )
        & $buildPython @downloadArgs
        if ($LASTEXITCODE -ne 0) { throw "Falha ao baixar as dependencias offline para $pythonAbi." }
    } finally {
        Remove-SafeTree $buildVenvDir $stagingDir
        $env:PYTHONDONTWRITEBYTECODE = $previousNoBytecode
        $env:PYTHONNOUSERSITE = $previousNoUserSite
        $env:PYTHONPATH = $previousPythonPath
        $env:PYTHONHOME = $previousPythonHome
    }

    $wheels = @(Get-ChildItem -LiteralPath $wheelDir -Filter "*.whl" -File)
    if ($wheels.Count -lt 20) { throw "Wheelhouse incompleto: $($wheels.Count) arquivos." }
    foreach ($pattern in $criticalPatterns) {
        if (@(Get-ChildItem -LiteralPath $wheelDir -Filter $pattern -File).Count -lt 1) {
            throw "Wheel critica ausente: $pattern"
        }
    }
    $wheelManifest = [ordered]@{
        requirements_sha256 = $requirementsHash
        python_version = $pythonMinor
        abi = $pythonAbi
        platform = $pythonPlatform
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
if (-not (Test-Wheelhouse $markerPath $requirementsHash)) { throw "Wheelhouse $pythonAbi falhou na verificacao final." }

if (-not (Test-WhisperModel $stagedModelDir)) {
    $candidates = @($WhisperModelSource, (Join-Path $rootDir "info\ai_models\faster-whisper-small"))
    if ($env:APPDATA) {
        $candidates += Join-Path $env:APPDATA "JK Sistema Cliente\local_app\info\ai_models\faster-whisper-small"
    }
    $modelSource = $candidates | Where-Object { $_ -and (Test-WhisperModel $_) } | Select-Object -First 1
    if (-not $modelSource) {
        throw "Whisper Small valido nao encontrado. Defina JK_WHISPER_MODEL_SOURCE e execute novamente."
    }
    Assert-PathInside $stagedModelDir $stagingDir
    if (Test-Path -LiteralPath $stagedModelDir) { Remove-SafeTree $stagedModelDir $stagingDir }
    Copy-Item -LiteralPath $modelSource -Destination $stagedModelDir -Recurse -Force
}
if (-not (Test-WhisperModel $stagedModelDir)) { throw "Whisper Small empacotado falhou na verificacao SHA256." }

$portableInventory = Get-PortableInventory $portableDir
$buildManifest = [ordered]@{
    version = $appVersion
    python = [ordered]@{
        version = $pythonVersion
        abi = $pythonAbi
        installer = [ordered]@{
            path = $pythonInstallerName
            size = $pythonInstallerSize
            sha256 = $pythonInstallerSha256
        }
        portable = $portableInventory
    }
    visual_cpp = [ordered]@{
        path = "VC_redist.x64.exe"
        size = (Get-Item -LiteralPath $vcInstaller).Length
        sha256 = (Get-FileHash -LiteralPath $vcInstaller -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    whisper = Get-Content -LiteralPath (Join-Path $stagedModelDir "model-manifest.json") -Raw | ConvertFrom-Json
}
$buildManifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $stagingDir "runtime-manifest.json") -Encoding UTF8

$wheelCount = @(Get-ChildItem -LiteralPath $wheelDir -Filter "*.whl" -File).Count
$modelBytes = (Get-ChildItem -LiteralPath $stagedModelDir -Recurse -File | Measure-Object Length -Sum).Sum
$portableBytes = [int64]$portableInventory.total_size
Write-Host "Runtime offline pronto: Python $pythonVersion/$pythonAbi ($([math]::Round($portableBytes / 1MB, 1)) MiB); $wheelCount wheels; Whisper $([math]::Round($modelBytes / 1MB, 1)) MiB."
