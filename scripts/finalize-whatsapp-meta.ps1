[CmdletBinding()]
param()

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

function New-RandomToken([int]$Bytes = 24) {
    $buffer = New-Object byte[] $Bytes
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($buffer) } finally { $generator.Dispose() }
    return [BitConverter]::ToString($buffer).Replace("-", "").ToLowerInvariant()
}

function Set-WranglerSecret([string]$Name, [string]$Value) {
    $Value | & npx.cmd --no-install wrangler secret put $Name
    if ($LASTEXITCODE -ne 0) {
        throw "Nao foi possivel gravar o segredo $Name."
    }
}

$root = Split-Path -Parent $PSScriptRoot
$gateway = Join-Path $root "cloudflare\whatsapp-gateway"
$localConfigPath = Join-Path $root "info\whatsapp_bridge.json"
$resultPath = Join-Path $root "info\whatsapp_meta_finalize_result.json"
$appId = "1463484892476388"
$wabaId = "2024687345078440"
$phoneNumberId = "1195146387020965"
$graphVersion = "v25.0"
$callbackUrl = "https://jk-whatsapp-gateway.jk-sistema.workers.dev/webhooks/whatsapp"
$appSecret = $null
$systemToken = $null
$verifyToken = $null
$stage = "inicializacao"

try {
    if (-not (Test-Path -LiteralPath $localConfigPath)) {
        throw "Configuracao local do WhatsApp nao encontrada."
    }
    $local = Get-Content -LiteralPath $localConfigPath -Raw | ConvertFrom-Json
    if (-not [string]$local.bridge_token) {
        throw "Bridge Token local nao configurado."
    }

    Write-Host "Finalizacao segura WhatsApp - JK Sistema" -ForegroundColor Cyan
    Write-Host "Os valores digitados nao serao exibidos nem gravados em arquivo local."
    Write-Host ""
    $stage = "leitura das credenciais"
    $appSecret = Read-SecretText "Cole o Meta App Secret e pressione Enter"
    $systemToken = Read-SecretText "Cole o System User Token e pressione Enter"
    if (-not $appSecret -or -not $systemToken) {
        throw "App Secret e System User Token sao obrigatorios."
    }

    # Validate the App Secret before changing the Worker. Sending it in the
    # POST body keeps it out of command lines, URLs and local logs.
    $stage = "validacao do Meta App Secret"
    Write-Host "Validando o Meta App Secret..."
    $appTokenResponse = Invoke-RestMethod -Method Post -Uri "https://graph.facebook.com/$graphVersion/oauth/access_token" -ContentType "application/x-www-form-urlencoded" -Body @{
        client_id = $appId
        client_secret = $appSecret
        grant_type = "client_credentials"
    } -TimeoutSec 60
    if (-not [string]$appTokenResponse.access_token) {
        throw "Meta App Secret invalido para o aplicativo informado."
    }

    $stage = "validacao do System User Token"
    Write-Host "Validando o System User Token e o acesso a WABA..."
    $metaHeaders = @{
        Authorization = "Bearer $systemToken"
        "Content-Type" = "application/json"
    }
    try {
        $wabaCheck = Invoke-RestMethod -Method Get -Uri "https://graph.facebook.com/$graphVersion/$wabaId`?fields=id,name" -Headers $metaHeaders -TimeoutSec 60
        if ([string]$wabaCheck.id -ne $wabaId) {
            throw "O token nao retornou a WABA esperada."
        }
    }
    catch {
        $detail = if ($_.ErrorDetails.Message) { [string]$_.ErrorDetails.Message } else { [string]$_.Exception.Message }
        throw "System User Token rejeitado pela Meta ou sem acesso a conta Black Jhon: $($detail.Substring(0, [Math]::Min(600, $detail.Length)))"
    }
    $verifyToken = New-RandomToken 24

    $stage = "gravacao dos segredos na Cloudflare"
    Write-Host "Gravando os segredos na Cloudflare..."
    Push-Location $gateway
    try {
        Set-WranglerSecret "META_APP_SECRET" $appSecret
        Set-WranglerSecret "META_ACCESS_TOKEN" $systemToken
        Set-WranglerSecret "META_SYSTEM_USER_TOKEN" $systemToken
        Set-WranglerSecret "META_VERIFY_TOKEN" $verifyToken
        Set-WranglerSecret "META_WABA_ID" $wabaId
        Set-WranglerSecret "META_PHONE_NUMBER_ID" $phoneNumberId
    }
    finally {
        Pop-Location
    }

    $stage = "propagacao e verificacao do webhook"
    Write-Host "Aguardando a propagacao do Worker e validando o webhook..."
    $webhookVerified = $false
    for ($attempt = 1; $attempt -le 15; $attempt++) {
        $challenge = New-RandomToken 8
        $verifyUri = "$callbackUrl`?hub.mode=subscribe&hub.verify_token=$verifyToken&hub.challenge=$challenge"
        try {
            $verifyResponse = Invoke-WebRequest -UseBasicParsing -Method Get -Uri $verifyUri -TimeoutSec 30
            if ([int]$verifyResponse.StatusCode -eq 200 -and [string]$verifyResponse.Content -eq $challenge) {
                $webhookVerified = $true
                break
            }
        }
        catch {
            if ($attempt -ge 15) { break }
        }
        Start-Sleep -Seconds 4
    }
    if (-not $webhookVerified) {
        throw "Worker nao confirmou o token de verificacao do webhook."
    }

    $stage = "assinatura do campo messages e da WABA"
    Write-Host "Assinando o campo messages e a WABA Black Jhon..."
    $bridgeHeaders = @{ Authorization = "Bearer $([string]$local.bridge_token)" }
    $metaFinalize = Invoke-RestMethod -Method Post -Uri "$([string]$local.worker_url)/bridge/meta/finalize" -Headers $bridgeHeaders -ContentType "application/json" -Body '{}' -TimeoutSec 120
    if (-not $metaFinalize.success) {
        throw "O Worker nao conseguiu finalizar a assinatura do webhook."
    }
    $subscriptions = $metaFinalize.subscriptions

    $stage = "teste de saude do gateway"
    $health = Invoke-RestMethod -Method Get -Uri "$([string]$local.worker_url)/bridge/status" -Headers $bridgeHeaders -TimeoutSec 30
    if (-not $health.success -or -not $health.meta.configured) {
        throw "Worker publicado, mas a configuracao Meta ainda nao ficou pronta."
    }

    $stage = "sincronizacao dos templates"
    $templateResult = $null
    $templateError = ""
    try {
        $templateResult = Invoke-RestMethod -Method Post -Uri "$([string]$local.worker_url)/bridge/templates/sync" -Headers $bridgeHeaders -ContentType "application/json" -Body '{"create_missing":true}' -TimeoutSec 90
    }
    catch {
        $templateError = $_.Exception.Message
    }

    $result = @{
        success = $true
        completed_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        app_id = $appId
        waba_id = $wabaId
        phone_number_id = $phoneNumberId
        graph_version = $graphVersion
        callback_url = $callbackUrl
        webhook_verified = $true
        meta_configured = $true
        subscriptions_found = @($subscriptions.data).Count
        templates_synced = if ($templateResult) { $templateResult.synced } else { 0 }
        template_error = $templateError
    }
    $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $resultPath -Encoding UTF8
    Write-Host ""
    Write-Host "META CONFIGURADA COM SUCESSO." -ForegroundColor Green
    Write-Host "Webhook: $callbackUrl"
    Write-Host "Volte ao JK e clique em Testar tudo."
}
catch {
    $safeMessage = [string]$_.Exception.Message
    $result = @{
        success = $false
        completed_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        stage = $stage
        error = $safeMessage
    }
    $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $resultPath -Encoding UTF8
    Write-Host ""
    Write-Host "NAO FOI POSSIVEL CONCLUIR:" -ForegroundColor Red
    Write-Host "Etapa: $stage" -ForegroundColor Red
    Write-Host $safeMessage -ForegroundColor Red
}
finally {
    $appSecret = $null
    $systemToken = $null
    $verifyToken = $null
    Write-Host ""
    Read-Host "Pressione Enter para fechar esta janela"
}
