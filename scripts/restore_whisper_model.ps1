param(
    [string]$Destination = $env:JK_WHISPER_MODEL_SOURCE
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Split-Path -Parent $scriptDir
$lockPath = Join-Path $rootDir "installer-runtime.lock.json"
$lock = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json
if ([int]$lock.schema_version -ne 1 -or -not $lock.whisper) { throw "Contrato Whisper invalido." }
if (-not $Destination) { $Destination = Join-Path $rootDir ".installer_runtime\whisper-source" }
$destinationRoot = [IO.Path]::GetFullPath($Destination)
$revision = [string]$lock.whisper.revision

function Write-Base64File([string]$Relative, [string]$Base64) {
    $target = Join-Path $destinationRoot $Relative
    $parent = Split-Path -Parent $target
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    [IO.File]::WriteAllBytes($target, [Convert]::FromBase64String($Base64))
}

function Assert-WhisperFile([object]$Entry) {
    $relative = [string]$Entry.path
    if (-not $relative -or [IO.Path]::IsPathRooted($relative) -or $relative.Contains('..')) {
        throw "Caminho Whisper invalido: $relative"
    }
    $candidate = [IO.Path]::GetFullPath((Join-Path $destinationRoot $relative))
    $prefix = $destinationRoot.TrimEnd('\') + '\'
    if (-not $candidate.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Caminho Whisper fora do destino: $relative"
    }
    $item = Get-Item -LiteralPath $candidate -ErrorAction Stop
    if ($item.Length -ne [int64]$Entry.size) { throw "Tamanho Whisper divergente: $relative" }
    $actual = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne ([string]$Entry.sha256).ToLowerInvariant()) { throw "SHA256 Whisper divergente: $relative" }
}

New-Item -ItemType Directory -Force -Path $destinationRoot | Out-Null
Write-Base64File "CACHEDIR.TAG" "U2lnbmF0dXJlOiA4YTQ3N2Y1OTdkMjhkMTcyNzg5ZjA2ODg2ODA2YmM1NQ0KIyBUaGlzIGZpbGUgaXMgYSBjYWNoZSBkaXJlY3RvcnkgdGFnIGNyZWF0ZWQgYnkgaHVnZ2luZ2ZhY2VfaHViLg0KIyBGb3IgaW5mb3JtYXRpb24gYWJvdXQgY2FjaGUgZGlyZWN0b3J5IHRhZ3MsIHNlZToNCiMJaHR0cHM6Ly9iZm9yZC5pbmZvL2NhY2hlZGlyLw0K"
Write-Base64File "models--Systran--faster-whisper-small\refs\main" "NTM2YjA2NjI3NDJjMDIzNDdiYzBlOTgwYTAxMDQxZjMzM2JjZTEyMA=="
Write-Base64File "models--Systran--faster-whisper-small\trees\$revision.json" "ew0KICJmb3JtYXRfdmVyc2lvbiI6IDEsDQogImZpbGVzIjogew0KICAiLmdpdGF0dHJpYnV0ZXMiOiB7DQogICAic2l6ZSI6IDE0NzcsDQogICAiYmxvYl9pZCI6ICJjN2Q5ZjMzMzJhOTUwMzU1ZDVhNzdkODUwMDBmMDVlNmY0NTQzNWVhIg0KICB9LA0KICAiUkVBRE1FLm1kIjogew0KICAgInNpemUiOiAxOTk4LA0KICAgImJsb2JfaWQiOiAiMTY1MTFmNjEwNmVhZDJmYmMzYTE0ODA5NTdhMjE3YzI2NjFhNWE5OCINCiAgfSwNCiAgImNvbmZpZy5qc29uIjogew0KICAgInNpemUiOiAyMzcwLA0KICAgImJsb2JfaWQiOiAiZTUwNDc1MzcwNTliZDhmMTgyZDljYTY0YzQ3MDIwMTU4NTAxNTE4NyINCiAgfSwNCiAgIm1vZGVsLmJpbiI6IHsNCiAgICJzaXplIjogNDgzNTQ2OTAyLA0KICAgImJsb2JfaWQiOiAiNTA0YTdmZTAyM2YxOTQ2MDU2NGE2MjM4ODdhOWY3N2VhNTNhYzcwOCIsDQogICAibGZzX3NoYTI1NiI6ICIzZTMwNTkyMTUwNmQ4ODcyODE2MDIzZTRjMjczZTc1ZDI0MTlmYjg5YjI0ZGE5N2I0ZmU3YmNlMTQxNzBkNjcxIiwNCiAgICJsZnNfc2l6ZSI6IDQ4MzU0NjkwMiwNCiAgICJ4ZXRfaGFzaCI6ICI0MjlmZmM4NDQ5MmMxMTIyZGEwMDVkNzI3NGZjMzk3YmYxNDJmZTEyMDY2ZWI3ZTQ2MTJkYzI1YzU3OWRiMmVhIg0KICB9LA0KICAidG9rZW5pemVyLmpzb24iOiB7DQogICAic2l6ZSI6IDIyMDMyMzksDQogICAiYmxvYl9pZCI6ICI3ODE4YWRiNmRlOWZhMzA2NGQzZmY4MTIyNmZkZDY3NWJlMWY2MzQ0Ig0KICB9LA0KICAidm9jYWJ1bGFyeS50eHQiOiB7DQogICAic2l6ZSI6IDQ1OTg2MSwNCiAgICJibG9iX2lkIjogImM5MDc0NjQ0ZDlkMTIwNTY4NmYxNmQ0MTE1NjQ3Mjk0NjEzMjRiNzUiDQogIH0NCiB9DQp9"
Write-Base64File "model-manifest.json" "ew0KICAibW9kZWwiOiAic21hbGwiLA0KICAiZW5naW5lIjogImZhc3Rlci13aGlzcGVyPT0xLjIuMSIsDQogICJkb3dubG9hZGVkX2F0IjogIjIwMjYtMDctMTBUMTg6Mzc6MzRaIiwNCiAgInRvdGFsX2J5dGVzIjogNDg2MjEzNDc0LA0KICAiZmlsZXMiOiBbDQogICAgew0KICAgICAgInBhdGgiOiAiQ0FDSEVESVIuVEFHIiwNCiAgICAgICJzaXplIjogMTk1LA0KICAgICAgInNoYTI1NiI6ICJlODg5M2UyZGNiM2Y0NTQ1ZWY2N2EwYTViN2I3NjkxMTNkMzhlZWYxYWE2MWM3YzdiYTE0NjU5YjAyYWY5NTVmIg0KICAgIH0sDQogICAgew0KICAgICAgInBhdGgiOiAibW9kZWxzLS1TeXN0cmFuLS1mYXN0ZXItd2hpc3Blci1zbWFsbC9yZWZzL21haW4iLA0KICAgICAgInNpemUiOiA0MCwNCiAgICAgICJzaGEyNTYiOiAiOGQ1ZDA2NmFjMzcwZmJmMmIyOTIxOTI5OTQ5MDg2ZjEyZTg0NjZkYWNiNTI2OWMyZDRiZWY5MmRlNDg4NDRjOSINCiAgICB9LA0KICAgIHsNCiAgICAgICJwYXRoIjogIm1vZGVscy0tU3lzdHJhbi0tZmFzdGVyLXdoaXNwZXItc21hbGwvc25hcHNob3RzLzUzNmIwNjYyNzQyYzAyMzQ3YmMwZTk4MGEwMTA0MWYzMzNiY2UxMjAvY29uZmlnLmpzb24iLA0KICAgICAgInNpemUiOiAyMzcwLA0KICAgICAgInNoYTI1NiI6ICJiNTU0OTZhYzc5NDBhN2FlNDdkMmMwMWVhYjQwZWRmZDg3MDFmZWVjMTIyOWQ5Y2NlM2I0MDAxNDM4M2ZiODI4Ig0KICAgIH0sDQogICAgew0KICAgICAgInBhdGgiOiAibW9kZWxzLS1TeXN0cmFuLS1mYXN0ZXItd2hpc3Blci1zbWFsbC9zbmFwc2hvdHMvNTM2YjA2NjI3NDJjMDIzNDdiYzBlOTgwYTAxMDQxZjMzM2JjZTEyMC9tb2RlbC5iaW4iLA0KICAgICAgInNpemUiOiA0ODM1NDY5MDIsDQogICAgICAic2hhMjU2IjogIjNlMzA1OTIxNTA2ZDg4NzI4MTYwMjNlNGMyNzNlNzVkMjQxOWZiODliMjRkYTk3YjRmZTdiY2UxNDE3MGQ2NzEiDQogICAgfSwNCiAgICB7DQogICAgICAicGF0aCI6ICJtb2RlbHMtLVN5c3RyYW4tLWZhc3Rlci13aGlzcGVyLXNtYWxsL3NuYXBzaG90cy81MzZiMDY2Mjc0MmMwMjM0N2JjMGU5ODBhMDEwNDFmMzMzYmNlMTIwL3Rva2VuaXplci5qc29uIiwNCiAgICAgICJzaXplIjogMjIwMzIzOSwNCiAgICAgICJzaGEyNTYiOiAiZmI3YjYzMTkxZTliYjA0NTA4MmM3OWZkNzQyYTMxMDZhMTJjOTk1MTNhYjMwZGY0YTBkNDdmYTZjYjZmZDBhYiINCiAgICB9LA0KICAgIHsNCiAgICAgICJwYXRoIjogIm1vZGVscy0tU3lzdHJhbi0tZmFzdGVyLXdoaXNwZXItc21hbGwvc25hcHNob3RzLzUzNmIwNjYyNzQyYzAyMzQ3YmMwZTk4MGEwMTA0MWYzMzNiY2UxMjAvdm9jYWJ1bGFyeS50eHQiLA0KICAgICAgInNpemUiOiA0NTk4NjEsDQogICAgICAic2hhMjU2IjogIjM0Y2UzZmUxYzUwNDEwMjdiM2Y4ZDQyOTEyMjcwOTkzZjk4NmRiYzRiYjM0Y2YyN2Y5NTFlMzRhMWU0NTM5MTMiDQogICAgfSwNCiAgICB7DQogICAgICAicGF0aCI6ICJtb2RlbHMtLVN5c3RyYW4tLWZhc3Rlci13aGlzcGVyLXNtYWxsL3RyZWVzLzUzNmIwNjYyNzQyYzAyMzQ3YmMwZTk4MGEwMTA0MWYzMzNiY2UxMjAuanNvbiIsDQogICAgICAic2l6ZSI6IDg2NywNCiAgICAgICJzaGEyNTYiOiAiMmMyYTBlYWI5ZDlmNWNiNWI0NTg1NTU1ZjJkZmQyNGU0MGVhNGQ2NTAzNTYwYjk5ZDI2MjRjNTY5YmUzOWI1MyINCiAgICB9DQogIF0NCn0="

$downloads = @(
    @{ Relative = "models--Systran--faster-whisper-small/snapshots/$revision/config.json"; Remote = "config.json" },
    @{ Relative = "models--Systran--faster-whisper-small/snapshots/$revision/model.bin"; Remote = "model.bin" },
    @{ Relative = "models--Systran--faster-whisper-small/snapshots/$revision/tokenizer.json"; Remote = "tokenizer.json" },
    @{ Relative = "models--Systran--faster-whisper-small/snapshots/$revision/vocabulary.txt"; Remote = "vocabulary.txt" }
)
foreach ($download in $downloads) {
    $target = Join-Path $destinationRoot $download.Relative
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
    $expected = @($lock.whisper.files | Where-Object { [string]$_.path -eq [string]$download.Relative })[0]
    $valid = $false
    if (Test-Path -LiteralPath $target -PathType Leaf) {
        try { Assert-WhisperFile $expected; $valid = $true } catch { $valid = $false }
    }
    if (-not $valid) {
        $url = "https://huggingface.co/Systran/faster-whisper-small/resolve/$revision/$($download.Remote)?download=true"
        Invoke-WebRequest -Uri $url -OutFile $target -UseBasicParsing
    }
}
foreach ($entry in @($lock.whisper.files)) { Assert-WhisperFile $entry }
$manifestHash = (Get-FileHash -LiteralPath (Join-Path $destinationRoot "model-manifest.json") -Algorithm SHA256).Hash.ToLowerInvariant()
if ($manifestHash -ne ([string]$lock.whisper.manifest_sha256).ToLowerInvariant()) {
    throw "Manifesto Whisper restaurado diverge do contrato."
}
Write-Host "Whisper restaurado e verificado em $destinationRoot"
