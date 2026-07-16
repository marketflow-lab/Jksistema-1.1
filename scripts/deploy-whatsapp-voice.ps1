[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-zA-Z0-9.-]+$')]
    [string]$VoiceSipHost,
    [string]$D1Name = "jk-whatsapp-gateway"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Read-SecretText([string]$Prompt) {
    $secure = Read-Host $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

function Set-WranglerSecret([string]$Name, [string]$Value) {
    if (-not $Value) { throw "O segredo $Name está vazio." }
    $Value | & npx.cmd --no-install wrangler secret put $Name
    if ($LASTEXITCODE -ne 0) { throw "Não foi possível gravar o segredo $Name." }
}

$root = Split-Path -Parent $PSScriptRoot
$gateway = Join-Path $root "cloudflare\whatsapp-gateway"
$configPath = Join-Path $gateway "wrangler.toml"
$python = Join-Path $root ".venv\Scripts\python.exe"
$localConfigs = @(
    (Join-Path $root "info\whatsapp_bridge.json"),
    (Join-Path $env:APPDATA "JK Sistema Cliente\local_app\info\whatsapp_bridge.json")
)

if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { throw "wrangler.toml não encontrado." }
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Python do ambiente virtual não encontrado." }

Push-Location $gateway
$openAiKey = $null
$webhookSecret = $null
$projectId = $null
try {
    npm.cmd test
    if ($LASTEXITCODE -ne 0) { throw "Os testes do gateway falharam." }
    npm.cmd run check
    if ($LASTEXITCODE -ne 0) { throw "A verificação TypeScript falhou." }

    $openAiKey = (& $python -c "import os,sys; os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1]); from backend.services.ia_providers import _obter_openai_api_key; print(_obter_openai_api_key() or '', end='')" $root)
    if ($LASTEXITCODE -ne 0 -or -not $openAiKey) { throw "A chave OpenAI existente não pôde ser lida pelo backend." }
    $webhookSecret = Read-SecretText "OpenAI webhook signing secret (whsec_...)"
    $projectId = Read-SecretText "OpenAI Project ID (proj_...)"
    if ($webhookSecret -notmatch '^whsec_') { throw "Webhook secret OpenAI inválido." }
    if ($projectId -notmatch '^proj_') { throw "Project ID OpenAI inválido." }

    $toml = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8
    if ($toml -match '(?m)^VOICE_SIP_HOST\s*=') {
        $toml = [regex]::Replace($toml, '(?m)^VOICE_SIP_HOST\s*=\s*"[^"]*"', "VOICE_SIP_HOST = `"$VoiceSipHost`"")
    }
    else {
        $toml = $toml.Replace('[vars]', "[vars]`r`nVOICE_SIP_HOST = `"$VoiceSipHost`"")
    }
    Set-Content -LiteralPath $configPath -Value $toml -Encoding UTF8

    # A voz permanece desligada até o preflight final; divergência de fingerprint
    # também faz o Worker rejeitar novas chamadas.
    foreach ($localConfig in ($localConfigs | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $localConfig)) { continue }
        $local = Get-Content -LiteralPath $localConfig -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($local.PSObject.Properties.Name -contains 'voice_enabled') { $local.voice_enabled = $false }
        else { $local | Add-Member -NotePropertyName voice_enabled -NotePropertyValue $false }
        $local | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $localConfig -Encoding UTF8
    }

    & npx.cmd --no-install wrangler d1 migrations apply $D1Name --remote
    if ($LASTEXITCODE -ne 0) { throw "Falha ao aplicar a migração D1 de voz." }
    Set-WranglerSecret "OPENAI_REALTIME_API_KEY" $openAiKey
    Set-WranglerSecret "OPENAI_WEBHOOK_SECRET" $webhookSecret
    Set-WranglerSecret "OPENAI_PROJECT_ID" $projectId

    & npx.cmd --no-install wrangler deploy --strict
    if ($LASTEXITCODE -ne 0) { throw "Falha ao publicar o gateway de voz." }
    Write-Host "Gateway de voz publicado. Abra Configurações e execute Validar ligações antes de habilitar." -ForegroundColor Green
}
finally {
    $openAiKey = $null
    $webhookSecret = $null
    $projectId = $null
    Pop-Location
}
