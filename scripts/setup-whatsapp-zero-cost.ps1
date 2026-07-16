[CmdletBinding()]
param(
    [string]$D1Name = "jk-whatsapp-gateway",
    [string]$KVNamespace = "jk-whatsapp-media",
    [string]$BusinessPhone = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Read-SecretText([string]$Prompt) {
    $secure = Read-Host $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function New-RandomToken([int]$Bytes = 32) {
    $buffer = New-Object byte[] $Bytes
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($buffer) } finally { $generator.Dispose() }
    return [BitConverter]::ToString($buffer).Replace("-", "").ToLowerInvariant()
}

function Invoke-Wrangler {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & npx.cmd --no-install wrangler @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Wrangler falhou: $($Arguments -join ' ')"
    }
}

function Set-WranglerSecret([string]$Name, [string]$Value) {
    $Value | & npx.cmd --no-install wrangler secret put $Name
    if ($LASTEXITCODE -ne 0) {
        throw "Nao foi possivel gravar o segredo $Name."
    }
}

$root = Split-Path -Parent $PSScriptRoot
$gateway = Join-Path $root "cloudflare\whatsapp-gateway"
$exampleConfig = Join-Path $gateway "wrangler.example.toml"
$configPath = Join-Path $gateway "wrangler.toml"
$localConfigPath = Join-Path $root "info\whatsapp_bridge.json"

if (-not (Test-Path -LiteralPath $gateway -PathType Container)) {
    throw "Gateway nao encontrado em $gateway"
}
if (-not (Get-Command node.exe -ErrorAction SilentlyContinue) -or -not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
    throw "Node.js e npm sao obrigatorios para usar o Wrangler."
}

$confirmation = Read-Host "Confirme que a conta Cloudflare esta no plano Workers Free e sem upgrade automatico (digite FREE)"
if ($confirmation -cne "FREE") {
    throw "Implantacao cancelada. Custo zero exige confirmacao explicita do plano Free."
}

Push-Location $gateway
$appSecret = $null
$systemToken = $null
$wabaId = $null
$phoneNumberId = $null
try {
    npm.cmd install
    if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar dependencias do gateway." }
    npm.cmd test
    if ($LASTEXITCODE -ne 0) { throw "Os testes do gateway falharam." }
    npm.cmd run check
    if ($LASTEXITCODE -ne 0) { throw "A verificacao TypeScript falhou." }

    Invoke-Wrangler login

    if (Test-Path -LiteralPath $configPath) {
        throw "wrangler.toml ja existe. Remova-o manualmente somente se deseja uma implantacao nova."
    }
    Copy-Item -LiteralPath $exampleConfig -Destination $configPath

    # Wrangler 4.110+ no longer accepts --json for `d1 create`. Keep parsing
    # the UUID from its text output so the zero-cost setup remains compatible.
    $d1Output = (& npx.cmd --no-install wrangler d1 create $D1Name 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) { throw "Falha ao criar D1: $d1Output" }
    $databaseId = ""
    try {
        $d1Json = $d1Output | ConvertFrom-Json
        foreach ($propertyName in @("uuid", "database_id", "id")) {
            if (-not $databaseId -and $d1Json.PSObject.Properties.Name -contains $propertyName) {
                $databaseId = [string]$d1Json.$propertyName
            }
        }
    }
    catch {
        $match = [regex]::Match($d1Output, "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
        if ($match.Success) { $databaseId = $match.Value }
    }
    if (-not $databaseId) { throw "Nao foi possivel identificar o ID do D1 criado." }

    $kvOutput = (& npx.cmd --no-install wrangler kv namespace create $KVNamespace 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) { throw "Falha ao criar Workers KV: $kvOutput" }
    $kvMatch = [regex]::Match($kvOutput, '[0-9a-fA-F]{32}')
    if (-not $kvMatch.Success) { throw "Nao foi possivel identificar o ID do Workers KV criado." }
    $kvNamespaceId = $kvMatch.Value

    $toml = Get-Content -LiteralPath $configPath -Raw
    $toml = $toml.Replace('database_name = "jk-whatsapp-gateway"', ('database_name = "{0}"' -f $D1Name))
    $toml = $toml.Replace('database_id = "REPLACE_WITH_D1_DATABASE_ID"', ('database_id = "{0}"' -f $databaseId))
    $toml = $toml.Replace('id = "REPLACE_WITH_KV_NAMESPACE_ID"', ('id = "{0}"' -f $kvNamespaceId))

    $graphVersion = Read-Host "Versao atual da Graph API exibida no painel Meta (ex.: v23.0)"
    if ($graphVersion -notmatch '^v\d+\.\d+$') { throw "Versao da Graph API invalida." }
    $toml = $toml.Replace('META_GRAPH_API_VERSION = "REPLACE_WITH_CURRENT_META_GRAPH_VERSION"', "META_GRAPH_API_VERSION = `"$graphVersion`"")
    Set-Content -LiteralPath $configPath -Value $toml -Encoding UTF8

    Invoke-Wrangler d1 migrations apply $D1Name --remote

    $appSecret = Read-SecretText "Meta App Secret"
    $systemToken = Read-SecretText "Meta System User Token"
    $wabaId = Read-SecretText "Meta WABA ID"
    $phoneNumberId = Read-SecretText "Meta Phone Number ID"
    $bridgeToken = New-RandomToken 32
    $verifyToken = New-RandomToken 24

    $deployOutput = (Invoke-Wrangler deploy --strict | Out-String)
    Set-WranglerSecret "META_APP_SECRET" $appSecret
    Set-WranglerSecret "META_ACCESS_TOKEN" $systemToken
    Set-WranglerSecret "META_SYSTEM_USER_TOKEN" $systemToken
    Set-WranglerSecret "META_WABA_ID" $wabaId
    Set-WranglerSecret "META_PHONE_NUMBER_ID" $phoneNumberId
    Set-WranglerSecret "BRIDGE_TOKEN" $bridgeToken
    Set-WranglerSecret "META_VERIFY_TOKEN" $verifyToken
    $deployOutput = (Invoke-Wrangler deploy --strict | Out-String)

    $urlMatch = [regex]::Match($deployOutput, 'https://[^\s]+\.workers\.dev')
    $workerUrl = if ($urlMatch.Success) { $urlMatch.Value.TrimEnd('/') } else { Read-Host "URL workers.dev publicada" }
    if ($workerUrl -notmatch '^https://') { throw "URL do Worker invalida." }

    $headers = @{ Authorization = "Bearer $bridgeToken" }
    $health = Invoke-RestMethod -Method Get -Uri "$workerUrl/bridge/status" -Headers $headers
    if (-not $health.success -or -not $health.zero_cost.policy_valid) {
        throw "Worker respondeu, mas a politica de custo zero nao esta valida."
    }
    $templateBody = @{ create_missing = $true } | ConvertTo-Json
    $templateResult = Invoke-RestMethod -Method Post -Uri "$workerUrl/bridge/templates/sync" -Headers $headers -ContentType "application/json" -Body $templateBody

    $local = @{}
    if (Test-Path -LiteralPath $localConfigPath) {
        try {
            $storedLocal = Get-Content -LiteralPath $localConfigPath -Raw | ConvertFrom-Json
            foreach ($property in $storedLocal.PSObject.Properties) { $local[$property.Name] = $property.Value }
        }
        catch { $local = @{} }
    }
    $local["worker_url"] = $workerUrl
    $local["bridge_token"] = $bridgeToken
    $local["business_phone"] = $BusinessPhone
    $local["enabled"] = $false
    $local["zero_cost_policy_valid_until"] = "2026-09-30T23:59:59Z"
    $local["updated_at"] = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $localDir = Split-Path -Parent $localConfigPath
    if (-not (Test-Path -LiteralPath $localDir)) { New-Item -ItemType Directory -Path $localDir | Out-Null }
    $local | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $localConfigPath -Encoding UTF8

    Write-Host ""
    Write-Host "Gateway publicado com D1 e Workers KV Free e ainda DESATIVADO no JK, como protecao." -ForegroundColor Green
    Write-Host "Callback URL: $workerUrl/webhooks/whatsapp"
    Write-Host "Verify token (copie agora para a Meta): $verifyToken"
    Write-Host "No painel Meta, assine somente o campo messages."
    Write-Host "Templates enviados/sincronizados: $($templateResult.synced)"
    Write-Host "Abra Configuracoes > WhatsApp - Joao, baixe o Whisper, teste e so entao ative."
}
finally {
    Pop-Location
    $appSecret = $null
    $systemToken = $null
    $wabaId = $null
    $phoneNumberId = $null
}
