$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$rootDir = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$prepareScript = Join-Path $rootDir "scripts\prepare_installer_runtime.ps1"
$parseTokens = $null
$parseErrors = $null
$scriptAst = [Management.Automation.Language.Parser]::ParseFile($prepareScript, [ref]$parseTokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) { throw "Script de runtime contem erros de sintaxe." }
# Importar somente as funcoes evita downloads, MSI e qualquer runtime real.
foreach ($name in @("Assert-PathInside", "Remove-SafeTree", "Invoke-WixBundleExtract")) {
    $functionAst = $scriptAst.Find({
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
    }, $true)
    if (-not $functionAst) { throw "Funcao de runtime ausente: $name" }
    Invoke-Expression $functionAst.Extent.Text
}

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

$tempParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$fixtureRoot = Join-Path $tempParent ("jk-wix-extraction-test-" + [guid]::NewGuid().ToString("N"))
Assert-PathInside $fixtureRoot $tempParent
New-Item -ItemType Directory -Path $fixtureRoot | Out-Null

function Start-Process {
    param(
        [string]$FilePath,
        [string[]]$ArgumentList,
        [switch]$Wait,
        [switch]$PassThru,
        [string]$WindowStyle,
        [string]$RedirectStandardOutput,
        [string]$RedirectStandardError
    )
    $script:processCalls++
    Assert-True ($FilePath -eq "fixture-wix.exe") "Executavel WiX divergente."
    Assert-True ($Wait -and $PassThru -and $WindowStyle -eq "Hidden") "Invocacao deve aguardar processo oculto e obter seu exit code."
    $expectedArguments = @(
        "burn", "extract", "`"$script:installerFixture`"", "-o", "`"$(Join-Path $script:bundleFixture 'payloads')`"",
        "-oba", "`"$(Join-Path $script:bundleFixture 'ba')`""
    )
    Assert-True (($ArgumentList -join "`n") -eq ($expectedArguments -join "`n")) "Argumentos de extracao divergentes."
    Assert-PathInside $RedirectStandardOutput $script:stagingDir
    Assert-PathInside $RedirectStandardError $script:stagingDir
    Assert-True ($RedirectStandardOutput -ne $RedirectStandardError) "stdout e stderr precisam de arquivos distintos."
    Assert-True ((Test-Path -LiteralPath (Split-Path -Parent $RedirectStandardOutput) -PathType Container)) "Pasta de logs temporarios ausente."
    Assert-True (@(Get-ChildItem -LiteralPath $script:bundleFixture -Force).Count -eq 0) "Tentativa reutilizou arquivos parciais."
    $script:logFiles.Add($RedirectStandardOutput)
    $script:logFiles.Add($RedirectStandardError)
    Set-Content -LiteralPath $RedirectStandardOutput -Value "fixture stdout privado" -Encoding ascii
    Set-Content -LiteralPath $RedirectStandardError -Value "fixture stderr privado" -Encoding ascii
    New-Item -ItemType Directory -Path (Join-Path $script:bundleFixture "payloads") | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $script:bundleFixture "ba") | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $script:bundleFixture "components") | Out-Null
    Set-Content -LiteralPath (Join-Path $script:bundleFixture "payloads\partial.msi") -Value "partial" -Encoding ascii
    $code = $script:exitCodes[$script:processCalls - 1]
    if ($code -eq 0) {
        Set-Content -LiteralPath (Join-Path $script:bundleFixture "ba\manifest.xml") -Value "<fixture />" -Encoding ascii
    }
    return [pscustomobject]@{ ExitCode = $code }
}

function Start-Sleep {
    param([int]$Milliseconds)
    Assert-True ($Milliseconds -eq (300 * $script:sleepCalls.Count + 300)) "Backoff deve ser curto e limitado."
    $script:sleepCalls.Add($Milliseconds)
}

function Test-ExtractionCase([string]$Name, [int[]]$Codes, [int]$ExpectedCalls, [bool]$ExpectedSuccess) {
    $script:stagingDir = Join-Path $fixtureRoot $Name
    $script:bundleFixture = Join-Path $script:stagingDir "python-bundle"
    $script:installerFixture = Join-Path $script:stagingDir "python installer.exe"
    New-Item -ItemType Directory -Path $script:bundleFixture -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $script:bundleFixture "old-partial.txt") -Value "old" -Encoding ascii
    $script:exitCodes = $Codes
    $script:processCalls = 0
    $script:logFiles = [Collections.Generic.List[string]]::new()
    $script:sleepCalls = [Collections.Generic.List[int]]::new()
    $failure = $null
    $visibleOutput = @(& {
        try {
            Invoke-WixBundleExtract "fixture-wix.exe" $script:installerFixture $script:bundleFixture
        } catch {
            $script:caseFailure = $_.Exception.Message
        }
    } 3>&1)
    $failure = $script:caseFailure
    $script:caseFailure = $null
    Assert-True ($script:processCalls -eq $ExpectedCalls) "Numero inesperado de tentativas em $Name."
    Assert-True ($script:sleepCalls.Count -eq ($ExpectedCalls - 1)) "Retry inesperado em $Name."
    Assert-True (($null -eq $failure) -eq $ExpectedSuccess) "Resultado inesperado em $Name."
    Assert-True (($visibleOutput -join ' ') -notmatch 'fixture (stdout|stderr) privado') "Saida privada foi publicada."
    foreach ($logFile in $script:logFiles) {
        Assert-True (-not (Test-Path -LiteralPath $logFile)) "Log temporario nao foi removido."
        Assert-True (-not (Test-Path -LiteralPath (Split-Path -Parent $logFile))) "Pasta temporaria de logs nao foi removida."
    }
    if ($ExpectedSuccess) {
        Assert-True ((Test-Path -LiteralPath (Join-Path $script:bundleFixture "ba\manifest.xml") -PathType Leaf)) "Extracao bem sucedida foi removida."
    } else {
        Assert-True ($failure -match "Codigo: $($Codes[$ExpectedCalls - 1])\.") "Falha perdeu codigo de saida."
        Assert-True (-not (Test-Path -LiteralPath $script:bundleFixture)) "Extracao incompleta permaneceu apos falha final."
    }
}

try {
    $script:caseFailure = $null
    Test-ExtractionCase "pipe-then-success" @(-2147024664, 0) 2 $true
    Test-ExtractionCase "pipe-exhausted" @(-2147024664, -2147024664, -2147024664) 3 $false
    Test-ExtractionCase "unrelated-error" @(5, 0) 1 $false
    Test-ExtractionCase "immediate-success" @(0) 1 $true
    Test-ExtractionCase "msi-reboot-is-not-burn-success" @(3010, 0) 1 $false
    Write-Host "installer WiX extraction: OK"
} finally {
    Remove-SafeTree $fixtureRoot $tempParent
}
