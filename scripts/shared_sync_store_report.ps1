[CmdletBinding()]
param(
    [string]$TenantRoot,
    [string]$OutputPath,
    [string]$PythonExecutable
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$reportScript = Join-Path $PSScriptRoot 'shared_sync_store_report.py'
if (-not (Test-Path -LiteralPath $reportScript -PathType Leaf)) {
    throw 'Copie o arquivo shared_sync_store_report.py para a mesma pasta deste arquivo.'
}

$runtimeRoot = Join-Path $env:APPDATA 'JK Sistema Cliente\local_app'
if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
    $pythonCandidates = @(
        (Join-Path $runtimeRoot 'python_runtime\portable\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\JK Sistema Cliente\resources\local_app\python_runtime\portable\python.exe')
    )
    $detectedPython = @($pythonCandidates | Where-Object {
        Test-Path -LiteralPath $_ -PathType Leaf
    } | Select-Object -First 1)
    if ($detectedPython.Count -eq 0) {
        throw 'Python portatil do JK nao encontrado. Informe -PythonExecutable com o caminho de um Python 3 instalado.'
    }
    $PythonExecutable = [string]$detectedPython[0]
}

if ([string]::IsNullOrWhiteSpace($TenantRoot)) {
    $infoRoot = Join-Path $runtimeRoot 'info'
    if (-not (Test-Path -LiteralPath $infoRoot -PathType Container)) {
        throw 'Pasta de dados nao encontrada. Informe -TenantRoot com a pasta que contem lojas_config.json.'
    }
    $tenants = @(Get-ChildItem -LiteralPath $infoRoot -Directory | Where-Object {
        -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -and
        $_.Name -match '^(?:[0-9]{6}|default)$' -and
        (Test-Path -LiteralPath (Join-Path $_.FullName 'lojas_config.json') -PathType Leaf)
    } | Sort-Object Name)
    if ($tenants.Count -eq 0) {
        throw 'Cadastro de lojas nao encontrado. Informe -TenantRoot com a pasta que contem lojas_config.json.'
    }
    if ($tenants.Count -eq 1) {
        $TenantRoot = $tenants[0].FullName
    } else {
        Write-Host 'Escolha o cliente que apresenta as lojas duplicadas:'
        for ($i = 0; $i -lt $tenants.Count; $i++) {
            Write-Host ('{0} - Cliente {1}' -f ($i + 1), $tenants[$i].Name)
        }
        $selection = Read-Host 'Digite o numero'
        $selectedNumber = 0
        if (-not [int]::TryParse($selection, [ref]$selectedNumber) -or
            $selectedNumber -lt 1 -or $selectedNumber -gt $tenants.Count) {
            throw 'Escolha invalida. Execute novamente e escolha um dos numeros apresentados.'
        }
        $TenantRoot = $tenants[$selectedNumber - 1].FullName
    }
}

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $PSScriptRoot ('relatorio-lojas-{0}.json' -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
}

& $PythonExecutable -B $reportScript --tenant-root $TenantRoot --output $OutputPath
if ($LASTEXITCODE -ne 0) {
    throw 'O diagnostico nao terminou. Consulte a mensagem acima; nenhum cadastro foi alterado.'
}
Write-Host ('Relatorio gerado: {0}' -f [IO.Path]::GetFileName($OutputPath))
Write-Host 'Envie esse JSON para a conferencia das lojas. O arquivo nao remove nem altera os cadastros.'
