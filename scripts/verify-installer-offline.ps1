param(
    [Parameter(Mandatory = $true)][string]$PackageRoot,
    [string]$BuildRoot
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$package = [System.IO.Path]::GetFullPath($PackageRoot)
$build = if ($BuildRoot) { [System.IO.Path]::GetFullPath($BuildRoot) } else { Split-Path -Parent $package }
$scratchRoot = Join-Path $build ".installer_runtime\offline-smoke"
$scratch = [System.IO.Path]::GetFullPath($scratchRoot)
$allowed = [System.IO.Path]::GetFullPath((Join-Path $build ".installer_runtime")).TrimEnd('\') + '\'
if (-not $scratch.StartsWith($allowed, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Diretorio temporario fora da area permitida."
}

$requirements = Join-Path $package "requirements.txt"
$wheelDir = Join-Path $package "python_wheels"
$pythonInstaller = Get-ChildItem -LiteralPath (Join-Path $package "python_runtime") -Filter "python-*.exe" -File | Select-Object -First 1
$modelDir = Join-Path $package "black_jhon_runtime\faster-whisper-small"
if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) { throw "requirements.txt ausente no pacote." }
if (-not $pythonInstaller) { throw "Instalador Python ausente no pacote." }
if (-not (Test-Path -LiteralPath $modelDir -PathType Container)) { throw "Whisper empacotado ausente." }

if (Test-Path -LiteralPath $scratch) { Remove-Item -LiteralPath $scratch -Recurse -Force }
New-Item -ItemType Directory -Force -Path $scratch | Out-Null
$pythonHome = Join-Path $scratch "python311"
$pythonRegistryKey = "HKCU:\Software\Python\PythonCore\3.11"
$pythonRegistryNative = "HKCU\Software\Python\PythonCore\3.11"
$pythonRegistryBackup = Join-Path $scratch "python311-registry-backup.reg"
$restorePythonRegistry = $false
$removeSmokeRegistry = $false

function Get-RegisteredPythonExecutable {
    $installPathKey = Join-Path $pythonRegistryKey "InstallPath"
    if (-not (Test-Path -LiteralPath $installPathKey)) { return $null }
    return (Get-ItemProperty -LiteralPath $installPathKey -ErrorAction SilentlyContinue).ExecutablePath
}

function Remove-SmokePythonRegistration {
    if (-not (Test-Path -LiteralPath $pythonRegistryKey)) { return }
    $registered = Get-RegisteredPythonExecutable
    if (-not $registered) { return }
    $registeredFull = [System.IO.Path]::GetFullPath($registered)
    $pythonHomeFull = [System.IO.Path]::GetFullPath($pythonHome).TrimEnd('\') + '\'
    if ($registeredFull.StartsWith($pythonHomeFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $pythonRegistryKey -Recurse -Force
    }
}

function Expand-RegisteredPythonPayload {
    param([Parameter(Mandatory = $true)][string]$Destination)

    $components = @(
        @{ Name = "Python 3.11.9 Core Interpreter (64-bit)"; File = "core.msi" },
        @{ Name = "Python 3.11.9 Standard Library (64-bit)"; File = "lib.msi" },
        @{ Name = "Python 3.11.9 Executables (64-bit)"; File = "exe.msi" },
        @{ Name = "Python 3.11.9 Development Libraries (64-bit)"; File = "dev.msi" }
    )
    $uninstallRoots = @(
        "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall",
        "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall"
    )
    foreach ($component in $components) {
        $entry = $null
        foreach ($root in $uninstallRoots) {
            if (-not (Test-Path -LiteralPath $root)) { continue }
            $entry = Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue |
                ForEach-Object { Get-ItemProperty -LiteralPath $_.PSPath -ErrorAction SilentlyContinue } |
                Where-Object { $_.DisplayName -eq $component.Name } |
                Select-Object -First 1
            if ($entry) { break }
        }
        if (-not $entry -or -not $entry.InstallSource) { return $false }
        $msi = Join-Path $entry.InstallSource $component.File
        if (-not (Test-Path -LiteralPath $msi -PathType Leaf)) { return $false }
        $extract = Start-Process -FilePath "msiexec.exe" -ArgumentList @(
            "/a",
            "`"$msi`"",
            "TARGETDIR=`"$Destination`"",
            "/qn"
        ) -Wait -PassThru -WindowStyle Hidden
        if ($extract.ExitCode -ne 0) { return $false }
    }
    return $true
}

$python = $null
$registeredPython = Get-RegisteredPythonExecutable
if ($registeredPython -and (Test-Path -LiteralPath $registeredPython -PathType Leaf)) {
    $registeredVersion = (& $registeredPython --version 2>&1) -join " "
    if ($LASTEXITCODE -eq 0 -and $registeredVersion -eq "Python 3.11.9") {
        $python = $registeredPython
        Write-Host "[offline-runtime] Reutilizando Python 3.11.9 ja instalado para o ambiente isolado."
    } else {
        throw "O Python 3.11 registrado nao e a versao 3.11.9 exigida: $registeredVersion"
    }
} elseif (Test-Path -LiteralPath $pythonRegistryKey) {
    & reg.exe export $pythonRegistryNative $pythonRegistryBackup /y | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Falha ao preservar o registro existente do Python 3.11." }
    Remove-Item -LiteralPath $pythonRegistryKey -Recurse -Force
    $restorePythonRegistry = $true
}

$installArgs = @(
    "/quiet",
    "InstallAllUsers=0",
    "TargetDir=`"$pythonHome`"",
    "Include_pip=1",
    "Include_launcher=0",
    "AssociateFiles=0",
    "Shortcuts=0",
    "Include_test=0",
    "PrependPath=0"
)
try {
    if (-not $python) {
        $install = Start-Process -FilePath $pythonInstaller.FullName -ArgumentList $installArgs -Wait -PassThru -WindowStyle Hidden
        if ($install.ExitCode -ne 0) { throw "Instalacao isolada do Python falhou: $($install.ExitCode)" }
        $python = Join-Path $pythonHome "python.exe"
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
            Write-Host "[offline-runtime] Registro MSI inconsistente detectado; extraindo componentes 3.11.9 sem alterar a instalacao."
            if (-not (Expand-RegisteredPythonPayload -Destination $pythonHome)) {
                throw "Python isolado nao foi criado e a contingencia MSI nao esta disponivel."
            }
        }
        $isolatedVersion = (& $python --version 2>&1) -join " "
        if ($LASTEXITCODE -ne 0 -or $isolatedVersion -ne "Python 3.11.9") {
            throw "Python isolado invalido: $isolatedVersion"
        }
        $removeSmokeRegistry = $true
    }

    $venv = Join-Path $scratch "venv"
    & $python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Criacao da venv isolada falhou." }
    $venvPython = Join-Path $venv "Scripts\python.exe"
    & $venvPython -m pip install --disable-pip-version-check --no-index --find-links $wheelDir -r $requirements
    if ($LASTEXITCODE -ne 0) { throw "Instalacao offline da wheelhouse falhou." }
    & $venvPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw "pip check falhou na wheelhouse offline." }

    $smokeScript = Join-Path $scratch "smoke.py"
    @'
import json
import subprocess
import sys
import wave
from pathlib import Path

import ctranslate2
import faster_whisper
import onnxruntime
import openai_codex
from codex_cli_bin import bundled_codex_path
from faster_whisper import WhisperModel

model_root = Path(sys.argv[1]).resolve()
manifest = json.loads((model_root / "model-manifest.json").read_text(encoding="utf-8-sig"))
if not manifest.get("files"):
    raise RuntimeError("whisper_manifest_empty")
snapshots = list(model_root.glob("models--Systran--faster-whisper-small/snapshots/*"))
if len(snapshots) != 1:
    raise RuntimeError("whisper_snapshot_invalid")
codex = Path(bundled_codex_path()).resolve()
completed = subprocess.run([str(codex), "--version"], capture_output=True, text=True, timeout=20)
if completed.returncode != 0 or "codex-cli" not in (completed.stdout + completed.stderr):
    raise RuntimeError("codex_cli_invalid")
audio = model_root.parent / "offline-smoke.wav"
with wave.open(str(audio), "wb") as handle:
    handle.setnchannels(1)
    handle.setsampwidth(2)
    handle.setframerate(16000)
    handle.writeframes(b"\x00\x00" * 16000)
model = WhisperModel(str(snapshots[0]), device="cpu", compute_type="int8")
segments, _info = model.transcribe(str(audio), language="pt", beam_size=1)
list(segments)
print("offline-runtime-smoke: ok")
'@ | Set-Content -LiteralPath $smokeScript -Encoding UTF8

    & $venvPython $smokeScript $modelDir
    if ($LASTEXITCODE -ne 0) { throw "Smoke test nativo Codex/Whisper falhou." }
    Write-Host "[offline-runtime] OK - Python, wheels, Codex CLI, ONNX, CTranslate2 e Whisper validados sem rede."
} finally {
    if ($removeSmokeRegistry -or $restorePythonRegistry) {
        Remove-SmokePythonRegistration
    }
    if ($restorePythonRegistry) {
        & reg.exe import $pythonRegistryBackup | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Falha ao restaurar o registro anterior do Python 3.11." }
    }
}
